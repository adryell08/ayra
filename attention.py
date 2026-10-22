"""Gerenciador de atenção: com vários rostos, para quem olhar, por quanto tempo e quando trocar.

Comportamentos:
  * ALVO principal: o rosto de maior "pontuação" (perto, centralizado, em movimento,
    recém-chegado). Quem já é o alvo ganha um bônus (histerese) para o robô não ficar
    pulando de uma pessoa para outra.
  * TROCA: só acontece se outro rosto vencer por um tempo seguido (e o alvo atual já
    tiver sido mantido por um mínimo) — ou na hora, se o alvo sumir.
  * OLHADAS DE RELANCE: com 2+ pessoas, de tempos em tempos o robô olha ~1 s para outra
    pessoa e volta, como alguém atento à roda.
  * ROSTO NOVO: reage (olha de relance e arregala os olhos). Se estava sozinho/dormindo,
    "acorda" e cumprimenta.
  * OCIOSO: sem ninguém, o olhar passeia; depois de um tempo fica sonolento e dorme.
  * GANCHOS para a IA: `set_speaker(id)` (quem fala vira o alvo) e `look_at(x, y, s)`.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from tracker import Track, TrackEvent


@dataclass
class AttentionConfig:
    size_ref: float = 0.45          # altura do rosto (fração do quadro) que vale pontuação máxima
    w_size: float = 1.0
    w_center: float = 0.35
    w_motion: float = 0.40
    motion_ref: float = 0.5
    novelty_bonus: float = 0.45
    novelty_time: float = 2.5
    hysteresis: float = 0.30
    speaker_bonus: float = 1.5
    min_hold: float = 2.5           # s mantendo o alvo antes de aceitar troca
    switch_confirm: float = 0.6     # s seguidos em que o desafiante precisa vencer
    lose_grace: float = 0.4         # s esperando o alvo reaparecer antes de trocar
    glance_interval: Tuple[float, float] = (4.0, 9.0)
    glance_duration: Tuple[float, float] = (0.8, 1.4)
    new_face_glance: float = 1.4
    idle_delay: float = 2.5
    sleepy_after: float = 25.0
    sleep_after: float = 50.0
    wander_interval: Tuple[float, float] = (1.4, 3.6)
    too_close_size: float = 0.52
    too_close_cooldown: float = 10.0


@dataclass
class Event:
    kind: str       # new_face | greet | wake | switch | glance | glance_end | face_lost
    track_id: Optional[int] = None   # idle | sleepy | sleep | too_close


@dataclass
class AttentionState:
    mode: str = "idle"              # idle | searching | tracking | glance | override
    target_id: Optional[int] = None
    glance_id: Optional[int] = None
    focus_id: Optional[int] = None  # quem os olhos encaram agora
    gaze: Tuple[float, float] = (0.0, 0.0)
    n_faces: int = 0
    idle_level: int = 0             # 0 desperto, 1 sonolento, 2 dormindo
    proximity: float = 0.0          # altura do rosto-alvo (0..1)
    speaker_id: Optional[int] = None
    scores: Dict[int, float] = field(default_factory=dict)
    events: List[Event] = field(default_factory=list)


class AttentionManager:
    def __init__(self, cfg: Optional[AttentionConfig] = None, seed: Optional[int] = None):
        self.cfg = cfg or AttentionConfig()
        self.rng = random.Random(seed)
        self.target_id: Optional[int] = None
        self.target_hold = 0.0
        self.target_lost_t = 0.0
        self.challenger: Optional[int] = None
        self.challenger_t = 0.0
        self.glance_id: Optional[int] = None
        self.glance_end = 0.0
        self.next_glance = 0.0
        self.speaker_id: Optional[int] = None
        self.override: Optional[Tuple[float, float, float]] = None
        self.no_face_t = 0.0
        self.idle_level = 0
        self._idle = False
        self._wander = (0.0, 0.0)
        self._next_wander = 0.0
        self._last_gaze = (0.0, 0.0)
        self._too_close_until = 0.0
        self._near_faces = set()
        self._started = False

    # ---- ganchos para a IA / controle manual --------------------------------
    def set_speaker(self, track_id: Optional[int]) -> None:
        """Quem está falando vira o alvo preferencial (voz da IA ou do usuário)."""
        self.speaker_id = track_id

    def look_at(self, gx: float, gy: float, seconds: float, now: float = 0.0) -> None:
        self.override = (gx, gy, now + seconds)

    # ---- pontuação ---------------------------------------------------------
    def _score(self, tr: Track, now: float) -> float:
        c = self.cfg
        size = min(tr.h / c.size_ref, 1.0)
        gx, gy = tr.gaze_xy()
        center = 1.0 - min(math.hypot(gx, gy * 1.2) / 1.3, 1.0)
        motion = min(math.hypot(tr.vx, tr.vy) / c.motion_ref, 1.0)
        novelty = c.novelty_bonus * max(0.0, 1.0 - (now - tr.born) / c.novelty_time)
        s = c.w_size * size + c.w_center * center + c.w_motion * motion + novelty
        if tr.id == self.speaker_id:
            s += c.speaker_bonus
        return s

    def _sched_glance(self, now: float) -> float:
        lo, hi = self.cfg.glance_interval
        k = 2.0 if self.speaker_id is not None else 1.0
        return now + self.rng.uniform(lo, hi) * k

    def _start_glance(self, tid: int, seconds: float, now: float, ev: List[Event]) -> None:
        self.glance_id, self.glance_end = tid, now + seconds
        ev.append(Event("glance", tid))

    # ---- laço principal -----------------------------------------------------
    def update(self, tracks: List[Track], now: float, dt: float,
               tracker_events: Optional[List[TrackEvent]] = None) -> AttentionState:
        c = self.cfg
        ev: List[Event] = []
        if not self._started:
            self._started = True
            self.next_glance = self._sched_glance(now)
        cands = [t for t in tracks if t.confirmed and t.visible]
        by_id = {t.id: t for t in cands}
        n = len(cands)
        scores = {t.id: self._score(t, now) for t in cands}

        # --- presença / ociosidade / sono
        woke_or_greeted = False
        if n == 0:
            self.no_face_t += dt
            if self.no_face_t >= c.idle_delay and not self._idle:
                self._idle = True
                self._next_wander = now
                ev.append(Event("idle"))
            level = 2 if self.no_face_t >= c.sleep_after else 1 if self.no_face_t >= c.sleepy_after else 0
            if level != self.idle_level:
                if level == 1:
                    ev.append(Event("sleepy"))
                elif level == 2:
                    ev.append(Event("sleep"))
                self.idle_level = level
        else:
            if self._idle or self.idle_level > 0:
                ev.append(Event("wake" if self.idle_level > 0 else "greet"))
                woke_or_greeted = True
            self._idle = False
            self.idle_level = 0
            self.no_face_t = 0.0

        # --- rosto novo com outra pessoa já sendo observada
        for te in tracker_events or []:
            if te.kind == "new" and te.track_id in by_id and self.target_id is not None \
                    and te.track_id != self.target_id and self.target_id in by_id:
                ev.append(Event("new_face", te.track_id))
                if self.glance_id is None:
                    self._start_glance(te.track_id, c.new_face_glance, now, ev)

        # --- alvo perdido?
        if self.target_id is not None and self.target_id not in by_id:
            self.target_lost_t += dt
            if self.target_lost_t > c.lose_grace:
                ev.append(Event("face_lost", self.target_id))
                self.target_id = None
                self.glance_id = None
        else:
            self.target_lost_t = 0.0

        # --- escolher alvo se não há
        if self.target_id is None and cands:
            best = max(cands, key=lambda t: scores[t.id])
            self._set_target(best.id)
            if not woke_or_greeted:
                ev.append(Event("switch", best.id))

        # --- manter / trocar alvo
        cur = by_id.get(self.target_id) if self.target_id is not None else None
        if cur is not None:
            self.target_hold += dt
            cur.dwell += dt
            others = [t for t in cands if t.id != cur.id]
            if others:
                best = max(others, key=lambda t: scores[t.id])
                speaker_now = (best.id == self.speaker_id)
                needs = scores[best.id] > scores[cur.id] + c.hysteresis
                if needs and (self.target_hold >= c.min_hold or speaker_now):
                    if self.challenger == best.id:
                        self.challenger_t += dt
                    else:
                        self.challenger, self.challenger_t = best.id, 0.0
                    if self.challenger_t >= (0.15 if speaker_now else c.switch_confirm):
                        self._set_target(best.id)
                        ev.append(Event("switch", best.id))
                else:
                    self.challenger, self.challenger_t = None, 0.0

        # --- olhadas de relance
        if self.glance_id is not None:
            g = by_id.get(self.glance_id)
            if g is None or now >= self.glance_end or self.glance_id == self.target_id:
                ev.append(Event("glance_end", self.glance_id))
                self.glance_id = None
                self.next_glance = self._sched_glance(now)
        elif cur is not None and n >= 2 and now >= self.next_glance and self.override is None:
            others = [t for t in cands if t.id != cur.id]
            weights = [0.3 + max(scores[t.id], 0.0) for t in others]
            pick = self.rng.choices(others, weights=weights, k=1)[0]
            self._start_glance(pick.id, self.rng.uniform(*c.glance_duration), now, ev)

        # --- muito perto?
        self._near_faces.intersection_update(by_id)
        for tr in cands:
            if tr.h < c.too_close_size - .04:
                self._near_faces.discard(tr.id)
        if cur is not None and cur.h > c.too_close_size:
            if cur.id not in self._near_faces and now >= self._too_close_until:
                self._too_close_until = now + c.too_close_cooldown
                ev.append(Event("too_close", cur.id))
            self._near_faces.add(cur.id)

        # --- para onde olhar
        mode = "idle"
        focus: Optional[int] = None
        if self.override is not None and now < self.override[2]:
            gaze, mode = (self.override[0], self.override[1]), "override"
        elif self.override is not None:
            self.override = None
            gaze = self._last_gaze
        else:
            gaze = self._last_gaze
        if mode != "override":
            if self.glance_id is not None and self.glance_id in by_id:
                gaze, mode, focus = by_id[self.glance_id].gaze_xy(), "glance", self.glance_id
            elif cur is not None:
                gaze, mode, focus = cur.gaze_xy(), "tracking", cur.id
            elif self.target_id is not None:             # alvo sumiu há instantes: mantém o olhar
                gaze, mode = self._last_gaze, "searching"
            elif not self._idle:
                gaze, mode = self._last_gaze, "searching"
            else:
                gaze, mode = self._idle_gaze(now), "idle"
        self._last_gaze = gaze

        return AttentionState(
            mode=mode, target_id=self.target_id, glance_id=self.glance_id, focus_id=focus,
            gaze=gaze, n_faces=n, idle_level=self.idle_level,
            proximity=cur.h if cur is not None else 0.0, speaker_id=self.speaker_id,
            scores=scores, events=ev)

    def _set_target(self, tid: int) -> None:
        self.target_id = tid
        self.target_hold = 0.0
        self.target_lost_t = 0.0
        self.challenger, self.challenger_t = None, 0.0
        self.glance_id = None

    def _idle_gaze(self, now: float) -> Tuple[float, float]:
        if self.idle_level == 2:
            return (0.0, 0.30)
        if now >= self._next_wander:
            r = self.rng
            if r.random() < 0.25:
                self._wander = (r.uniform(-0.2, 0.2), r.uniform(-0.1, 0.1))
            else:
                self._wander = (r.uniform(-0.85, 0.85), r.uniform(-0.45, 0.35))
            self._next_wander = now + r.uniform(*self.cfg.wander_interval)
        if self.idle_level == 1:
            return (self._wander[0] * 0.4, 0.20)
        return self._wander
