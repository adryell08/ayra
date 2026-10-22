"""Pipeline completo, sem entrada/saída: detecções entram, matriz de LEDs sai.

    detecções -> FaceTracker -> AttentionManager -> GazeController ─┐
                                      │ eventos                      ├─> face.render_field -> LEDs
                                      └─> ExpressionEngine ─────────┘

Usado igualmente pelo `main.py` (janela) e pelo `selftest.py` (testes sem câmera).
Quando a IA existir, ela conversa com este objeto: `set_mood`, `pulse`,
`attention.set_speaker`, `attention.look_at`.
"""
from __future__ import annotations

import random
from collections import deque
import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

from attention import AttentionConfig, AttentionManager, AttentionState
from expressions import ExpressionEngine
from actions import ACTIONS, ActionPlayer
from face import render_field
from gaze import GazeController
from perception import VisualReactor
from gestures import HandReactor, ObjectReactor
from tracker import Detection, FaceTracker, Track

REFLEX_GROWTH_SPEED = 0.60       # altura normalizada por segundo, no mesmo ID
REFLEX_SAMPLE_MAX_AGE = 0.20     # não deriva tamanho através de oclusões/pausas
REFLEX_NEAR_HYSTERESIS = 0.04


@dataclass
class RobotFrame:
    alpha: np.ndarray            # (H, W) brilho de cada LED, 0..1
    glint: np.ndarray            # (H, W) brilho do "reflexo" nos olhos
    shown: str                   # expressão exibida agora
    mood: str                    # humor escolhido ("auto" ou fixo)
    gaze: Tuple[float, float]    # olhar atual (-1..1)
    attention: AttentionState
    tracks: List[Track]


class Robot:
    def __init__(self, grid: Tuple[int, int] = (80, 45), reduce_motion: bool = False,
                 seed: Optional[int] = None, cfg: Optional[AttentionConfig] = None, sensitivity=1.0):
        self.W, self.H = grid
        rng = random.Random(seed)
        self.rng = rng
        self.sensitivity = sensitivity
        self.tracker = FaceTracker(min_hits=2)
        self.attention = AttentionManager(cfg, seed=rng.random())
        self.gaze = GazeController(reduce_motion, random.Random(rng.random()))
        self.expr = ExpressionEngine(reduce_motion, random.Random(rng.random()))
        self.reduce_motion = reduce_motion
        self.last: Optional[RobotFrame] = None
        self._face_sizes = {}       # ID -> (instante do quadro novo, altura suavizada)
        self._near_faces = set()
        self._growth_runs = {}
        self._smile_baselines = {}
        self.actions = ActionPlayer(seed)
        self._pupil = 0.42          # razão pupila/íris (suavizada)
        self._near = 0.0            # convergência (rosto muito perto)
        self._ambient = 0.5         # luz ambiente 0..1 (brilho médio da câmera)
        self.now_hint = 0.0
        self._mouth_history = {}    # ID -> [(instante, largura relativa da boca)]
        self._speaking_until = {}   # ID -> até quando considerar fala
        self.last_trigger = None
        self.trigger_history = deque(maxlen=32)  # somente eventos numéricos, nunca pixels
        self.trigger_sequence = 0
        from preferences import DEFAULT_FEATURES
        self.features = dict(DEFAULT_FEATURES)
        self.perception = VisualReactor(sensitivity)
        self.hands = HandReactor(sensitivity)
        self.objects = ObjectReactor(sensitivity)

    # ---- controles públicos (teclado hoje, IA amanhã) ------------------------
    def set_mood(self, name: str) -> None:
        self.expr.set_mood(name)

    def play(self, name: str, seconds=None) -> bool:
        """Inicia uma simulação do catálogo (speak, cry...). False se o nome for desconhecido."""
        return self.actions.play(name, self.now_hint, seconds)

    def stop(self, name=None) -> None:
        self.actions.stop(name)

    def set_speech_level(self, level: float) -> None:
        self.actions.set_speech_level(level, self.now_hint)

    def set_ambient(self, level) -> None:
        """Luz do ambiente (0 escuro .. 1 muito claro), p.ex. brilho médio da câmera/255. Só contrai a pupila em humor neutro."""
        if level is not None and math.isfinite(level):
            self._ambient = max(0.0, min(1.0, float(level)))

    def _iris_dynamics(self, att, shown, dt):
        """Pupila: emoção sobrepõe luz. Convergência: rosto próximo aproxima as íris do nariz."""
        emotion = {"happy": .52, "surprised": .72, "wide": .74, "sad": .55, "angry": .28, "sleepy": .34, "sleeping": .2}
        if shown in emotion:
            target = emotion[shown]
        else:
            bright = max(0.0, min(1.0, (self._ambient - .45) / .4))
            target = .50 - .26 * bright + (.10 * (1 - self._ambient / .45) if self._ambient < .45 else 0.0)
        k = min(1.0, max(dt, 0.0) * 5.0)
        self._pupil += (target - self._pupil) * k
        near = max(0.0, min(1.0, (att.proximity - .15) / .35))
        self._near += (near - self._near) * min(1.0, max(dt, 0.0) * 4.0)
        return {"pupil": self._pupil, "near": self._near}

    def set_reduce_motion(self, on: bool) -> None:
        self.reduce_motion = on
        self.gaze.reduce_motion = on
        self.expr.set_reduce_motion(on)

    def blink(self, *, reflex: bool = False) -> bool:
        if reflex and not self.features["reflex"]:
            return False
        return self.expr.blink_now(0, reflex=reflex)

    def wink(self) -> None:
        self.expr.blink_now(1)

    def set_feature(self, name, enabled):
        if name not in self.features:
            raise ValueError("Reação desconhecida")
        self.features[name] = bool(enabled)
        if name == "auto_blink":
            self.expr.auto_blink = bool(enabled)
        elif name == "hands":
            self.hands = HandReactor(self.sensitivity)
        elif name == "objects":
            self.objects = ObjectReactor(self.sensitivity)
        elif name in ("tongue", "smile"):
            kind = "tongue_out" if name == "tongue" else "smile"
            self.perception._states.pop(kind, None)
            self.perception.last_scores.pop(kind, None)
        elif name == "reflex":
            self._face_sizes.clear()
            self._near_faces.clear()
            self._growth_runs.clear()
            self.expr._recoil_left = 0.
        if not enabled and self.last_trigger:
            kinds = {"hands": {"wave", "palm_hello", "hand_threat"}, "tongue": {"tongue_out"},
                     "smile": {"smile", "smile_landmarks"}, "objects": {"dog", "cat", "bottle", "cup"},
                     "reflex": {"face_reflex", "too_close", "hand_threat"}}
            if self.last_trigger["kind"] in kinds.get(name, set()):
                self.expr._pulses.clear()

    # ---- um passo de simulação -----------------------------------------------
    def step(self, dets: Optional[List[Detection]], now: float, dt: float) -> RobotFrame:
        self.now_hint = now
        tev = self.tracker.update(dets, now) if dets is not None else self.tracker.tick(now)
        att = self.attention.update(self.tracker.tracks, now, dt, tev)
        if dets is not None and self.features["reflex"]:
            self._approach_reflex(now)
        self._react(att, now)
        if dets is not None:
            scores = {}
            landmark_smile = False
            observed = {"tongue_out"}
            for detection in dets:
                matched = next((tr for tr in self.tracker.tracks if tr.confirmed and tr.last_seen == now
                                and abs(tr.cx - detection.cx) < tr.w * 0.5
                                and abs(tr.cy - detection.cy) < tr.h * 0.5), None)
                if matched is not None:
                    speaking = self._speech_gate(matched.id, detection.cues.get("mouth_ratio"), now)
                    for kind, score in detection.cues.items():
                        if kind == "mouth_ratio":
                            continue
                        if speaking and kind in ("tongue_out", "smile_landmarks", "smile") and score < .93:
                            score = 0.0                               # falando: boca em movimento não é língua nem sorriso
                        if (kind == "tongue_out" and not self.features["tongue"]) or (kind in ("smile", "smile_landmarks") and not self.features["smile"]):
                            continue
                        if kind == "smile_landmarks":
                            landmark_smile = True
                            kind, score = "smile", self._landmark_smile(matched.id, score, now)
                        scores[kind] = max(scores.get(kind, 0.0), score)
                        observed.add(kind)
            observations = getattr(dets, "observations", None)
            fresh = getattr(dets, "observed_kinds", None)
            if self.features["smile"] and observations is not None and fresh is not None and "smile" in fresh:
                landmark_smile = False
                scores["smile"] = max((x.score for x in observations if x.kind == "smile"), default=0.)
                observed.add("smile")
            for event in self.perception.update(scores, now, observed):
                if event.kind == "smile" and self.perception.last_scores.get("tongue_out", 0.) >= .6 / self.sensitivity:
                    continue
                reaction = self.perception.reactions.get(event.kind)
                if reaction:
                    self.expr.pulse(*reaction)
                    self._record_trigger("smile_landmarks" if event.kind == "smile" and landmark_smile else event.kind,
                                         event.score, now)
            if observations is not None:
                hand_events = self.hands.update(observations, now) if self.features["hands"] and (fresh is None or "hand" in fresh) else []
                for event in hand_events:
                    if event.kind in ("wave", "palm_hello"):
                        if self.perception.last_scores.get("tongue_out", 0.) < .6 / self.sensitivity:
                            self.expr.pulse("happy", 1.6)
                            self._record_trigger(event.kind, event.score, now)
                    elif event.kind == "hand_threat" and self.features["reflex"]:
                        if self.blink(reflex=True):
                            self.expr.recoil_now()
                            self._record_trigger(event.kind, event.score, now)
                if self.features["objects"] and (fresh is None or set(fresh) & {"dog", "cat", "bottle", "cup"}):
                    for event in self.objects.update(observations, now):
                        if self.perception.last_scores.get("tongue_out", 0.) < .6 / self.sensitivity:
                            self.expr.pulse("happy" if event.kind in ("dog", "cat") else "wide", 1.6)
                            self._record_trigger(event.kind, event.score, now)

        self.gaze.set_target(*att.gaze)
        gp = self.gaze.update(dt)
        if self.gaze.consume_big_saccade() and self.features["auto_blink"] and self.rng.random() < 0.35:
            self.expr.blink_now(0)               # piscar durante sacadas grandes é natural

        self.expr.set_idle_level(att.idle_level)
        self.expr.update(dt)
        render_args = (self.expr.visual_pose(), self.expr.blink, gp, self.gaze.motion_scale, self.expr.zzz,
                       self.features, self.expr.shown_name(),
                       {**self._glint_dynamics(gp, att, dt), **self.actions.update(now, dt, self, self.reduce_motion),
                        "t": 0.0 if self.reduce_motion else now,
                        **self._iris_dynamics(att, self.expr.shown_name(), dt)})
        self.last_render_args = render_args          # o painel físico (Arduino) redesenha o mesmo instante
        alpha, glint = render_field(self.W, self.H, *render_args)
        self.last = RobotFrame(alpha, glint, self.expr.shown_name(), self.expr.mood, gp, att,
                               list(self.tracker.tracks))
        return self.last

    def _glint_dynamics(self, gp, att, dt):
        """Velocidade suavizada do olhar e pulso de troca de foco para o reflexo (catchlight)."""
        prev = getattr(self, "_glint_prev", gp)
        vel = getattr(self, "_glint_vel", (0.0, 0.0))
        if dt > 1e-4:
            vx, vy = (gp[0] - prev[0]) / dt, (gp[1] - prev[1]) / dt
            vel = (vel[0] * 0.7 + max(-4.0, min(4.0, vx)) * 0.3, vel[1] * 0.7 + max(-4.0, min(4.0, vy)) * 0.3)
        pulse = getattr(self, "_glint_pulse", 0.0) * (0.0 if dt > 5 else 2.718281828 ** (-dt / 0.35))
        if any(e.kind in ("switch", "glance", "new_face", "greet", "wake") for e in att.events):
            pulse = 1.0
        self._glint_prev, self._glint_vel, self._glint_pulse = gp, vel, pulse
        if self.reduce_motion:
            return {"vx": 0.0, "vy": 0.0, "pulse": 0.0}
        return {"vx": vel[0], "vy": vel[1], "pulse": pulse}

    def _record_trigger(self, kind, score, now):
        self.trigger_sequence += 1
        self.last_trigger = dict(kind=kind, score=float(score), time=now,
                                 sequence=self.trigger_sequence)
        self.trigger_history.append(dict(self.last_trigger))

    def _speech_gate(self, track_id, ratio_cue, now):
        """Fala = largura da boca oscilando rápido. Sorriso/língua são estáveis; durante a fala são suprimidos."""
        if ratio_cue is None:
            return now < self._speaking_until.get(track_id, 0.0)
        history = self._mouth_history.setdefault(track_id, [])
        history.append((now, float(ratio_cue) * 2.0))
        while history and now - history[0][0] > 0.9:
            history.pop(0)
        if len(history) >= 6:
            values = [v for _, v in history]
            mean = sum(values) / len(values)
            deviation = (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5
            turns = sum(1 for a, b, c in zip(values, values[1:], values[2:]) if (b - a) * (c - b) < 0)
            if deviation >= 0.04 and turns >= 2:
                self._speaking_until[track_id] = now + 0.8
        active = {tr.id for tr in self.tracker.tracks if tr.confirmed}
        for table in (self._mouth_history, self._speaking_until):
            for key in [k for k in table if k not in active]:
                del table[key]
        return now < self._speaking_until.get(track_id, 0.0)

    def _landmark_smile(self, track_id, score, now):
        """Geometria não é confiança FER: exige mudança sobre o rosto neutro e confirmação."""
        if not math.isfinite(score):
            return 0.
        state = self._smile_baselines.setdefault(track_id, [now, score])
        state[1] = min(state[1], score)
        active_ids = {tr.id for tr in self.tracker.tracks if tr.confirmed}
        self._smile_baselines = {key: value for key, value in self._smile_baselines.items() if key in active_ids}
        if now - state[0] < .6 or score < max(.70, state[1] + .18):
            return 0.
        return min(score, .89)  # fallback passa pela confirmação fraca, nunca pelo atalho FER

    def _approach_reflex(self, now: float) -> None:
        """Somente medidas novas do mesmo rosto; distância é altura, não metros."""
        samples = {}
        near = set()
        trigger = False
        trigger_score = 0.
        runs = {}
        close_size = self.attention.cfg.too_close_size
        for tr in self.tracker.tracks:
            if not tr.confirmed or tr.last_seen != now:
                continue
            previous = self._face_sizes.get(tr.id)
            samples[tr.id] = (now, tr.h)
            was_near = tr.id in self._near_faces
            is_near = tr.h > close_size or (was_near and tr.h >= close_size - REFLEX_NEAR_HYSTERESIS)
            if is_near:
                near.add(tr.id)
            fast = False
            if previous is not None:
                age = now - previous[0]
                if 1e-3 <= age <= REFLEX_SAMPLE_MAX_AGE:
                    delta = tr.h - previous[1]
                    speed = delta / age
                    count, growth = self._growth_runs.get(tr.id, (0, 0.))
                    count, growth = (count + 1, growth + delta) if speed >= .45 / self.sensitivity else (0, 0.)
                    runs[tr.id] = (count, growth)
                    fast = (speed >= 1.0 and delta >= .025) or (count >= 2 and growth >= .035)
                    if fast:
                        trigger_score = max(trigger_score, speed)
            trigger |= fast or (is_near and not was_near)
            if is_near and not was_near:
                trigger_score = max(trigger_score, tr.h)
        self._face_sizes, self._near_faces = samples, near
        self._growth_runs = runs
        if trigger and self.blink(reflex=True):
            self._record_trigger("face_reflex", trigger_score, now)

    def _react(self, att: AttentionState, now=0.) -> None:
        for e in att.events:
            if e.kind == "too_close" and not self.features["reflex"]:
                continue
            self._record_trigger(e.kind, att.proximity if e.kind == "too_close" else att.scores.get(e.track_id, 0.), now)
            if e.kind == "new_face":
                self.expr.pulse("wide", 0.9)
            elif e.kind == "greet":
                self.expr.pulse_seq([("wide", 0.35), ("happy", 1.4)])
            elif e.kind == "wake":
                self.expr.pulse_seq([("surprised", 0.8), ("happy", 1.6)])
            elif e.kind == "switch" and self.features["auto_blink"]:
                self.expr.blink_now(0)
            elif e.kind == "glance_end" and self.features["auto_blink"] and self.rng.random() < 0.4:
                self.expr.blink_now(0)
            elif e.kind == "too_close" and self.features["reflex"] and not self.reduce_motion:
                self.expr.pulse("surprised", 1.0)
