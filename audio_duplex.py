"""Áudio full-duplex da Ayra: um microfone só, com cancelamento de eco, repartido entre IA e operador.

Sem isto, o microfone precisava ficar mudo enquanto o robô falava (senão a IA ouvia a si mesma e o operador
ouvia o próprio eco). Com o cancelamento de eco, as duas pontas falam ao mesmo tempo, como numa ligação.

- `EchoCanceller`: filtro adaptativo em blocos no domínio da frequência (PBFDAF/NLMS, 400 ms de eco), com
  detecção de fala dupla (não "desaprende" quando a pessoa fala junto) e supressão do eco residual.
- `StreamResampler`: troca de taxa contínua entre blocos (sem estalos), com filtro antialiasing.
- `AudioHub`: abre o microfone só enquanto alguém escuta (IA ou operador), recebe do alto-falante o que de fato
  tocou (referência do eco) e entrega o áudio limpo a cada assinante na taxa dele.
Modos: "eco" (cancelamento, padrão), "fone" (fone de ouvido: sem eco, sem processamento) e "meia" (meia-via:
microfone mudo enquanto o robô fala, o comportamento antigo).
"""
from __future__ import annotations

import math
import queue
import threading
import time
from collections import deque
from typing import Callable, Dict, Optional

import numpy as np

RATE = 16000                 # taxa interna do processamento de eco (voz)
BLOCK = 256                  # 16 ms
MODES = ("eco", "fone", "meia")
MODE_LABELS = {"eco": "Cancelamento de eco (alto-falante)", "fone": "Fone de ouvido (sem eco)",
               "meia": "Meia-via (mudo enquanto fala)"}


class StreamResampler:
    """Converte a taxa bloco a bloco, com estado (fase e filtro), sem descontinuidade entre blocos."""

    def __init__(self, src: int, dst: int):
        self.src, self.dst = int(src), int(dst)
        self._pos = 0.0                      # posição fracionária da próxima amostra de saída (em amostras de entrada)
        self._last = 0.0
        self._sos = self._zi = None
        if self.dst < self.src:
            try:
                from scipy.signal import butter, sosfilt_zi
                self._sos = butter(6, 0.45 * self.dst, fs=self.src, output="sos")
                self._zi = sosfilt_zi(self._sos) * 0.0
            except ImportError:
                self._sos = None

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float32)
        if self.src == self.dst or len(x) == 0:
            return x.copy()
        if self._sos is not None:
            from scipy.signal import sosfilt
            x, self._zi = sosfilt(self._sos, x, zi=self._zi)
            x = x.astype(np.float32)
        step = self.src / self.dst
        buf = np.concatenate([[self._last], x])        # índice 0 = última amostra do bloco anterior
        n = int(math.floor((len(x) - self._pos) / step)) + 1 if self._pos <= len(x) else 0
        idx = self._pos + step * np.arange(max(0, n))
        idx = idx[idx <= len(x)]
        out = np.interp(idx, np.arange(len(buf)), buf).astype(np.float32)
        self._pos = (idx[-1] + step - len(x)) if len(idx) else self._pos - len(x)
        self._last = float(x[-1])
        return out


class EchoCanceller:
    """PBFDAF (overlap-save) + detecção de fala dupla + supressão do eco residual. Amostras em float (-1..1)."""

    def __init__(self, rate: int = RATE, block: int = BLOCK, tail_ms: int = 400, mu: float = 0.5):
        self.N = block
        self.P = int(math.ceil(tail_ms / 1000 * rate / block))
        self.mu = mu
        self.X = np.zeros((self.P, block + 1), np.complex64)
        self.W = np.zeros((self.P, block + 1), np.complex64)
        self._xprev = np.zeros(block, np.float32)
        self._erl = 1.0                       # eco/ref aprendido (começa conservador: eco tão alto quanto a ref)
        self._far = 0.0                        # energia recente da referência
        self._gain = 1.0
        self.near_talk = False
        self.far_talk = False
        self.erle_db = 0.0
        self._dt_time = 0

    def reset(self) -> None:
        self.W[:] = 0
        self.X[:] = 0
        self._erl, self._gain = 1.0, 1.0

    def process(self, mic: np.ndarray, ref: np.ndarray) -> np.ndarray:
        N = self.N
        mic = np.asarray(mic, np.float32)
        ref = np.asarray(ref, np.float32)
        Xk = np.fft.rfft(np.concatenate([self._xprev, ref])).astype(np.complex64)
        self._xprev = ref
        self.X = np.roll(self.X, 1, axis=0)
        self.X[0] = Xk
        y = np.fft.irfft((self.W * self.X).sum(axis=0), 2 * N)[N:].astype(np.float32)
        e = mic - y
        ref_e, mic_e, y_e, e_e = (float(np.mean(v * v)) + 1e-12 for v in (ref, mic, y, e))
        self._far = max(ref_e, 0.85 * self._far)
        self.far_talk = self._far > 1e-6
        # fala dupla: o microfone bem mais alto do que o eco esperado, ou (já convergido) sobra muito além do eco
        expected = self._erl * self._far
        ratio = e_e / mic_e
        self.near_talk = mic_e > 4.0 * expected + 2e-6 or (self.erle_db > 8.0 and ratio > 0.25 and mic_e > 1e-6)
        self._dt_time = self._dt_time + 1 if (self.near_talk and self.far_talk) else 0
        mu = self.mu
        if self.near_talk and self._dt_time > 125:              # >2 s "falando junto": o eco pode ter mudado
            mu, self.near_talk = self.mu * 0.1, False             # (robô mudou de lugar): reaprende devagar
        if self.far_talk and not self.near_talk:
            E = np.fft.rfft(np.concatenate([np.zeros(N, np.float32), e])).astype(np.complex64)
            power = (np.abs(self.X) ** 2).sum(axis=0) + 1e-6 * (2 * N)
            G = self.X.conj() * (E / power)[None, :]
            g = np.fft.irfft(G, 2 * N, axis=1)
            g[:, N:] = 0                                            # restrição de gradiente (convolução linear)
            self.W += (mu * np.fft.rfft(g, axis=1)).astype(np.complex64)
            if y_e > 1e-9:
                self._erl = 0.98 * self._erl + 0.02 * min(4.0, y_e / ref_e)
            self.erle_db = 0.95 * self.erle_db + 0.05 * 10 * math.log10(mic_e / e_e)
        # supressão residual: só eco tocando (ninguém falando perto) -> abaixa o que sobrou
        target = 0.25 if (self.far_talk and not self.near_talk) else 1.0
        rate = 0.25 if target < self._gain else 0.7                   # volta rápido quando alguém fala perto
        self._gain += (target - self._gain) * rate
        return e * self._gain


class AudioHub:
    """Microfone único (abre só com assinantes), referência do alto-falante e entrega do áudio limpo."""

    def __init__(self, mode: str = "eco", capture_factory: Optional[Callable] = None, linger: float = 3.0):
        self.mode = mode if mode in MODES else "eco"
        self.aec = EchoCanceller()
        self._lock = threading.RLock()
        self._subs: Dict[int, tuple] = {}
        self._next = 1
        self._ref = deque()
        self._ref_len = 0
        self._ref_rs = StreamResampler(24000, RATE)
        self._in = np.zeros(0, np.float32)
        self._in_rs = None
        self._in_rate = None
        self._stream = None
        self._capture_factory = capture_factory
        self._linger = linger
        self._work: "queue.SimpleQueue" = queue.SimpleQueue()
        self._worker = None
        self.dropped = 0
        self._idle_since = None
        self.level = 0.0
        self.playing_until = 0.0                # meia-via: mudo até um pouco depois de tocar
        self.error = ""

    # ---- referência (o que o alto-falante tocou agora) ----
    def _ensure_worker(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._work_loop, name="audio-hub", daemon=True)
            self._worker.start()

    def _work_loop(self) -> None:
        while True:
            item = self._work.get()
            if item is None:
                return
            try:
                if item[0] == "cap":
                    if self._work.qsize() > 12:          # atrasou (>240 ms): descarta para não acumular atraso
                        self.dropped += 1
                        continue
                    self.feed_capture(item[1], item[2])
                else:
                    self.push_reference(item[1], item[2])
            except Exception as error:
                self.error = str(error)[:120]

    def submit_capture(self, block: np.ndarray, rate: int) -> None:
        """Chamado pela thread do PortAudio: só copia e enfileira (o processamento pesado fica no worker)."""
        self._ensure_worker()
        self._work.put(("cap", np.array(block, copy=True), rate))

    def submit_reference(self, pcm: np.ndarray, rate: int = 24000) -> None:
        self._ensure_worker()
        self._work.put(("ref", np.array(pcm, copy=True), rate))

    def push_reference(self, pcm: np.ndarray, rate: int = 24000) -> None:
        x = np.asarray(pcm, np.float32) / 32768.0
        if np.any(x):
            self.playing_until = time.perf_counter() + 0.25
        if self.mode != "eco" or self._stream is None:
            return
        with self._lock:
            if self._ref_rs.src != rate:
                self._ref_rs = StreamResampler(rate, RATE)
            y = self._ref_rs.process(x)
            self._ref.append(y)
            self._ref_len += len(y)
            limit = int(0.08 * RATE)                   # atraso excessivo desalinha o eco: mantém ~40 ms
            while self._ref_len > limit and len(self._ref) > 1:
                self._ref_len -= len(self._ref.popleft())

    def _pop_ref(self, n: int) -> np.ndarray:
        out = np.zeros(n, np.float32)
        got = 0
        while got < n and self._ref:
            chunk = self._ref[0]
            take = min(n - got, len(chunk))
            out[got:got + take] = chunk[:take]
            if take == len(chunk):
                self._ref.popleft()
            else:
                self._ref[0] = chunk[take:]
            self._ref_len -= take
            got += take
        return out

    # ---- assinantes ----
    def subscribe(self, rate: int, callback: Callable[[np.ndarray], None]) -> int:
        """`callback(pcm_int16)` na taxa pedida, na thread do microfone. Abre o microfone se preciso."""
        with self._lock:
            sid = self._next
            self._next += 1
            self._subs[sid] = (int(rate), callback, StreamResampler(RATE, rate))
            self._idle_since = None
        self._ensure_stream()
        return sid

    def unsubscribe(self, sid: int) -> None:
        with self._lock:
            self._subs.pop(sid, None)
            if not self._subs:
                self._idle_since = time.perf_counter()
        threading.Timer(self._linger + 0.05, self._maybe_close).start()

    @property
    def active(self) -> bool:
        return self._stream is not None

    def _maybe_close(self) -> None:
        with self._lock:
            if self._subs or self._idle_since is None or time.perf_counter() - self._idle_since < self._linger:
                return
            stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

    def _ensure_stream(self) -> None:
        with self._lock:
            if self._stream is not None:
                return
            if self._capture_factory is not None:
                self._stream = self._capture_factory(self.feed_capture)
                return
        try:
            import sounddevice as sd
        except Exception as error:
            self.error = f"Microfone indisponível: {error}"
            raise RuntimeError(self.error) from error
        last = None
        for rate in (48000, None):
            try:
                if rate is None:
                    rate = int(sd.query_devices(kind="input")["default_samplerate"])
                stream = sd.InputStream(samplerate=rate, channels=1, dtype="int16", blocksize=int(rate * 0.02),
                                        callback=lambda indata, frames, t, status, r=rate: self.submit_capture(indata[:, 0], r))
                stream.start()
                with self._lock:
                    self._stream = stream
                self.error = ""
                return
            except Exception as error:
                last = error
        self.error = f"Microfone indisponível: {last}"
        raise RuntimeError(self.error)

    # ---- captura -> eco -> assinantes ----
    def feed_capture(self, block: np.ndarray, rate: int) -> None:
        try:
            x = np.asarray(block, np.float32) / 32768.0
            with self._lock:
                if self._in_rate != rate:
                    self._in_rs, self._in_rate = StreamResampler(rate, RATE), rate
                self._in = np.concatenate([self._in, self._in_rs.process(x)])
                frames = []
                while len(self._in) >= BLOCK:
                    frame, self._in = self._in[:BLOCK], self._in[BLOCK:]
                    ref = self._pop_ref(BLOCK)
                    if self.mode == "eco":
                        frame = self.aec.process(frame, ref)
                    elif self.mode == "meia" and time.perf_counter() < self.playing_until:
                        frame = np.zeros_like(frame)
                    frames.append(frame)
                subs = list(self._subs.values())
            for frame in frames:
                self.level = 0.8 * self.level + 0.2 * min(1.0, float(np.sqrt(np.mean(frame * frame))) / 0.05)
                for rate_out, cb, rs in subs:
                    out = rs.process(frame)
                    try:
                        cb(np.clip(out * 32768.0, -32768, 32767).astype(np.int16))
                    except Exception:
                        pass
        except Exception as error:                  # nunca derruba a thread de áudio
            self.error = str(error)[:120]

    def set_mode(self, mode: str) -> None:
        if mode in MODES:
            with self._lock:
                self.mode = mode
                self._ref.clear()
                self._ref_len = 0

    def far_active(self) -> bool:
        """O robô está tocando som agora (ou acabou de tocar)."""
        return time.perf_counter() < self.playing_until

    def close(self) -> None:
        self._work.put(None)
        with self._lock:
            self._subs.clear()
            stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
