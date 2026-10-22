"""Microfone e alto-falante (sounddevice/PortAudio), sem gravar nada em disco.

`Recorder.record` capta a pergunta até o usuário parar, ou sozinho após silêncio (VAD por energia).
`Player.play` toca PCM e expõe o envelope de volume para a boca da Nuria acompanhar a fala.
Erros de dispositivo viram `VoiceError` com mensagem em pt-BR (RNF02).
"""
from __future__ import annotations

import io
from collections import deque
import threading
import time
import wave
from typing import Callable, Optional

import numpy as np


class VoiceError(RuntimeError):
    pass


def _sd():
    try:
        import sounddevice as sd
        return sd
    except (ImportError, OSError) as error:
        raise VoiceError("Áudio indisponível: instale 'sounddevice' (pip install -r requirements.txt).") from error


def to_wav(samples: np.ndarray, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.asarray(samples, dtype=np.int16).tobytes())
    return buf.getvalue()


def envelope(samples: np.ndarray, rate: int, step: float = 0.02) -> np.ndarray:
    """Volume 0..1 a cada `step` s (RMS normalizado), usado para a boca."""
    x = np.asarray(samples, dtype=np.float32) / 32768.0
    n = max(1, int(rate * step))
    frames = len(x) // n
    if frames == 0:
        return np.zeros(1, dtype=np.float32)
    rms = np.sqrt(np.mean(x[:frames * n].reshape(frames, n) ** 2, axis=1))
    peak = float(np.percentile(rms, 95)) or 1.0
    return np.clip(rms / peak, 0.0, 1.0).astype(np.float32)


class Recorder:
    RATE = 16000

    def __init__(self, max_seconds: float = 15.0, silence_after: float = 1.2, no_speech_timeout: float = 7.0):
        self.max_seconds = max_seconds
        self.silence_after = silence_after
        self.no_speech_timeout = no_speech_timeout
        self.level = 0.0                    # 0..1 para o medidor da interface
        self.heard_speech = False

    def record(self, stop: threading.Event, on_level: Optional[Callable[[float], None]] = None) -> Optional[bytes]:
        """Bloqueia até terminar. Devolve WAV, ou None se não houve fala."""
        sd = _sd()
        chunks, lock = [], threading.Lock()
        state = dict(floor=None, speech=False, last_voice=0.0, start=time.perf_counter(), warm=[], peak=0.0, recent=[])

        def callback(indata, frames, t, status):
            block = indata[:, 0].copy()
            with lock:
                chunks.append(block)
            rms = float(np.sqrt(np.mean((block.astype(np.float32) / 32768.0) ** 2)))
            now = time.perf_counter()
            if now - state["start"] < 0.3:
                state["warm"].append(rms)
                return
            if state["floor"] is None:
                state["floor"] = float(np.median(state["warm"])) if state["warm"] else 0.005
            # piso de ruído adaptativo: 20º percentil dos últimos ~3 s (ventilador, sala)
            state["recent"] = (state["recent"] + [rms])[-100:]
            if len(state["recent"]) >= 20:
                state["floor"] = min(state["floor"] * 1.01 + 1e-5, float(np.percentile(state["recent"], 20)))
            threshold = max(0.010, state["floor"] * 2.5)
            self.level = min(1.0, rms / max(threshold * 4, 1e-4))
            if on_level:
                on_level(self.level)
            if rms > threshold:
                state["peak"] = max(rms, state["peak"] * 0.995)
            # fim da fala: abaixo do limiar OU bem abaixo do volume da própria voz
            if rms > max(threshold, 0.22 * state["peak"]):
                state["speech"], state["last_voice"] = True, now

        try:
            stream = sd.InputStream(samplerate=self.RATE, channels=1, dtype="int16", blocksize=int(self.RATE * 0.03),
                                    callback=callback)
        except Exception as error:
            raise VoiceError(f"Microfone indisponível: {error}") from error
        with stream:
            while not stop.is_set():
                time.sleep(0.03)
                now = time.perf_counter()
                elapsed = now - state["start"]
                if elapsed > self.max_seconds:
                    break
                if state["speech"] and now - state["last_voice"] > self.silence_after:
                    break
                if not state["speech"] and elapsed > self.no_speech_timeout:
                    break
        self.level = 0.0
        self.heard_speech = state["speech"]
        manual = stop.is_set() and time.perf_counter() - state["start"] > 0.6
        if not chunks or not (state["speech"] or manual):     # parada manual: deixa a transcrição decidir
            return None
        return to_wav(np.concatenate(chunks), self.RATE)


class Player:
    def __init__(self):
        self._env = np.zeros(1, dtype=np.float32)
        self._t0 = None
        self._step = 0.02

    def level(self) -> float:
        if self._t0 is None:
            return 0.0
        i = int((time.perf_counter() - self._t0) / self._step)
        return float(self._env[i]) if 0 <= i < len(self._env) else 0.0

    def play(self, samples: np.ndarray, rate: int, stop: threading.Event) -> None:
        sd = _sd()
        self._env = envelope(samples, rate, self._step)
        duration = len(samples) / float(rate)
        try:
            sd.play(np.asarray(samples, dtype=np.int16), rate)
        except Exception as error:
            raise VoiceError(f"Alto-falante indisponível: {error}") from error
        self._t0 = time.perf_counter()
        try:
            while time.perf_counter() - self._t0 < duration + 0.15:
                if stop.wait(0.03):
                    break
        finally:
            try:
                sd.stop()
            except Exception:
                pass
            self._t0 = None


# ---------------------------------------------------------------------------
# Tempo real (Realtime API): microfone em blocos e reprodução progressiva, ambos a 24 kHz
# ---------------------------------------------------------------------------
class MicStream:
    """Envia blocos de ~40 ms enquanto escuta; detecta o fim da fala localmente (mesmo VAD do Recorder).

    `run(stop, on_chunk)` bloqueia até: parada manual, silêncio após a fala, nada dito ou tempo máximo.
    Devolve "speech" | "manual" | "silence" (nada dito). Se o dispositivo não abrir a 24 kHz,
    captura na taxa padrão e converte (nunca declara uma taxa diferente da enviada).
    """

    RATE = 24000

    def __init__(self, max_seconds: float = 20.0, silence_after: float = 0.8, no_speech_timeout: float = 7.0):
        self.max_seconds, self.silence_after, self.no_speech_timeout = max_seconds, silence_after, no_speech_timeout
        self.level = 0.0
        self.capture_rate = self.RATE

    def run(self, stop: threading.Event, on_chunk: Callable[[np.ndarray], None]) -> str:
        sd = _sd()
        from realtime import resample
        state = dict(floor=None, speech=False, last_voice=0.0, start=time.perf_counter(), warm=[], peak=0.0, recent=[])

        def callback(indata, frames, t, status):
            block = indata[:, 0].copy()
            pcm = resample(block, self.capture_rate, self.RATE)
            on_chunk(pcm)
            rms = float(np.sqrt(np.mean((block.astype(np.float32) / 32768.0) ** 2)))
            now = time.perf_counter()
            if now - state["start"] < 0.3:
                state["warm"].append(rms)
                return
            if state["floor"] is None:
                state["floor"] = float(np.median(state["warm"])) if state["warm"] else 0.005
            state["recent"] = (state["recent"] + [rms])[-100:]
            if len(state["recent"]) >= 20:
                state["floor"] = min(state["floor"] * 1.01 + 1e-5, float(np.percentile(state["recent"], 20)))
            threshold = max(0.010, state["floor"] * 2.5)
            self.level = min(1.0, rms / max(threshold * 4, 1e-4))
            if rms > threshold:
                state["peak"] = max(rms, state["peak"] * 0.995)
            if rms > max(threshold, 0.22 * state["peak"]):
                state["speech"], state["last_voice"] = True, now

        stream = None
        for rate in (self.RATE, None):
            try:
                if rate is None:
                    rate = int(sd.query_devices(kind="input")["default_samplerate"])
                self.capture_rate = rate
                stream = sd.InputStream(samplerate=rate, channels=1, dtype="int16", blocksize=int(rate * 0.04),
                                        callback=callback)
                break
            except Exception as error:
                last = error
        if stream is None:
            raise VoiceError(f"Microfone indisponível: {last}")
        reason = "silence"
        with stream:
            while True:
                if stop.wait(0.02):
                    reason = "manual"
                    break
                now = time.perf_counter()
                elapsed = now - state["start"]
                if elapsed > self.max_seconds:
                    reason = "speech" if state["speech"] else "silence"
                    break
                if state["speech"] and now - state["last_voice"] > self.silence_after:
                    reason = "speech"
                    break
                if not state["speech"] and elapsed > self.no_speech_timeout:
                    reason = "silence"
                    break
        self.level = 0.0
        if reason == "manual" and not state["speech"] and time.perf_counter() - state["start"] < 0.6:
            return "silence"
        return reason


class StreamPlayer:
    """Toca blocos PCM conforme chegam. Conta o que foi de fato reproduzido (para cortar o histórico
    na interrupção) e mede o volume do bloco em reprodução (boca sincronizada com o som real)."""

    RATE = 24000

    def __init__(self):
        self._q: "deque[np.ndarray]" = deque()
        self._lock = threading.Lock()
        self._played = 0
        self._received = 0
        self._level = 0.0
        self._stream = None
        self._cur = np.zeros(0, np.int16)

    def _callback(self, outdata, frames, t, status):
        out = np.zeros(frames, np.int16)
        filled = 0
        with self._lock:
            while filled < frames:
                if len(self._cur) == 0:
                    if not self._q:
                        break
                    self._cur = self._q.popleft()
                take = min(frames - filled, len(self._cur))
                out[filled:filled + take] = self._cur[:take]
                self._cur = self._cur[take:]
                filled += take
            self._played += filled
        rms = float(np.sqrt(np.mean((out.astype(np.float32) / 32768.0) ** 2))) if frames else 0.0
        self._level = 0.6 * self._level + 0.4 * min(1.0, rms / 0.12)
        outdata[:, 0] = out

    def start(self) -> None:
        if self._stream is not None:
            return
        sd = _sd()
        try:
            self._stream = sd.OutputStream(samplerate=self.RATE, channels=1, dtype="int16",
                                           blocksize=int(self.RATE * 0.02), callback=self._callback)
            self._stream.start()
        except Exception as error:
            self._stream = None
            raise VoiceError(f"Alto-falante indisponível: {error}") from error

    def begin(self) -> None:
        """Nova resposta: zera contadores."""
        with self._lock:
            self._q.clear()
            self._cur = np.zeros(0, np.int16)
            self._played = self._received = 0

    def feed(self, pcm: np.ndarray) -> None:
        with self._lock:
            self._q.append(np.asarray(pcm, dtype=np.int16))
            self._received += len(pcm)

    def clear(self) -> int:
        """Descarta o pendente; devolve quantas amostras já tocaram nesta resposta."""
        with self._lock:
            self._q.clear()
            self._cur = np.zeros(0, np.int16)
            played = self._played
        self._level = 0.0
        return played

    def pending(self) -> int:
        with self._lock:
            return len(self._cur) + sum(len(c) for c in self._q)

    @property
    def played(self) -> int:
        return self._played

    @property
    def received(self) -> int:
        return self._received

    def level(self) -> float:
        return self._level if self.pending() or self._level > 0.02 else 0.0

    def close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
