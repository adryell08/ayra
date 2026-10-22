"""Controle do olhar: movimento suave com sacadas rápidas e microssacadas.

* perseguição suave (smooth pursuit) enquanto o alvo se move devagar;
* sacada (saccade) rápida quando o alvo "pula" (troca de pessoa, olhada de relance);
* microssacadas: pequenos tremores durante a fixação, que dão vida ao olhar.
Com "Reduzir Movimento" as microssacadas somem e o movimento fica mais lento e sem mola.
"""
from __future__ import annotations

import random
from typing import Optional, Tuple

import numpy as np


class GazeController:
    def __init__(self, reduce_motion: bool = False, rng: Optional[random.Random] = None):
        self.rng = rng or random.Random()
        self.reduce_motion = reduce_motion
        self.pos = np.zeros(2)
        self.vel = np.zeros(2)
        self.target = np.zeros(2)
        self.mode = "pursuit"
        self._micro = np.zeros(2)
        self._micro_next = 0.6
        self.big_saccade = False

    @property
    def motion_scale(self) -> float:
        return 0.70 if self.reduce_motion else 1.0

    def set_target(self, gx: float, gy: float) -> None:
        new = np.clip(np.array([gx, gy], dtype=np.float64), -1.0, 1.0)
        jump = float(np.linalg.norm(new - self.target))
        self.target = new
        if jump > 0.28:
            self.mode = "saccade"
            if jump > 0.5:
                self.big_saccade = True

    def consume_big_saccade(self) -> bool:
        v, self.big_saccade = self.big_saccade, False
        return v

    def update(self, dt: float) -> Tuple[float, float]:
        dt = min(max(dt, 0.0), 0.05)
        if not self.reduce_motion:
            self._micro_next -= dt
            if self._micro_next <= 0:
                amp = self.rng.uniform(0.010, 0.030)
                ang = self.rng.uniform(0, 6.2832)
                self._micro = np.array([np.cos(ang), np.sin(ang)]) * amp
                self._micro_next = self.rng.uniform(0.4, 1.6)
        else:
            self._micro[:] = 0.0

        err = self.target + self._micro - self.pos
        if self.mode == "saccade" and np.linalg.norm(err) < 0.05:
            self.mode = "pursuit"
        if self.reduce_motion:
            w = 8.0                              # uma só rigidez: sem “mola” e sem overshoot
        else:
            w = 34.0 if self.mode == "saccade" else 11.0
        n = max(1, int(dt / 0.004))
        h = dt / n
        for _ in range(n):                       # mola criticamente amortecida
            acc = (w * w) * err - 2.0 * w * self.vel
            self.vel += acc * h
            self.pos += self.vel * h
            err = self.target + self._micro - self.pos
        self.pos = np.clip(self.pos, -1.05, 1.05)
        return float(self.pos[0]), float(self.pos[1])
