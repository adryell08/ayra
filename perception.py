"""Sinais visuais locais: somente escores numéricos saem da captura.

Língua: heurística experimental de cor/geometria abaixo dos cantos da boca
localizados pelo YuNet. Não confunde boca aberta com um blendshape tongueOut:
esse coeficiente não está na lista de 52 categorias do MediaPipe.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np


def tongue_out_score(image, face, sensitivity=1.0) -> float:
    """Espera BGR e uma linha YuNet no mesmo espaço de pixels. Não retém ROI.

    Cor sozinha não basta: componente central, compacto e prolongado abaixo da
    boca. Iluminação, batom, barba e resolução podem afetar esta heurística.
    """
    import cv2
    if image is None or image.ndim != 3 or len(face) < 15:
        return 0.0
    if not np.isfinite(face[:15]).all():
        return 0.0
    fh = float(face[3])
    mx, my = (float(face[10]) + float(face[12])) / 2, (float(face[11]) + float(face[13])) / 2
    mw = abs(float(face[12]) - float(face[10]))
    if fh < 48 or mw < 10 or abs(float(face[11]) - float(face[13])) > mw * 0.45:
        return 0.0
    ih, iw = image.shape[:2]
    x0, x1 = max(0, int(mx - mw * 0.62)), min(iw, int(mx + mw * 0.62) + 1)
    y0, y1 = max(0, int(my + fh * 0.025)), min(ih, int(my + fh * 0.25) + 1)
    if x1 <= x0 or y1 <= y0 or y1 - y0 < fh * 0.12:
        return 0.0
    roi = image[y0:y1, x0:x1]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    b, g, r = (roi[..., i].astype(np.int16) for i in range(3))
    pink = (((hsv[..., 0] <= 12) | (hsv[..., 0] >= 160)) & (hsv[..., 1] >= 65 / sensitivity)
            & (hsv[..., 2] >= 40) & (r - g > 25 / sensitivity) & (r - b > 15 / sensitivity)).astype(np.uint8)
    count, _, stats, centers = cv2.connectedComponentsWithStats(pink, connectivity=8)
    best = 0.0
    for i in range(1, count):
        x, y, w, h, area = stats[i]
        extent = (y0 + y + h - my) / fh
        if (0.18 * mw <= w <= 1.15 * mw and h >= 0.04 * fh / sensitivity and extent >= 0.075 / sensitivity
                and y <= 0.08 * fh and abs(x0 + centers[i][0] - mx) <= 0.30 * mw
                and area >= 0.45 * w * h and h / max(w, 1) >= 0.25 / sensitivity):
            best = max(best, min(1.0, 0.60 + (extent - 0.075) * 3.5))
    return float(best)


def mouth_ratio(face) -> float:
    """Largura da boca / largura entre os olhos (YuNet). Oscila rápido na fala; é estável no sorriso."""
    if len(face) < 15 or not np.isfinite(face[:15]).all():
        return 0.
    eye_width = math.hypot(float(face[6] - face[4]), float(face[7] - face[5]))
    if eye_width < 14:
        return 0.
    return math.hypot(float(face[12] - face[10]), float(face[13] - face[11])) / eye_width


def smile_landmark_score(face) -> float:
    """Fallback explícito por abertura lateral da boca; não é FER nem emoção."""
    if len(face) < 15 or not np.isfinite(face[:15]).all():
        return 0.
    eye_width = math.hypot(float(face[6] - face[4]), float(face[7] - face[5]))
    mouth_width = math.hypot(float(face[12] - face[10]), float(face[13] - face[11]))
    if eye_width < 14:
        return 0.
    return float(np.clip((mouth_width / eye_width - .75) / .40, 0., 1.))


@dataclass(frozen=True)
class VisualEvent:
    kind: str
    score: float


class VisualReactor:
    """Confirma sinais e limita reações usando somente o tempo injetado."""
    def __init__(self, sensitivity=1.0):
        self.sensitivity = sensitivity
        self.last_scores = {}
        self.reactions = {"tongue_out": ("angry", 1.4), "smile": ("happy", 1.6)}
        self.cooldowns = {"tongue_out": 4.0, "smile": 3.0}
        self._states = {}
        self.last_events = []

    def update(self, scores, now, observed=None):
        events = []
        for kind in self.reactions:
            if observed is not None and kind not in observed:
                continue
            state = self._states.setdefault(kind, dict(first=None, last=None, count=0,
                                                       latched=False, release=None, until=-math.inf))
            score = float(scores.get(kind, 0.0))
            if not math.isfinite(score):
                score = 0.0
            self.last_scores[kind] = score
            if state["last"] is not None and now - state["last"] > 0.35:
                state.update(first=None, count=0, release=None)
            if score < min(.65, .40 / self.sensitivity):
                state.update(first=None, count=0)
                if state["release"] is None:
                    state["release"] = now
                if now - state["release"] >= .12:
                    state["latched"] = False
            elif score >= min(.90, (.55 if kind == "smile" else .60) / self.sensitivity):
                state["release"] = None
                if state["first"] is None:
                    state["first"] = now
                state["count"] += 1
                confirmed = score >= .94 or (state["count"] >= 3 and now - state["first"] >= (.30 if kind == "smile" else .18))
                if (not state["latched"] and confirmed and now >= state["until"]):
                    events.append(VisualEvent(kind, min(score, 1.0)))
                    state.update(latched=True, until=now + self.cooldowns[kind])
            else:
                # A faixa de histerese conserva a trava, mas não soma confirmação.
                state.update(first=None, count=0, release=None)
            state["last"] = now
        self.last_events = events
        return events
