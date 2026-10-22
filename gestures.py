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
