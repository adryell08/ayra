"""Filtro de voz robótica (vocoder de canais) em tempo real, a 24 kHz.

A mesma "garganta" sintética para todos: a voz da IA (Realtime) e a do operador humano passam pelo mesmo
vocoder com tom fixo, então soam iguais. Processa em blocos com estado (sem estalos entre blocos) e
custa pouco processador (~1 ms por bloco de 40 ms num i5).
"""
from __future__ import annotations

import threading

import numpy as np

RATE = 24000


class RobotVoice:
    def __init__(self, rate: int = RATE, pitch: float = 105.0, bands: int = 18, mix_ring: float = 0.04, seed: int = 7):
        from scipy.signal import butter, sosfilt_zi        # carga tardia: ~70 MB só quando a voz robótica é usada
        self.rate, self.pitch, self.mix_ring = rate, pitch, mix_ring
        edges = np.geomspace(120.0, min(6800.0, rate * 0.45), bands + 1)
        self._bands = []
        for lo, hi in zip(edges[:-1], edges[1:]):
            sos = butter(2, [lo, hi], btype="bandpass", fs=rate, output="sos")
            self._bands.append(dict(sos=sos, zi_mod=sosfilt_zi(sos) * 0.0, zi_car=sosfilt_zi(sos) * 0.0,
                                    noisy=lo > 2600.0))
        self._env_sos = butter(2, 50.0, btype="lowpass", fs=rate, output="sos")   # envelope rápido: consoantes nítidas
        for b in self._bands:
            b["zi_env"] = sosfilt_zi(self._env_sos) * 0.0
        self._phase = 0.0
        self._delay = int(round(0.0035 * rate))                 # atraso do caminho robótico: o seco espera o mesmo tempo (sem eco/pente)
        self._tail = np.zeros(self._delay, np.float32)
        self._ring_phase = 0.0
        self._gain = 1.0
        self._rng = np.random.default_rng(seed)

    def reset(self) -> None:
        for b in self._bands:
            b["zi_mod"] *= 0.0
            b["zi_car"] *= 0.0
            b["zi_env"] *= 0.0
        self._gain = 1.0
        self._tail[:] = 0.0

    def process(self, pcm: np.ndarray, level: float = 1.0, dry: float | None = None) -> np.ndarray:
        """level: grau de robótica (0 = voz natural, 1 = totalmente robótica); a mistura com a voz original é
        (1 − level). `dry` força a parte original (uso avançado)."""
        level = max(0.0, min(1.0, float(level)))
        if dry is None:
            dry = 1.0 - level
        if level <= 0.0 and dry >= 1.0:
            return np.asarray(pcm, dtype=np.int16)
        from scipy.signal import sosfilt
        x = np.asarray(pcm, dtype=np.float32) / 32768.0
        n = len(x)
        if n == 0:
            return np.zeros(0, np.int16)
        # portadora: dente de serra no tom fixo (vozeado) + ruído para consoantes (bandas altas)
        t = self._phase + np.arange(n) * (self.pitch / self.rate)
        saw = 2.0 * (t - np.floor(t + 0.5))
        self._phase = float(t[-1] + self.pitch / self.rate) % 1.0
        noise = self._rng.standard_normal(n).astype(np.float32) * 0.6
        out = np.zeros(n, np.float32)
        for b in self._bands:
            mod, b["zi_mod"] = sosfilt(b["sos"], x, zi=b["zi_mod"])
            env, b["zi_env"] = sosfilt(self._env_sos, np.abs(mod), zi=b["zi_env"])
            car_src = 0.5 * saw + noise if b["noisy"] else saw
            car, b["zi_car"] = sosfilt(b["sos"], car_src, zi=b["zi_car"])
            out += car * np.maximum(env, 0.0)
        # pitada de modulação em anel da voz original: mantém a inteligibilidade com timbre metálico
        rt = self._ring_phase + np.arange(n) * (55.0 / self.rate)
        self._ring_phase = float(rt[-1] + 55.0 / self.rate) % 1.0
        ring = x * np.sin(2 * np.pi * rt)
        # volume acompanha o da voz original (ganho suavizado entre blocos)
        rms_in = float(np.sqrt(np.mean(x * x)) + 1e-7)
        rms_out = float(np.sqrt(np.mean(out * out)) + 1e-7)
        target = min(40.0, rms_in / rms_out) if rms_in > 2e-4 else self._gain
        self._gain = 0.8 * self._gain + 0.2 * target
        y = (1.0 - self.mix_ring) * out * self._gain + self.mix_ring * ring
        if dry > 0.0:
            ext = np.concatenate([self._tail, x])                  # voz original com o mesmo atraso do caminho robótico
            xd = ext[:n]
            self._tail = ext[n:n + self._delay].copy() if len(ext) >= n + self._delay else ext[-self._delay:].copy()
            y = (1.0 - dry) * y + dry * xd
        y = np.tanh(y * 1.4) / np.tanh(1.4)                       # leve saturação, sem estourar
        return np.clip(y * 32767.0, -32768, 32767).astype(np.int16)


class VoiceMask:
    """Perfil de voz da Ayra, o mesmo para a IA e para o operador.

    * original  — a voz como chega, sem processar (nunca é silenciada por este filtro);
    * robotica  — 100 % filtro (sem voz original misturada): identidade vocal padronizada;
    * mistura   — parte original, parte filtro (não padroniza a identidade: o painel avisa).

    Regra de segurança: com um perfil de máscara ativo (robótica ou mistura) o áudio original NUNCA toca como plano B.
    Se o filtro falhar (biblioteca ausente, erro, saída inválida), `process` devolve None — quem chama descarta o bloco —
    e o motivo fica só em `diag()` (painel privado). Depois de uma falha há uma pausa curta antes de tentar de novo.
    """
    PROFILES = ("original", "robotica", "mistura")
    RETRY_S = 2.0

    def __init__(self, level: float = 0.0, factory=None, clock=None):
        import time
        self._factory = factory or RobotVoice
        self._clock = clock or time.perf_counter
        self._filter = None
        self._flock = threading.Lock()
        self._warming = False
        self._retry_at = 0.0
        self.level = 0.0
        self.set_level(level)
        self.processed = {"ai": 0, "operator": 0}
        self.suppressed = {"ai": 0, "operator": 0}
        self.failures = 0
        self.consecutive = 0
        self.last_error = ""
        self._ms: list = []

    # ---- perfil ------------------------------------------------------------------------
    def set_level(self, level: float) -> None:
        level = max(0.0, min(1.0, float(level)))
        self.level = 1.0 if level >= 0.995 else (0.0 if level <= 0.0 else level)

    def warm(self) -> None:
        """Carrega o filtro (e o scipy, ~2 s) AGORA, de forma síncrona. O app chama na criação quando já há máscara ativa (como antes);
        sem máscara nada é carregado (memória). Falha não levanta: aparece (e é contada) no primeiro uso real."""
        try:
            with self._flock:
                if self._filter is None:
                    self._filter = self._factory()
        except Exception:
            pass

    def warm_async(self) -> None:
        """Para quando a máscara é ligada durante o uso (painel/menu): carrega em segundo plano, uma vez."""
        if self._filter is None and not self._warming:
            self._warming = True

            def run():
                try:
                    self.warm()
                finally:
                    self._warming = False
            threading.Thread(target=run, name="voz-warm", daemon=True).start()

    @property
    def profile(self) -> str:
        return "original" if self.level <= 0.0 else ("robotica" if self.level >= 1.0 else "mistura")

    @property
    def active(self) -> bool:
        return self.level > 0.0

    @property
    def mixed(self) -> bool:
        return self.profile == "mistura"

    # ---- processamento -----------------------------------------------------------------
    def reset(self) -> None:
        if self._filter is not None:
            self._filter.reset()

    def process(self, pcm, source: str = "ai"):
        """Áudio pronto para tocar, ou None quando a máscara está ativa e falhou (nunca o original)."""
        src = "operator" if source == "operator" else "ai"
        x = np.asarray(pcm, dtype=np.int16)
        if self.level <= 0.0:
            return x
        now = self._clock()
        if now < self._retry_at:
            self.suppressed[src] += 1
            return None
        t0 = now
        try:
            with self._flock:
                if self._filter is None:
                    self._filter = self._factory()
            y = self._filter.process(x, level=self.level, dry=0.0 if self.level >= 1.0 else None)
            y = np.asarray(y)
            if y.shape != x.shape or y.dtype != np.int16 or (len(x) and not np.any(np.isfinite(y.astype(np.float32)))):
                raise ValueError("saída do filtro inválida")
        except Exception as error:                                # nunca derruba o áudio nem o app
            self.failures += 1
            self.consecutive += 1
            self.last_error = f"{type(error).__name__}: {str(error)[:120]}"
            self._filter = None
            self._retry_at = self._clock() + self.RETRY_S
            self.suppressed[src] += 1
            return None
        self.consecutive = 0
        self.processed[src] += 1
        self._ms = (self._ms + [(self._clock() - t0) * 1000.0])[-200:]
        return y

    def diag(self) -> dict:
        """Diagnóstico para o painel privado do operador (nunca vai para a tela pública)."""
        w = sorted(self._ms)
        return dict(perfil=self.profile, nivel=round(self.level * 100), misturado=self.mixed,
                    ok=self.consecutive == 0, falhas=self.failures, descartados=dict(self.suppressed),
                    processados=dict(self.processed), ultimo_erro=self.last_error,
                    bloco_p50_ms=round(w[len(w) // 2], 3) if w else 0.0, bloco_p95_ms=round(w[int(len(w) * .95)], 3) if w else 0.0)


def synthesize_local(text: str, rate: int = RATE):
    """Voz gratuita do Windows (SAPI via pyttsx3) → PCM 24 kHz. Usada pelo operador no modo teleoperado.
    Devolve None se não houver voz local disponível."""
    import os
    import tempfile
    import wave
    try:
        import pyttsx3
    except ImportError:
        return None
    path = tempfile.mktemp(suffix=".wav")
    try:
        engine = pyttsx3.init()
        for voice in engine.getProperty("voices") or []:
            tag = (getattr(voice, "id", "") + " " + getattr(voice, "name", "")).lower()
            if "pt-br" in tag or "pt_br" in tag or "portug" in tag or "maria" in tag:
                engine.setProperty("voice", voice.id)
                break
        engine.save_to_file(text, path)
        engine.runAndWait()
        with wave.open(path, "rb") as w:
            src_rate, width, ch = w.getframerate(), w.getsampwidth(), w.getnchannels()
            raw = w.readframes(w.getnframes())
        if width != 2:
            return None
        pcm = np.frombuffer(raw, np.int16)
        if ch > 1:
            pcm = pcm.reshape(-1, ch).mean(axis=1).astype(np.int16)
        from realtime import resample
        return resample(pcm, src_rate, rate)
    except Exception:
        return None
    finally:
        try:
            os.remove(path)                                   # nada de áudio fica em disco
        except OSError:
            pass
