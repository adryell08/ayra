"""Saúde da captura: somente números e digest; nunca mantém um quadro."""
import hashlib
import numpy as np


class CaptureHealth:
    def __init__(self):
        self.reset()

    def reset(self):
        self.last_frame_at = None
        self.last_change_at = None
        self._digest = None
        self.varied = False
        self.fps = 0.0
        self.brightness = None
        self.color = 0.0
        self.resolution = None

    def observe(self, frame, now):
        if self.last_frame_at is not None:
            instant = 1 / max(now - self.last_frame_at, 0.001)
            self.fps = instant if not self.fps else 0.8 * self.fps + 0.2 * instant
        self.last_frame_at = now
        self.resolution = (int(frame.shape[1]), int(frame.shape[0]))
        self.brightness = float(frame.mean())
        if frame.ndim == 3 and frame.shape[2] >= 3:
            sample = frame[::16, ::16, :3].astype(np.float32)
            self.color = float((np.abs(sample[..., 0] - sample[..., 1]).mean()
                                + np.abs(sample[..., 1] - sample[..., 2]).mean()) / 2)
        else:
            self.color = 0.0
        digest = hashlib.blake2b(memoryview(np.ascontiguousarray(frame)), digest_size=8).digest()
        if digest != self._digest:
            if self._digest is not None:
                self.varied = True
            self.last_change_at = now
            self._digest = digest

    def snapshot(self, now):
        age = None if self.last_frame_at is None else max(0, now - self.last_frame_at)
        if age is None:
            state, note = "waiting", "Aguardando primeira imagem: selecionando/abrindo câmera."
        elif age > 1.0:
            state, note = "waiting", "Aguardando imagem: nenhum quadro novo há mais de 1 s."
        elif self.brightness < 3:
            state, note = "waiting", "Aguardando imagem: quadro quase preto; confira tampa/iluminação e câmera selecionada."
        elif self.color < 0.5:
            state, note = "waiting", "Aguardando imagem RGB: quadro monocromático; confira se é câmera IR."
        elif not self.varied or now - self.last_change_at > 1.5:
            state, note = "waiting", "Aguardando imagem: quadro estático; mova-se ou selecione outra câmera."
        else:
            state, note = "ok", ""
        return dict(state=state, note=note, capture_fps=self.fps if age is not None and age <= 1 else 0.0,
                    brightness=self.brightness, color=self.color, resolution=self.resolution,
                    frame_age_ms=None if age is None else age * 1000)
