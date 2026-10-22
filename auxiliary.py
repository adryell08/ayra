"""Inferência auxiliar em CPU/worker; fila limitada, sem imagens em resultados.

Decodificação dos tensores baseada na arquitetura MediaPipe Palm Detection e no
exemplo OpenCV Zoo (Apache-2.0): models/palm_detection_mediapipe/mp_palmdet.py.
"""
from __future__ import annotations
from dataclasses import dataclass
import threading
import time
import numpy as np

from preparar_modelos import model_path, valid_model


@dataclass(frozen=True)
class Observation:
    kind: str
    score: float
    x: float
    y: float
    size: float


class ObservedBatch(list):
    def __init__(self, faces=(), observations=None, status="", observed_kinds=None):
        super().__init__(faces)
        self.observations = observations
        self.perception_status = status
        self.observed_kinds = {"hand"} if observed_kinds is None else set(observed_kinds)


def load_net(path):
    import cv2
    cv2.setNumThreads(1)  # modelos pequenos: evita disputar todos os núcleos com o render
    # Leitura por buffer evita o problema já confirmado de acentos no Windows.
    net = cv2.dnn.readNetFromONNX(np.frombuffer(path.read_bytes(), dtype=np.uint8))
    net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
    return net


class PalmDetector:
    def __init__(self, net=None, mirror=True, threshold=.45):
        self.net = load_net(model_path("hands")) if net is None else net
        self.mirror = mirror
        self.threshold = threshold
        self.anchors = np.array([((x + .5) / side, (y + .5) / side)
                                 for side, repeats in ((24, 2), (12, 6))
                                 for y in range(side) for x in range(side)
                                 for _ in range(repeats)], dtype=np.float32)

    def infer(self, image):
        import cv2
        h, w = image.shape[:2]
        scale = 192 / max(h, w)
        rw, rh = round(w * scale), round(h * scale)
        left, top = (192 - rw) // 2, (192 - rh) // 2
        padded = np.zeros((192, 192, 3), dtype=np.uint8)
        padded[top:top + rh, left:left + rw] = cv2.resize(image, (rw, rh))
        blob = padded[..., ::-1].astype(np.float32)[None] / 255
        self.net.setInput(np.ascontiguousarray(blob))
        outputs = self.net.forward(self.net.getUnconnectedOutLayersNames())
        boxes = next(x.reshape(-1, 18) for x in outputs if x.shape[-1] == 18)
        logits = next(x.reshape(-1) for x in outputs if x.shape[-1] == 1)
        return self.decode(boxes, logits, w, h, scale, left, top)

    def decode(self, boxes, logits, w, h, scale, left, top):
        import cv2
        scores = 1 / (1 + np.exp(-np.clip(logits, -50, 50)))
        self.last_score = float(scores.max())
        self.last_class = "hand" if self.last_score >= self.threshold else "none"
        indices = np.flatnonzero(scores >= self.threshold)
        if not len(indices):
            return []
        raw = boxes[indices]
        centers = (raw[:, :2] + self.anchors[indices] * 192 - [left, top]) / scale
        sizes = np.maximum(raw[:, 2:4] / scale, 0)
        rects = np.column_stack((centers - sizes / 2, sizes))
        keep = np.asarray(cv2.dnn.NMSBoxes(rects.tolist(), scores[indices].tolist(), self.threshold, .30)).reshape(-1)
        result = []
        for j in keep[:2]:
            cx, cy = centers[j] / [w, h]
            size = sizes[j, 1] / h
            if not (0 <= cx <= 1 and 0 <= cy <= 1 and .03 < size < 1.5):
                continue
            result.append(Observation("hand", float(scores[indices[j]]),
                                      float(1 - cx if self.mirror else cx), float(cy), float(size)))
        return result


class ObjectDetector:
    labels = {0: "person", 15: "cat", 16: "dog", 39: "bottle", 41: "cup"}
    interested = {15, 16, 39, 41}

    def __init__(self, net=None, mirror=True, threshold=.28):
        self.net = load_net(model_path("objects")) if net is None else net
        self.mirror, self.threshold = mirror, threshold
        self.last_score, self.last_class = 0., "none"

    def infer(self, image):
        import cv2
        h, w = image.shape[:2]
        scale = 416 / max(h, w)
        rw, rh = round(w * scale), round(h * scale)
        left, top = (416 - rw) // 2, (416 - rh) // 2
        padded = np.zeros((416, 416, 3), np.uint8)
        padded[top:top + rh, left:left + rw] = cv2.resize(image, (rw, rh))
        normalized = (padded.astype(np.float32) - [103.53, 116.28, 123.675]) / [57.375, 57.12, 58.395]
        self.net.setInput(np.ascontiguousarray(normalized.transpose(2, 0, 1)[None], dtype=np.float32))
        outputs = self.net.forward(self.net.getUnconnectedOutLayersNames())
        return self.decode(outputs, w, h, scale, left, top)

    def decode(self, outputs, w, h, scale, left, top):
        import cv2
        candidates = []
        self.last_class, self.last_score = "none", 0.
        # OpenCV 5 ordena nomes de saída de forma diferente. Este peso tem três
        # escalas; agrupar por forma evita depender da ordem/versão do engine.
        class_layers = {x.size // 80: x.reshape(-1, 80) for x in outputs if x.shape[-1] == 80}
        box_layers = {x.size // 32: x.reshape(-1, 4, 8) for x in outputs if x.shape[-1] == 32}
        if not class_layers or class_layers.keys() != box_layers.keys():
            raise ValueError("NanoDet: formas de saída incompatíveis (esperado classes=80, caixas=32)")
        for points, classes in class_layers.items():
            stride = next(s for s in (8, 16, 32, 64) if (416 // s) ** 2 == points)
            offsets = box_layers[points]
            ids, scores = classes.argmax(axis=1), classes.max(axis=1)
            if len(scores) and float(scores.max()) > self.last_score:
                best = int(scores.argmax())
                self.last_score = float(scores[best])
                self.last_class = self.labels.get(int(ids[best]), f"COCO#{ids[best]}")
            selected = np.flatnonzero((scores >= self.threshold) & np.isin(ids, list(self.interested)))
            side = 416 // stride
            for i in selected[np.argsort(scores[selected])[-100:]]:
                logits = offsets[i] - offsets[i].max(axis=1, keepdims=True)
                prob = np.exp(logits); prob /= prob.sum(axis=1, keepdims=True)
                distances = prob @ np.arange(8) * stride
                cx, cy = (i % side) * stride + (stride - 1) / 2, (i // side) * stride + (stride - 1) / 2
                x0, y0 = max(0., (cx - distances[0] - left) / scale), max(0., (cy - distances[1] - top) / scale)
                x1, y1 = min(float(w), (cx + distances[2] - left) / scale), min(float(h), (cy + distances[3] - top) / scale)
                if x1 > x0 and y1 > y0:
                    candidates.append((int(ids[i]), float(scores[i]), [x0, y0, x1 - x0, y1 - y0]))
        result = []
        for label in self.interested:
            rows = [c for c in candidates if c[0] == label]
            if not rows:
                continue
            keep = np.asarray(cv2.dnn.NMSBoxes([r[2] for r in rows], [r[1] for r in rows], self.threshold, .5)).reshape(-1)
            for i in keep[:4]:
                _, score, (x, y, bw, bh) = rows[i]
                cx = (x + bw / 2) / w
                result.append(Observation(self.labels[label], score, 1 - cx if self.mirror else cx,
                                          (y + bh / 2) / h, bh / h))
        return result


class SmileDetector:
    requires_faces = True
    # Pontos de alinhamento ArcFace/MobileFaceNet 112x112 usados pelo OpenCV Zoo.
    canonical = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
                          [41.5493, 92.3655], [70.7299, 92.2041]])

    def __init__(self, net=None, mirror=True, threshold=.55):
        self.net = load_net(model_path("smile")) if net is None else net
        self.mirror = mirror
        self.last_class, self.last_score = "none", 0.

    @classmethod
    def alignment(cls, points):
        x, y = points[:, 0], points[:, 1]
        matrix = np.vstack((np.column_stack((x, -y, np.ones(5), np.zeros(5))),
                            np.column_stack((y, x, np.zeros(5), np.ones(5)))))
        target = np.r_[cls.canonical[:, 0], cls.canonical[:, 1]]
        values, _, rank, _ = np.linalg.lstsq(matrix, target, rcond=None)
        if rank < 4:
            raise ValueError("FER: landmarks insuficientes para alinhamento")
        a, b, tx, ty = values
        return np.array([[a, -b, tx], [b, a, ty]], dtype=np.float32)

    def infer(self, image, faces=()):
        import cv2
        available = [f for f in faces if len(f.landmarks) == 5 and f.conf >= .6]
        if not available:
            self.last_class, self.last_score = "no-face", 0.
            return [Observation("smile", 0., .5, .5, 0.)]
        face = max(available, key=lambda f: f.h)
        h, w = image.shape[:2]
        points = np.array(face.landmarks, dtype=np.float64)
        if self.mirror:
            points[:, 0] = 1 - points[:, 0]
        points *= [w, h]
        aligned = cv2.warpAffine(image, self.alignment(points), (112, 112), borderMode=cv2.BORDER_REPLICATE)
        rgb = aligned[..., ::-1].astype(np.float32) / 127.5 - 1
        self.net.setInput(np.ascontiguousarray(rgb.transpose(2, 0, 1)[None]))
        raw = np.asarray(self.net.forward()).reshape(-1)
        if raw.size != 7 or not np.isfinite(raw).all():
            raise ValueError("FER: esperado vetor finito de sete classes")
        if np.all(raw >= 0) and np.all(raw <= 1) and abs(float(raw.sum()) - 1) < .01:
            probabilities = raw
        else:
            probabilities = np.exp(raw - raw.max()); probabilities /= probabilities.sum()
        labels = ("angry", "disgust", "fearful", "happy", "neutral", "sad", "surprised")
        self.last_class = labels[int(probabilities.argmax())]
        self.last_score = float(probabilities.max())
        self.happy_score = float(probabilities[3])
        return [Observation("smile", self.happy_score, face.cx, face.cy, face.h)]


class AuxiliaryWorker:
    """No máximo um quadro reduzido pendente + um em processamento, só em RAM.

    Resultados expiram em 350 ms. Pausa/troca invalida até inferências em curso;
    um detector lento nunca acumula quadros nem bloqueia a captura facial.
    """
    def __init__(self, detectors, clock=time.perf_counter, period=.12):
        self.detectors, self.clock = detectors, clock
        self.period = period
        self._lock = threading.Lock()
        self._wake, self._stop = threading.Event(), threading.Event()
        self._pending = self._result = None
        self._busy = False
        self._last = -float("inf")
        self._generation = 0
        self.error = ""
        self.inference_ms = None
        self.last_score, self.last_class = 0., "none"
        self._finished = None
        self._thread = threading.Thread(target=self._run, name="perception", daemon=True)
        if detectors:
            self._thread.start()

    def submit(self, image, faces=()):
        import cv2
        if image is None or not self.detectors:
            return
        now = self.clock()
        with self._lock:
            if self._stop.is_set() or self._busy or self._pending is not None or now - self._last < self.period:
                return
            h, w = image.shape[:2]
            width = min(w, 416)
            reduced = cv2.resize(image, (width, max(1, round(h * width / w))))
            self._pending = (self._generation, now, reduced, tuple(faces))
            self._last = now
            self._wake.set()

    def take(self):
        with self._lock:
            result, self._result = self._result, None
        if result is not None and self.clock() - result[0] <= .35:
            return result[1]
        return None

    def snapshot(self):
        with self._lock:
            return dict(loaded=bool(self.detectors), enabled=True, inference_ms=self.inference_ms,
                        score=self.last_score, label=self.last_class, error=self.error,
                        age=None if self._finished is None else max(0., self.clock() - self._finished))

    def reset(self):
        with self._lock:
            self._generation += 1
            self._pending = self._result = None
            self._last = -float("inf")

    def close(self):
        self._stop.set()
        self.reset()
        self._wake.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2)
        if not self._thread.is_alive():
            self.detectors.clear()

    def _run(self):
        while not self._stop.is_set():
            self._wake.wait(.1)
            self._wake.clear()
            with self._lock:
                job, self._pending = self._pending, None
                self._busy = job is not None
            if job is None:
                continue
            generation, received, image, faces = job
            result = []
            started = self.clock()
            error = ""
            try:
                for detector in self.detectors:
                    result.extend(detector.infer(image, faces) if getattr(detector, "requires_faces", False)
                                  else detector.infer(image))
            except Exception as exc:
                message = str(exc).splitlines()
                error = (message[-1] if message else type(exc).__name__)[:150]
            finally:
                del image, faces, job
            with self._lock:
                self.inference_ms = max(0., (self.clock() - started) * 1000)
                self._finished = self.clock()
                self.last_score = float(getattr(self.detectors[0], "last_score", max((x.score for x in result), default=0.)))
                self.last_class = getattr(self.detectors[0], "last_class", result[0].kind if result else "none")
                self.error = error
                if generation == self._generation and not self._stop.is_set():
                    self._result = (received, result)
                self._busy = False


class AuxiliaryDetector:
    def __init__(self, detector, hands=True, objects=True, sensitivity=1.0, tongue=True, smile=True):
        self.detector, self.name = detector, detector.name
        self.workers, self._unavailable = {}, {}
        self._lock = threading.RLock()
        self._closed = False
        self.sensitivity = sensitivity
        self._enabled = {}
        self.status = ""
        for kind, enabled in dict(hands=hands, objects=objects, smile=smile, tongue=tongue).items():
            self.set_feature(kind, enabled)

    def set_feature(self, kind, enabled):
        if kind not in ("hands", "objects", "smile", "tongue"):
            return
        enabled = bool(enabled)
        with self._lock:
            if self._closed:
                return
            if self._enabled.get(kind) == enabled:
                return
            self._enabled[kind] = enabled
            previous = self.workers.pop(kind, None)
            self._unavailable[kind] = dict(loaded=False, enabled=enabled, inference_ms=None,
                                           score=0., label="Carregando" if enabled else "Desligado", error="", age=None)
        if previous:
            previous.close()
        if kind == "tongue":
            self.detector.tongue_enabled = enabled
            with self._lock:
                self._unavailable.pop(kind, None)
            return
        if kind == "smile":
            self.detector.smile_enabled = enabled
        if enabled:
            try:
                if not valid_model(kind):
                    raise RuntimeError("Modelo ausente/inválido: execute preparar_modelos.bat")
                factory, period = {"hands": (PalmDetector, .12), "objects": (ObjectDetector, .50),
                                   "smile": (SmileDetector, .15)}[kind]
                model = factory(mirror=getattr(self.detector, "mirror", True),
                                threshold={"hands": .45, "objects": .28, "smile": .55}[kind] / self.sensitivity)
                worker = AuxiliaryWorker([model], period=period)
                with self._lock:
                    closed = self._closed
                    if not closed:
                        self.workers[kind] = worker
                        self._unavailable.pop(kind, None)
                if closed:
                    worker.close()
            except Exception as error:
                with self._lock:
                    self._unavailable[kind].update(label="Indisponível", error=str(error))
        self.status = " · ".join(f"{key}: {'ligado' if value else 'desligado'}" for key, value in self._enabled.items())

    def module_statuses(self):
        enabled = self._enabled.get("tongue", True)
        tongue = dict(loaded=enabled and hasattr(self.detector, "tongue_score"), enabled=enabled,
                      inference_ms=getattr(self.detector, "tongue_ms", None),
                      score=getattr(self.detector, "tongue_score", 0.), label="YuNet + CV experimental",
                      error="" if not enabled or hasattr(self.detector, "tongue_score") else "Língua requer YuNet", age=0.)
        if not enabled:
            tongue.update(inference_ms=None, score=0., label="Desligado")
        with self._lock:
            unavailable = {name: dict(data) for name, data in self._unavailable.items()}
            workers = list(self.workers.items())
        if "smile" in unavailable and unavailable["smile"]["enabled"]:
            unavailable["smile"].update(label="landmarks experimental", score=getattr(self.detector, "smile_score", 0.))
        return {"tongue": tongue, **unavailable, **{name: worker.snapshot() for name, worker in workers}}

    def detect(self, image):
        faces = self.detector.detect(image)
        if "smile" in self.workers:
            for face in faces:
                face.cues.pop("smile", None)  # FER é a fonte quando carregado.
                face.cues.pop("smile_landmarks", None)
        observations, fresh = [], set()
        for kind, worker in self.workers.items():
            worker.submit(image, faces)
            result = worker.take()
            if result is not None:
                observations.extend(result)
                fresh.update({"hand"} if kind == "hands" else ({"smile"} if kind == "smile" else ObjectDetector.labels.values()))
        return ObservedBatch(faces, observations if fresh else None, self.status, fresh)

    def reset(self):
        with self._lock:
            workers = list(self.workers.values())
        for worker in workers:
            worker.reset()

    def close(self):
        with self._lock:
            self._closed = True
            workers = list(self.workers.values())
            self.workers.clear()
        for worker in workers:
            worker.close()
