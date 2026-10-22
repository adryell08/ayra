"""Expressões do robô: poses dos olhos, transições animadas e piscadas.

Uma *pose* é um vetor de parâmetros (abertura, largura, inclinação da pálpebra...).
O desenho em si fica em `face.py`; aqui só decidimos *o que mostrar* e *como chegar lá*.
Quando a IA existir, ela só precisa chamar `pulse()` / `set_mood()`: nada mais muda.
"""
from __future__ import annotations

import math
import random
from collections import deque
from typing import Deque, List, Optional, Tuple

import numpy as np

OPEN, WIDTH, TILT, LID, CHEEK, RADIUS, DY, GLINT, ARCH = range(9)


def _pose(open=1.0, width=1.0, tilt=0.0, lid=0.0, cheek=0.0, radius=0.38, dy=0.0, glint=1.0, arch=0.0):
    return np.array([open, width, tilt, lid, cheek, radius, dy, glint, arch], dtype=np.float64)


# open   : abertura vertical (1 = normal, >1 arregalado, ~0 fechado)
# tilt   : pálpebra superior inclinada (+ bravo: canto interno desce; - triste: canto externo desce)
# lid    : pálpebra superior reta cobrindo parte do olho (sonolento)
# cheek  : pálpebra inferior subindo em arco (olhos que sorriem)
# arch   : 0..1 troca o olho por um arco ∩ de espessura uniforme (olhos felizes ^ ^)
EXPRESSIONS = {
    "neutral":   _pose(),
    "happy":     _pose(open=1.0, arch=1.0, dy=-0.02, glint=0.0),
    "surprised": _pose(open=1.22, width=1.0, radius=0.50),
    "sad":       _pose(open=0.88, tilt=-0.50, lid=0.04, dy=0.06, glint=0.75),
    "angry":     _pose(open=0.80, tilt=0.85, lid=0.10, radius=0.22, glint=0.45),
    "wide":      _pose(open=1.16, radius=0.42),
    "sleepy":    _pose(open=0.66, lid=0.40, dy=0.10, glint=0.5),
    "sleeping":  _pose(open=0.10, radius=0.50, dy=0.14, glint=0.0),
}
LABELS = {
    "auto": "Automático", "neutral": "Neutro", "happy": "Feliz", "surprised": "Surpreso",
    "sad": "Triste", "angry": "Bravo", "wide": "Arregalado", "sleepy": "Sonolento",
    "sleeping": "Dormindo",
}
# atalhos 1..8 -> expressão
KEY_ORDER = ["neutral", "happy", "surprised", "sad", "angry", "wide", "sleepy", "sleeping"]

# frequência natural (rad/s) de cada parâmetro: abertura é mais ágil
_OMEGA = np.array([24.0, 15.0, 15.0, 15.0, 15.0, 12.0, 12.0, 10.0, 16.0])
REFLEX_TIMINGS = (0.035, 0.015, 0.040)  # fechar, fechado, abrir: 90 ms
REFLEX_COOLDOWN = 2.5


class Blinker:
    """Piscadas com ritmo irregular (2–6 s), piscada dupla ocasional e piscar de um olho só."""

    def __init__(self, rng: random.Random, reduce_motion: bool = False):
        self.rng = rng
        self.reduce_motion = reduce_motion
        self._t = 0.0
        self._phase: Optional[str] = None
        self._side = 0                  # 0 = ambos, -1 = esquerdo, +1 = direito
        self._timings = (0.06, 0.03, 0.10)
        self._next = self._schedule()
        self._double_pending = False
        self._reflex = False
        self._reflex_wait = 0.0
        self._blink_wait = 0.0

    def _schedule(self) -> float:
        lo, hi = (2.2, 6.0) if not self.reduce_motion else (4.0, 9.0)
        return self.rng.uniform(lo, hi)

    @property
    def reflex_active(self) -> bool:
        return self._reflex and self._phase is not None

    def trigger(self, side: int = 0, *, reflex: bool = False) -> bool:
        """Retorna se aceitou; o reflexo acelera uma piscada que já está em curso.

        Nunca fecha novamente durante a abertura. Entre novos ciclos há pelo menos
        350 ms, inclusive ao misturar reflexo, piscada natural e controle manual.
        """
        if reflex and self._reflex_wait > 1e-9:
            return False
        if self._phase is not None:
            if not reflex:
                return False
        elif self._blink_wait > 1e-9:
            return False
        start = 0.0
        if reflex and self._phase is not None:
            # Mantém a abertura atual, sem acrescentar outro ciclo visual.
            close, hold, opn = self._timings
            if self._phase == "opening":
                progress = min(max((self._t - close - hold) / opn, 0.0), 1.0)
                start = sum(REFLEX_TIMINGS[:2]) + progress * REFLEX_TIMINGS[2]
            else:
                start = min(self._t / close, 1.0) * REFLEX_TIMINGS[0]
        self._phase, self._t, self._side = "closing", start, side
        self._reflex = reflex
        self._blink_wait = 0.35
        if reflex:
            self._side = 0
            self._timings = REFLEX_TIMINGS
            self._reflex_wait = REFLEX_COOLDOWN
            self._double_pending = False
            self._next = self._schedule()
        elif side == 0:
            self._timings = (0.06, 0.03, 0.10) if not self.reduce_motion else (0.14, 0.0, 0.22)
        else:  # piscadela: mais lenta e deliberada
            self._timings = (0.12, 0.28, 0.16)
        return True

    def update(self, dt: float, enabled: bool = True) -> Tuple[float, float]:
        """Devolve (esq, dir) em [0,1]: 1 = olho aberto, 0 = fechado."""
        dt = max(0.0, dt)
        self._reflex_wait = max(0.0, self._reflex_wait - dt)
        self._blink_wait = max(0.0, self._blink_wait - dt)
        if self._phase is None:
            self._next -= dt
            if self._next <= 0:
                if enabled and self.trigger(0):
                    self._double_pending = self.rng.random() < 0.15
                self._next = self._schedule()
            return 1.0, 1.0
        close, hold, opn = self._timings
        self._t += dt
        # Tempo acumulado: não perde sobras entre fases (90 ms também a 60 Hz).
        if self._t < close:
            self._phase = "closing"
            v = 1.0 - self._t / close
        elif self._t < close + hold:
            self._phase = "hold"
            v = 0.0
        else:
            self._phase = "opening"
            v = min((self._t - close - hold) / opn, 1.0)
            if self._t >= close + hold + opn - 1e-12:
                self._phase = None
                if self._double_pending:
                    self._double_pending = False
                    self._next = 0.22
                v = 1.0
        v = v * v * (3 - 2 * v)  # suaviza
        if self._side == 0:
            return v, v
        return (v, 1.0) if self._side < 0 else (1.0, v)


class ExpressionEngine:
    def __init__(self, reduce_motion: bool = False, rng: Optional[random.Random] = None):
        self.rng = rng or random.Random()
        self.reduce_motion = reduce_motion
        self.auto_blink = True
        self.mood = "auto"                       # "auto" ou nome fixo (modo de teste)
        self.idle_level = 0                      # 0 desperto, 1 sonolento, 2 dormindo
        self._pulses: Deque[Tuple[str, float]] = deque()
        self.pose = EXPRESSIONS["neutral"].copy()
        self.vel = np.zeros_like(self.pose)
        self.blinker = Blinker(self.rng, reduce_motion)
        self.blink = (1.0, 1.0)
        self.zzz: List[dict] = []                # partículas "z" ao dormir
        self._zzz_next = 0.5
        self._recoil_left = 0.0
        self._set_dynamics()

    # ---- configuração -----------------------------------------------------
    def _set_dynamics(self) -> None:
        zeta = 1.0 if self.reduce_motion else 0.72      # sem "mola" com Reduzir Movimento
        w = _OMEGA * (0.7 if self.reduce_motion else 1.0)
        self._k = w * w
        self._c = 2.0 * zeta * w

    def set_reduce_motion(self, on: bool) -> None:
        self.reduce_motion = on
        self.blinker.reduce_motion = on
        self._set_dynamics()
        if on:
            self._recoil_left = 0.0

    def recoil_now(self) -> None:
        self._recoil_left = 0.0 if self.reduce_motion else 0.24

    def visual_pose(self):
        if self._recoil_left <= 0 or self.reduce_motion:
            return self.pose
        scale = 1 - 0.12 * math.sin(math.pi * (1 - self._recoil_left / .24))
        pose = self.pose.copy()
        pose[OPEN] *= scale
        pose[WIDTH] *= scale
        return pose

    def set_mood(self, name: str) -> None:
        self.mood = name if (name == "auto" or name in EXPRESSIONS) else "auto"

    def pulse(self, name: str, seconds: float = 1.0) -> None:
        """Expressão passageira (reação a eventos). Depois volta ao humor de base."""
        if name in EXPRESSIONS:
            self._pulses.clear()
            self._pulses.append((name, seconds))

    def pulse_seq(self, seq: List[Tuple[str, float]]) -> None:
        self._pulses.clear()
        for name, sec in seq:
            if name in EXPRESSIONS:
                self._pulses.append((name, sec))

    def blink_now(self, side: int = 0, *, reflex: bool = False) -> bool:
        """Piscada normal ou reflexo bilateral de 90 ms, com cooldown de 2,5 s."""
        return self.blinker.trigger(side, reflex=reflex)

    def set_idle_level(self, level: int) -> None:
        self.idle_level = level

    # ---- estado -----------------------------------------------------------
    def shown_name(self) -> str:
        if self.mood != "auto":
            return self.mood
        if self.idle_level >= 2:
            return "sleeping"
        if self.idle_level == 1:
            return "sleepy"
        if self._pulses:
            return self._pulses[0][0]
        return "neutral"

    def update(self, dt: float) -> None:
        dt = min(max(dt, 0.0), 0.05)
        self._recoil_left = max(0.0, self._recoil_left - dt)
        if self._pulses:
            name, left = self._pulses[0]
            left -= dt
            if left <= 0:
                self._pulses.popleft()
            else:
                self._pulses[0] = (name, left)
        shown = self.shown_name()
        target = EXPRESSIONS[shown]
        n = max(1, int(math.ceil(dt / 0.008)))
        h = dt / n
        for _ in range(n):
            acc = self._k * (target - self.pose) - self._c * self.vel
            self.vel += acc * h
            self.pose += self.vel * h
        self.pose[OPEN] = max(self.pose[OPEN], 0.02)
        self.pose[WIDTH] = max(self.pose[WIDTH], 0.3)
        self.blink = self.blinker.update(dt, enabled=target[OPEN] > 0.25 and self.auto_blink)
        self._update_zzz(dt, shown == "sleeping")

    def _update_zzz(self, dt: float, sleeping: bool) -> None:
        for z in self.zzz:
            z["age"] += dt
        self.zzz = [z for z in self.zzz if z["age"] < z["life"]]
        if sleeping and not self.reduce_motion:
            self._zzz_next -= dt
            if self._zzz_next <= 0:
                self.zzz.append({"age": 0.0, "life": 3.4})
                self._zzz_next = 1.9
        elif sleeping and self.reduce_motion and not self.zzz:
            self.zzz.append({"age": 0.0, "life": 3.4})   # um "z" parado, sem repetição
