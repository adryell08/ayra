"""Microfone e alto-falante (sounddevice/PortAudio), sem gravar nada em disco.

`Recorder.record` capta a pergunta até o usuário parar, ou sozinho após silêncio (VAD por energia).
`Player.play` toca PCM e expõe o envelope de volume para a boca da Ayra acompanhar a fala.
Erros de dispositivo viram `VoiceError` com mensagem em pt-BR (RNF02).
"""
from __future__ import annotations

import io
import os
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
    Devolve "speech" | "manual" | "silence" (nada dito). Com `hub` (audio_duplex.AudioHub) o microfone é
    compartilhado com o operador e chega com o eco do alto-falante já cancelado.
    `listen(..., wait_for_speech=True)` é a conversa contínua: espera a pessoa começar a falar (sem prazo),
    avisa em `on_speech_start` (para interromper a IA) e envia junto os 0,4 s anteriores (sem cortar a 1ª sílaba).
    """

    RATE = 24000

    def __init__(self, max_seconds: float = 30.0, silence_after: Optional[float] = None, no_speech_timeout: float = 7.0,
                 hub=None):
        # Fim de fala: 1,5 s de silêncio (pausa para pensar não fecha a mensagem). NURIA_FIM_FALA ajusta (0,6–4 s).
        if silence_after is None:
            try:
                silence_after = float(os.environ.get("NURIA_FIM_FALA", "1.5").replace(",", "."))
            except ValueError:
                silence_after = 1.5
            silence_after = min(4.0, max(0.6, silence_after))
        self.max_seconds, self.silence_after, self.no_speech_timeout = max_seconds, silence_after, no_speech_timeout
        self.level = 0.0
        self.capture_rate = self.RATE
        self.hub = hub

    def run(self, stop: threading.Event, on_chunk: Callable[[np.ndarray], None]) -> str:
        return self.listen(stop, on_chunk)

    def listen(self, stop: threading.Event, on_chunk: Callable[[np.ndarray], None],
               on_speech_start: Optional[Callable[[], None]] = None, wait_for_speech: bool = False) -> str:
        from realtime import resample
        state = dict(floor=None, speech=False, last_voice=0.0, start=time.perf_counter(), warm=[], peak=0.0,
                     recent=[], voiced=0.0, started_at=None)
        preroll: deque = deque()
        lock = threading.Lock()

        def handle(pcm: np.ndarray, rms: float, dur: float) -> None:
            now = time.perf_counter()
            with lock:
                if wait_for_speech and state["started_at"] is None:
                    preroll.append(pcm)
                    while sum(len(c) for c in preroll) > int(self.RATE * 0.4):
                        preroll.popleft()
                else:
                    on_chunk(pcm)
                if now - state["start"] < 0.3:
                    state["warm"].append(rms)
                    return
                if state["floor"] is None:
                    state["floor"] = float(np.median(state["warm"])) if state["warm"] else 0.005
                state["recent"] = (state["recent"] + [rms])[-100:]
                if len(state["recent"]) >= 20:
                    state["floor"] = min(state["floor"] * 1.01 + 1e-5, float(np.percentile(state["recent"], 20)))
                threshold = max(0.010, state["floor"] * 2.5)
                far = self.hub is not None and self.hub.far_active()
                if far:
                    threshold *= 2.0                       # robô falando: só voz clara vale (resto de eco não)
                self.level = min(1.0, rms / max(threshold * 4, 1e-4))
                if rms > threshold:
                    state["peak"] = max(rms, state["peak"] * 0.995)
                voiced = rms > max(threshold, 0.22 * state["peak"])
                state["voiced"] = state["voiced"] + dur if voiced else 0.0
                need = (0.25 if far else 0.12) if wait_for_speech else 0.0
                if voiced and state["voiced"] >= need:
                    if not state["speech"]:
                        state["speech"] = True
                        if wait_for_speech:
                            state["started_at"] = now
                            if on_speech_start:
                                on_speech_start()
                            for c in preroll:
                                on_chunk(c)
                            preroll.clear()
                    state["last_voice"] = now

        sid = stream = None
        if self.hub is not None:
            try:
                sid = self.hub.subscribe(self.RATE, lambda pcm: handle(
                    pcm, float(np.sqrt(np.mean((pcm.astype(np.float32) / 32768.0) ** 2))) if len(pcm) else 0.0,
                    len(pcm) / self.RATE))
                self.capture_rate = self.RATE
            except Exception:
                sid = None
        if sid is None:
            sd = _sd()

            def callback(indata, frames, t, status):
                block = indata[:, 0].copy()
                handle(resample(block, self.capture_rate, self.RATE),
                       float(np.sqrt(np.mean((block.astype(np.float32) / 32768.0) ** 2))), frames / self.capture_rate)
            last = None
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
        import contextlib
        reason = "silence"
        try:
            with (stream if stream is not None else contextlib.nullcontext()):
                while True:
                    if stop.wait(0.02):
                        reason = "manual"
                        break
                    now = time.perf_counter()
                    began = state["started_at"] or state["start"]
                    if (not wait_for_speech or state["speech"]) and now - began > self.max_seconds:
                        reason = "speech" if state["speech"] else "silence"
                        break
                    if state["speech"] and now - state["last_voice"] > self.silence_after:
                        reason = "speech"
                        break
                    if not wait_for_speech and not state["speech"] and now - state["start"] > self.no_speech_timeout:
                        reason = "silence"
                        break
        finally:
            if sid is not None:
                self.hub.unsubscribe(sid)
        self.level = 0.0
        if reason == "manual" and not state["speech"] and time.perf_counter() - state["start"] < 0.6:
            return "silence"
        if reason == "manual" and wait_for_speech and not state["speech"]:
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
        self.on_played: Optional[Callable[[np.ndarray], None]] = None   # referência do eco (audio_duplex)
        self.base_prebuffer = int(self.RATE * 0.12)  # buffer de jitter: só começa a tocar com 120 ms na fila…
        self.prebuffer = self.base_prebuffer         # …e aumenta sozinho (até 400 ms) se ainda faltar áudio
        self.max_prebuffer = int(self.RATE * 0.40)
        self.max_wait = 0.25                        # …ou depois de 250 ms (respostas curtíssimas)
        self._last_underrun = 0.0
        self._primed = False
        self._wait_since = 0.0
        self.underruns = 0

    def _callback(self, outdata, frames, t, status):
        out = np.zeros(frames, np.int16)
        filled = 0
        with self._lock:
            if not self._primed:
                queued = len(self._cur) + sum(len(c) for c in self._q)
                if queued == 0:
                    self._wait_since = 0.0
                else:
                    now = time.perf_counter()
                    self._wait_since = self._wait_since or now
                    if queued >= self.prebuffer or now - self._wait_since >= self.max_wait:
                        self._primed = True
            while self._primed and filled < frames:
                if len(self._cur) == 0:
                    if not self._q:
                        break
                    self._cur = self._q.popleft()
                take = min(frames - filled, len(self._cur))
                out[filled:filled + take] = self._cur[:take]
                self._cur = self._cur[take:]
                filled += take
            if self._primed and filled < frames:        # acabou a fila no meio do fluxo: junta de novo antes de tocar
                self._primed, self._wait_since = False, 0.0
                self.underruns += 1
                self._last_underrun = time.perf_counter()
                self.prebuffer = min(self.max_prebuffer, self.prebuffer + int(self.RATE * 0.04))   # +40 ms de folga
            elif (self.prebuffer > self.base_prebuffer and time.perf_counter() - self._last_underrun > 20.0):
                self.prebuffer -= int(self.RATE * 0.02)                 # estável há 20 s: reduz a folga (menos atraso)
                self._last_underrun = time.perf_counter() - 15.0
            self._played += filled
        rms = float(np.sqrt(np.mean((out.astype(np.float32) / 32768.0) ** 2))) if frames else 0.0
        self._level = 0.6 * self._level + 0.4 * min(1.0, rms / 0.12)
        outdata[:, 0] = out
        if self.on_played is not None:
            try:
                self.on_played(out)
            except Exception:
                pass

    def start(self) -> None:
        if self._stream is not None:
            return
        sd = _sd()
        try:
            # blocos de 40 ms e latência "alta": a thread de áudio precisa do GIL; com a interface a 60 quadros/s
            # blocos de 20 ms atrasavam e o som picotava.
            self._stream = sd.OutputStream(samplerate=self.RATE, channels=1, dtype="int16", latency="high",
                                           blocksize=int(self.RATE * 0.04), callback=self._callback)
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
            self._primed, self._wait_since = False, 0.0

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
