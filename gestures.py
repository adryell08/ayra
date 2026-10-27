"""Gestos por trajetória de uma palma; tempo e medidas injetados, sem pixels."""
from collections import deque
import math
from perception import VisualEvent


class HandReactor:
    def __init__(self, sensitivity=1.0):
        self.sensitivity = sensitivity
        self.history = deque(maxlen=64)
        self._threat_until = self._wave_until = -float("inf")
        self._near = False
        self._missing_since = None
        self._hello_latched = False

    def update(self, hands, now):
        hands = [h for h in hands if h.kind == "hand" and h.score >= .45 / self.sensitivity
                 and all(math.isfinite(v) for v in (h.score, h.x, h.y, h.size))]
        if not hands:
            self.history.clear()
            if self._missing_since is None:
                self._missing_since = now
            if now - self._missing_since >= .3:
                self._near = self._hello_latched = False
            return []
        self._missing_since = None
        previous = self.history[-1] if self.history else None
        if previous is not None and now - previous[0] <= .35:
            hand = min(hands, key=lambda h: abs(h.x - previous[1].x) + abs(h.y - previous[1].y))
            if abs(hand.x - previous[1].x) + abs(hand.y - previous[1].y) > .5:
                previous = None
        else:
            hand, previous = max(hands, key=lambda h: h.size), None
        if previous is None:
            self.history.clear()
        speed = 0 if previous is None else (hand.size - previous[1].size) / max(now - previous[0], .001)
        limit = .34 / self.sensitivity ** .5
        near = hand.size > limit or (self._near and hand.size >= limit - .07)
        threat = (near and not self._near) or (speed > .60 / self.sensitivity and hand.size > .20)
        self.history.append((now, hand))
        while self.history and now - self.history[0][0] > 1.4:
            self.history.popleft()
        strong = hand.score >= .85
        confirmed = strong or (len(self.history) >= 3 and now - self.history[0][0] >= .18)
        if confirmed:
            self._near = near
        if threat and confirmed and now >= self._threat_until:
            self._threat_until = now + 2.5
            self.history.clear()
            return [VisualEvent("hand_threat", hand.score)]
        if (not near and not self._hello_latched and now >= self._wave_until
                and len(self.history) >= (2 if strong else 4)
                and now - self.history[0][0] >= (.12 if strong else .35)
                and max(h.x for _, h in self.history) - min(h.x for _, h in self.history) < .05):
            self._wave_until = now + 4.
            self._hello_latched = True
            self.history.clear()
            return [VisualEvent("palm_hello", hand.score)]
        if near or now < self._wave_until or len(self.history) < 4:
            return []
        positions = [h.x for _, h in self.history]
        # Ignora tremor <3% do quadro; aceno exige ida, volta e nova ida.
        turns, direction, anchor = 0, 0, positions[0]
        for x in positions[1:]:
            if abs(x - anchor) < .03:
                continue
            new_direction = 1 if x > anchor else -1
            if direction and new_direction != direction:
                turns += 1
            direction, anchor = new_direction, x
        if (turns >= 2 and max(positions) - min(positions) >= .16
                and now - self.history[0][0] >= .45):
            self._wave_until = now + 4.0
            self.history.clear()
            return [VisualEvent("wave", hand.score)]
        return []


SIGNS = ("middle_finger", "thumbs_up", "peace", "ok_sign")   # gestos que a Ayra reconhece e devolve


def classify_hand(points):
    """Classifica 21 pontos da mão (MediaPipe) em coordenadas da imagem (y para baixo).

    Dedos: só distâncias (vale para mão girada ou espelhada). Joinha usa a direção do polegar (para cima).
    Retorna (rótulo, confiança 0..1): middle_finger, peace, ok_sign, thumbs_up, fist, open, other.
    """
    import numpy as np
    p = np.asarray(points, dtype=float)[:, :2]
    wrist = p[0]
    scale = float(np.linalg.norm(p[9] - wrist)) or 1.0
    dist = lambda a, b: float(np.linalg.norm(p[a] - p[b]))

    def finger(mcp, pip, dip, tip):
        path = dist(pip, mcp) + dist(dip, pip) + dist(tip, dip)
        straight = dist(tip, mcp) / max(path, 1e-6)                  # 1 = reto, ~0.3 = dobrado
        reach = (dist(tip, 0) - dist(pip, 0)) / scale
        return straight, reach

    idx, mid, ring, pinky = (finger(*range(b, b + 4)) for b in (5, 9, 13, 17))
    extended = lambda f: f[0] > .80 and f[1] > .25
    folded = lambda f: f[0] < .72 or f[1] < .05
    thumb_path = dist(3, 2) + dist(4, 3)
    thumb_out = (dist(4, 2) / max(thumb_path, 1e-6) > .85 and dist(4, 5) / scale > .45 and dist(4, 0) > dist(3, 0))
    if extended(mid) and all(folded(f) for f in (idx, ring, pinky)):
        others = max(dist(t, 0) for t in (8, 16, 20))
        lead = dist(12, 0) / max(others, 1e-6)
        if lead > 1.25:
            margin = min(mid[0] - .80, mid[1] - .25, *(max(.72 - f[0], .05 - f[1]) for f in (idx, ring, pinky)))
            return "middle_finger", float(min(1.0, .7 + margin + .15 * (lead - 1.25)))
    if extended(idx) and extended(mid) and folded(ring) and folded(pinky):
        return "peace", .85
    if dist(4, 8) / scale < .30 and all(extended(f) for f in (mid, ring, pinky)):
        return "ok_sign", .8
    if all(folded(f) for f in (idx, mid, ring, pinky)):
        v = p[4] - p[2]
        if thumb_out and v[1] < -.6 * float(np.linalg.norm(v)):      # polegar para cima (y da imagem cresce para baixo)
            return "thumbs_up", .85
        return "fist", .8
    if all(extended(f) for f in (idx, mid, ring, pinky)):
        return "open", .8
    return "other", .5


class SignReactor:
    """Confirma cada gesto (2 leituras em 0,8 s) e espera 6 s antes de reagir de novo ao mesmo gesto."""
    def __init__(self, sensitivity=1.0, kinds=SIGNS):
        self.sensitivity = sensitivity
        self.kinds = tuple(kinds)
        self.hits = {k: deque() for k in self.kinds}
        self.until = {k: -float("inf") for k in self.kinds}

    def update(self, observations, now, allowed=None):
        events = []
        for kind in self.kinds:
            hits = self.hits[kind]
            seen = [o for o in observations if o.kind == kind and o.score >= .5 / self.sensitivity]
            if seen:
                hits.append((now, max(o.score for o in seen)))
            while hits and now - hits[0][0] > .8:
                hits.popleft()
            if len(hits) >= 2 and now >= self.until[kind] and (allowed is None or kind in allowed):
                events.append(VisualEvent(kind, max(s for _, s in hits)))
                self.until[kind] = now + 6.0
                hits.clear()
        return events


class ObjectReactor:
    def __init__(self, sensitivity=1.0):
        self.threshold = .28 / sensitivity
        self._states = {}

    def update(self, observations, now):
        events = []
        for kind in ("dog", "cat", "bottle", "cup"):
            score = max((x.score for x in observations if x.kind == kind and math.isfinite(x.score)), default=0.)
            state = self._states.setdefault(kind, dict(first=None, last=None, count=0,
                                                       release=None, until=-float("inf"), active=False))
            if score < self.threshold:
                state.update(first=None, count=0)
                if state["release"] is None:
                    state["release"] = now
                if now - state["release"] >= .5:
                    state["active"] = False
            else:
                state["release"] = None
                if state["last"] is None or now - state["last"] > 1.2 or state["first"] is None:
                    state["first"] = now
                    state["count"] = 0
                state["count"] += 1
                confirmed = score >= .85 or (state["count"] >= 3 and now - state["first"] >= .7)
                if not state["active"] and confirmed and now >= state["until"]:
                    events.append(VisualEvent(kind, score))
                    state.update(active=True, until=now + 5.)
            state["last"] = now
        return events
