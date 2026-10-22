"""Simulações nomeadas (falar, chorar...): catálogo + executor determinístico.

A IA (ou o menu "Simulações") só pede `play(nome, ...)`/`stop(nome)`; o desenho fica em face.py.
Tempo injetado e RNG com seed: testes reproduzíveis. Nada aqui lê câmera ou áudio: o nível de fala
(0..1) é um número que a IA pode informar com `set_speech_level`.
"""
from __future__ import annotations

import math
import random
from typing import Dict, List, Optional

ACTIONS: Dict[str, dict] = {
    "speak": dict(label="Falar", seconds=6.0,
                  description="Boca abre e fecha em sincronia simulada de fala. A IA pode informar 'level' (0..1) do áudio real."),
    "cry": dict(label="Chorar", seconds=8.0,
                description="Olhos tristes tremendo, piscadas frequentes e lágrimas escorrendo."),
    "plead": dict(label="Súplica", seconds=6.0,
                  description="Olhos grandes, redondos e brilhantes com contorno fino e olhar para cima: pedido carinhoso."),
}
MAX_SECONDS = 60.0


class ActionPlayer:
    def __init__(self, seed: int = 0):
        self._rng = random.Random((seed or 0) + 1717)
        self._active: Dict[str, dict] = {}
        self._talk = 0.0
        self._phase = 0.0
        self._target = 0.0
        self._next_target = 0.0
        self._pause_until = 0.0
        self._next_pause = 1.2
        self._level: Optional[float] = None
        self._level_at = -math.inf
        self._tears: List[list] = []
        self._next_tear = 0.0
        self._next_blink = 0.0
        self._plead = 0.0
        self._next_look = 0.0

    # ---- API ------------------------------------------------------------------
    def play(self, name: str, now: float, seconds: Optional[float] = None) -> bool:
        if name not in ACTIONS:
            return False
        length = ACTIONS[name]["seconds"] if seconds is None else float(seconds)
        if not math.isfinite(length) or length <= 0:
            return False
        self._active[name] = dict(start=now, until=now + min(length, MAX_SECONDS))
        return True

    def stop(self, name: Optional[str] = None) -> None:
        if name is None or name == "all":
            self._active.clear()
        else:
            self._active.pop(name, None)

    def active(self) -> List[str]:
        return list(self._active)

    def set_speech_level(self, level: float, now: float) -> None:
        """Nível de áudio da fala (0..1) vindo da IA; vale por 300 ms e sobrepõe a simulação."""
        if math.isfinite(level):
            self._level, self._level_at = max(0.0, min(1.0, float(level))), now

    # ---- passo ----------------------------------------------------------------
    def update(self, now: float, dt: float, robot=None, reduce_motion: bool = False) -> dict:
        for name in [n for n, a in self._active.items() if now >= a["until"]]:
            del self._active[name]
        speaking = "speak" in self._active
        crying = "cry" in self._active
        external = self._level is not None and now - self._level_at < 0.3
        # --- fala -----------------------------------------------------------------
        if external:
            goal = self._level
        elif speaking:
            if now >= self._next_pause and now >= self._pause_until:
                self._pause_until = now + self._rng.uniform(0.22, 0.4)
                self._next_pause = self._pause_until + self._rng.uniform(1.0, 2.4)
            if now < self._pause_until:
                goal = 0.0
            else:
                self._phase += 2 * math.pi * 5.2 * max(dt, 0.0)
                if now >= self._next_target:
                    self._target = self._rng.uniform(0.35, 1.0)
                    self._next_target = now + self._rng.uniform(0.12, 0.28)
                goal = self._target * (0.35 + 0.65 * abs(math.sin(self._phase)))
        else:
            goal = 0.0
        k = min(1.0, max(dt, 0.0) * 22.0)
        self._talk += (goal - self._talk) * k
        # --- choro ----------------------------------------------------------------
        tremble = 0.0
        if crying:
            if robot is not None and robot.expr.mood == "auto":
                robot.expr.pulse("sad", 0.6)
            if now >= self._next_tear:
                side = -1 if len(self._tears) % 2 == 0 else 1
                self._tears.append([side, 0.0, self._rng.uniform(1.0, 1.5)])
                self._next_tear = now + self._rng.uniform(0.28, 0.55)
            if robot is not None and now >= self._next_blink:
                robot.expr.blink_now()
                self._next_blink = now + self._rng.uniform(1.2, 2.4)
            if not reduce_motion:
                tremble = 0.005 * math.sin(2 * math.pi * 11.0 * now)
        for tear in self._tears:
            tear[1] += max(dt, 0.0)
        self._tears = [t for t in self._tears if t[1] < t[2]]
        if not crying and not self._tears:
            self._next_tear = 0.0
        pleading = "plead" in self._active
        self._plead += ((1.0 if pleading else 0.0) - self._plead) * min(1.0, max(dt, 0.0) * 6.0)
        if pleading and robot is not None and now >= self._next_look:
            robot.attention.look_at(0.0, -0.30, 0.5, now)
            self._next_look = now + 0.3
        return {"plead": 0.0 if self._plead < 0.02 else self._plead,
                "talk": 0.0 if self._talk < 0.02 else self._talk,
                "tears": [(s, a, l) for s, a, l in self._tears], "tremble": tremble}
