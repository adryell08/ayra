"""Tempos numéricos do quadro recebido até flip; nenhuma imagem sai do detector."""
from __future__ import annotations

from collections import deque
import math
import time


class DetectionBatch(list):
    """Lista compatível com CameraSource.poll, com tempos do mesmo quadro."""
    def __init__(self, detections, received_at, detected_at):
        super().__init__(detections)
        self.received_at = received_at
        self.detected_at = detected_at
        self.observations = getattr(detections, "observations", None)
        self.perception_status = getattr(detections, "perception_status", "")
        self.observed_kinds = getattr(detections, "observed_kinds", None)


class TimedDetector:
    def __init__(self, detector, clock=time.perf_counter):
        self.detector = detector
        self.clock = clock
        self.name = detector.name

    def detect(self, frame):
        # O quadro já foi lido: sensor, exposição e buffer anteriores ficam de fora.
        received = self.clock()
        detections = self.detector.detect(frame)
        return DetectionBatch(detections, received, self.clock())

    def reset(self):
        if hasattr(self.detector, "reset"):
            self.detector.reset()

    def close(self):
        if hasattr(self.detector, "close"):
            self.detector.close()

    def module_statuses(self):
        return self.detector.module_statuses() if hasattr(self.detector, "module_statuses") else {}

    def set_feature(self, name, enabled):
        if hasattr(self.detector, "set_feature"):
            self.detector.set_feature(name, enabled)


class FrameMetrics:
    def __init__(self):
        self.samples = deque(maxlen=120)
        self.reset()

    def reset(self):
        self.samples.clear()
        self.pending = None
        self.received_at = None
        self.latency_ms = None
        self.detection_ms = None

    def accept(self, detections):
        if isinstance(detections, DetectionBatch):
            self.pending = detections

    def presented(self, now):
        batch, self.pending = self.pending, None
        if batch is not None:
            self.received_at = batch.received_at
            self.detection_ms = max(0, (batch.detected_at - batch.received_at) * 1000)
            self.latency_ms = max(0, (now - batch.received_at) * 1000)
            self.samples.append(self.latency_ms)

    @property
    def p95_ms(self):
        if not self.samples:
            return None
        values = sorted(self.samples)
        return values[math.ceil(len(values) * 0.95) - 1]
