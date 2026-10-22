"""Filtro de voz robótica (vocoder de canais) em tempo real, a 24 kHz.

A mesma "garganta" sintética para todos: a voz da IA (Realtime) e a do operador humano passam pelo mesmo
vocoder com tom fixo, então soam iguais. Processa em blocos com estado (sem estalos entre blocos) e
custa pouco processador (~1 ms por bloco de 40 ms num i5).
"""
from __future__ import annotations

import numpy as np
from scipy.signal import butter, sosfilt, sosfilt_zi

RATE = 24000


class RobotVoice:
    def __init__(self, rate: int = RATE, pitch: float = 105.0, bands: int = 18, mix_ring: float = 0.04, seed: int = 7):
        self.rate, self.pitch, self.mix_ring = rate, pitch, mix_ring
        edges = np.geomspace(120.0, min(6800.0, rate * 0.45), bands + 1)
        self._bands = []
        for lo, hi in zip(edges[:-1], edges[1:]):
            sos = butter(2, [lo, hi], btype="bandpass", fs=rate, output="sos")
            self._bands.append(dict(sos=sos, zi_mod=sosfilt_zi(sos) * 0.0, zi_car=sosfilt_zi(sos) * 0.0,
                                    noisy=lo > 2600.0))
        self._env_sos = butter(2, 28.0, btype="lowpass", fs=rate, output="sos")
        for b in self._bands:
            b["zi_env"] = sosfilt_zi(self._env_sos) * 0.0
        self._phase = 0.0
        self._ring_phase = 0.0
        self._gain = 1.0
        self._rng = np.random.default_rng(seed)

    def reset(self) -> None:
        for b in self._bands:
            b["zi_mod"] *= 0.0
            b["zi_car"] *= 0.0
            b["zi_env"] *= 0.0
        self._gain = 1.0

    def process(self, pcm: np.ndarray) -> np.ndarray:
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
        y = np.tanh(y * 1.4) / np.tanh(1.4)                       # leve saturação, sem estourar
        return np.clip(y * 32767.0, -32768, 32767).astype(np.int16)


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
