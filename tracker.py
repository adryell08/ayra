"""Detecção e rastreamento de rostos.

Camadas:
  * detectores   (YuNet / Haar)  -> lista de `Detection` por quadro;
  * fontes       (CameraSource / SimulatedSource) -> entregam detecções;
  * FaceTracker  -> dá um ID persistente a cada rosto entre quadros.

Todas as coordenadas são normalizadas (0..1) e já no "espaço de vista" (espelhado):
quem anda para a direita da tela aparece com x crescente. Nenhuma imagem da câmera é
guardada ou desenhada; só números (posição/tamanho) saem daqui.

Gancho futuro: `Track.embedding` (reconhecimento com SFace) permitirá reconhecer a
mesma pessoa quando ela sai e volta.
"""
from __future__ import annotations

import math
import os
import random
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
from camera_devices import CameraDevice, enumerate_cameras, preferred_cameras
from camera_health import CaptureHealth

os.environ.setdefault("OPENCV_LOG_LEVEL", "SILENT")   # sem avisos de câmera no console
try:
    import cv2
except Exception:  # pragma: no cover
    cv2 = None

MODEL_URL = ("https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/"
             "face_detection_yunet/face_detection_yunet_2023mar.onnx")
DEFAULT_MODEL = Path(__file__).resolve().parent / "models" / "face_detection_yunet_2023mar.onnx"


# --------------------------------------------------------------------------
# Tipos
# --------------------------------------------------------------------------
@dataclass
class Detection:
    x: float
    y: float
    w: float
    h: float            # caixa do rosto (fração do quadro)
    ex: float
    ey: float           # ponto médio entre os olhos
    conf: float = 1.0
    cues: dict = field(default_factory=dict)  # escores locais; nunca pixels/ROIs
    landmarks: tuple = ()  # cinco pontos normalizados/espelhados, somente números

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


@dataclass
class Track:
    id: int
    x: float
    y: float
    w: float
    h: float
    ex: float
    ey: float
    vx: float = 0.0
    vy: float = 0.0
    conf: float = 1.0
    first_seen: float = 0.0
    last_seen: float = 0.0
    born: float = 0.0            # instante da confirmação
    hits: int = 1
    confirmed: bool = False
    visible: bool = True
    dwell: float = 0.0           # tempo total sendo o alvo principal
    embedding: Optional[np.ndarray] = None   # reservado: reconhecimento facial futuro

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    def gaze_xy(self) -> Tuple[float, float]:
        """Onde olhar para encarar esta pessoa, em -1..1 (x: direita, y: baixo)."""
        return (max(-1.0, min(1.0, (self.ex - 0.5) * 2.0)),
                max(-1.0, min(1.0, (self.ey - 0.5) * 2.0)))


@dataclass
class TrackEvent:
    kind: str                   # "new" | "lost"
    track_id: int
    t: float = 0.0


# --------------------------------------------------------------------------
# Rastreador com IDs persistentes
# --------------------------------------------------------------------------
class FaceTracker:
    def __init__(self, ttl_visible: float = 0.30, ttl_remove: float = 1.5, min_hits: int = 3,
                 smooth: float = 0.55, gate: float = 1.4, max_tracks: int = 8):
        self.ttl_visible = ttl_visible
        self.ttl_remove = ttl_remove
        self.min_hits = min_hits
        self.smooth = smooth
        self.gate = gate
        self.max_tracks = max_tracks
        self.tracks: List[Track] = []
        self._next_id = 1
        self._last_t: Optional[float] = None

    def update(self, dets: List[Detection], now: float) -> List[TrackEvent]:
        events: List[TrackEvent] = []
        dt = 0.033 if self._last_t is None else max(now - self._last_t, 1e-3)
        self._last_t = now

        pairs = []
        for ti, tr in enumerate(self.tracks):
            stale = now - tr.last_seen
            lead = min(stale, 0.3) if stale > 0.3 else min(dt, 0.1)
            pcx, pcy = tr.cx + tr.vx * lead, tr.cy + tr.vy * lead
            for di, d in enumerate(dets):
                dist = math.hypot(d.cx - pcx, d.cy - pcy) / max(tr.h, 0.06)
                ratio = d.h / max(tr.h, 1e-3)
                if dist < self.gate and 0.5 < ratio < 2.0:
                    pairs.append((dist, ti, di))
        pairs.sort()
        used_t, used_d = set(), set()
        for _, ti, di in pairs:
            if ti in used_t or di in used_d:
                continue
            used_t.add(ti)
            used_d.add(di)
            self._apply(self.tracks[ti], dets[di], now, events)

        for di, d in enumerate(dets):
            if di in used_d or len(self.tracks) >= self.max_tracks:
                continue
            self.tracks.append(Track(id=0, x=d.x, y=d.y, w=d.w, h=d.h, ex=d.ex, ey=d.ey, conf=d.conf,
                                     first_seen=now, last_seen=now, hits=1, confirmed=False))
        events.extend(self.tick(now))
        return events

    def _apply(self, tr: Track, d: Detection, now: float, events: List[TrackEvent]) -> None:
        a = self.smooth
        dt = max(now - tr.last_seen, 1e-3)
        ocx, ocy = tr.cx, tr.cy
        tr.x += a * (d.x - tr.x)
        tr.y += a * (d.y - tr.y)
        tr.w += a * (d.w - tr.w)
        tr.h += a * (d.h - tr.h)
        tr.ex += a * (d.ex - tr.ex)
        tr.ey += a * (d.ey - tr.ey)
        vx = max(-3.0, min(3.0, (tr.cx - ocx) / dt))
        vy = max(-3.0, min(3.0, (tr.cy - ocy) / dt))
        tr.vx += 0.4 * (vx - tr.vx)
        tr.vy += 0.4 * (vy - tr.vy)
        tr.conf = d.conf
        tr.last_seen = now
        if not tr.confirmed and dt > .15:
            tr.hits = 0
        tr.hits += 1
        tr.visible = True
        required_hits = self.min_hits if d.conf >= .9 else max(4, self.min_hits)
        if not tr.confirmed and tr.hits >= required_hits:
            tr.confirmed = True
            tr.id = self._next_id
            self._next_id += 1
            tr.born = now
            events.append(TrackEvent("new", tr.id, now))

    def tick(self, now: float) -> List[TrackEvent]:
        """Envelhece rostos sem detecção nova (visibilidade e remoção)."""
        events: List[TrackEvent] = []
        keep = []
        for tr in self.tracks:
            stale = now - tr.last_seen
            tr.visible = stale <= self.ttl_visible
            if tr.confirmed:
                if stale > self.ttl_remove:
                    events.append(TrackEvent("lost", tr.id, now))
                    continue
            elif stale > 0.35:
                continue
            keep.append(tr)
        self.tracks = keep
        return events


# --------------------------------------------------------------------------
# Detectores
# --------------------------------------------------------------------------
def ensure_model(path: Path = DEFAULT_MODEL, timeout: float = 20.0) -> Optional[Path]:
    """Garante o modelo YuNet em disco (~230 KB). Baixa uma vez se faltar."""
    path = Path(path)
    if path.exists() and path.stat().st_size > 100_000:
        return path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(MODEL_URL, timeout=timeout) as r:
            data = r.read()
        if len(data) > 100_000:
            path.write_bytes(data)
            return path
    except Exception:
        pass
    return None


class YuNetDetector:
    name = "YuNet"

    def __init__(self, model_path: Path, score: float = 0.7, det_width: int = 416, mirror: bool = True, sensitivity=1.0):
        self.det_width = det_width
        self.mirror = mirror
        self.sensitivity = sensitivity
        self.tongue_ms, self.tongue_score = None, 0.
        self.smile_score = 0.
        size = (det_width, int(det_width * 0.75))
        try:
            self.det = cv2.FaceDetectorYN.create(str(model_path), "", size, score, 0.3, 50)
        except cv2.error:
            # OpenCV/Windows pode falhar ao abrir caminhos com acentos. Python lê
            # o mesmo ONNX e a sobrecarga por buffer evita depender desse caminho.
            model = np.frombuffer(Path(model_path).read_bytes(), dtype=np.uint8)
            self.det = cv2.FaceDetectorYN.create("onnx", model, np.empty(0, dtype=np.uint8),
                                                 size, score, 0.3, 50)

    def detect(self, frame) -> List[Detection]:
        h, w = frame.shape[:2]
        sw = self.det_width
        sh = int(round(h * sw / w))
        small = cv2.resize(frame, (sw, sh), interpolation=cv2.INTER_AREA) if w != sw else frame
        self.det.setInputSize((sw, sh))
        _, faces = self.det.detect(small)
        out: List[Detection] = []
        self.tongue_ms, self.tongue_score, self.smile_score = 0., 0., 0.
        if faces is None:
            return out
        for f in faces:
            x, y, bw, bh = float(f[0]), float(f[1]), float(f[2]), float(f[3])
            ex, ey = (float(f[4]) + float(f[6])) / 2, (float(f[5]) + float(f[7])) / 2
            nx, ny, nw, nh = x / sw, y / sh, bw / sw, bh / sh
            nex, ney = ex / sw, ey / sh
            if nh < 0.06:
                continue
            if self.mirror:
                nx, nex = 1.0 - (nx + nw), 1.0 - nex
            from perception import tongue_out_score, smile_landmark_score, mouth_ratio
            cue_started = time.perf_counter()
            tongue = tongue_out_score(small, f, self.sensitivity) if getattr(self, "tongue_enabled", True) else 0.
            self.tongue_ms += (time.perf_counter() - cue_started) * 1000
            self.tongue_score = max(self.tongue_score, tongue)
            smile = smile_landmark_score(f) if getattr(self, "smile_enabled", True) else 0.
            self.smile_score = max(self.smile_score, smile)
            landmarks = tuple((float(f[i]) / sw if not self.mirror else 1 - float(f[i]) / sw,
                               float(f[i + 1]) / sh) for i in range(4, 14, 2))
            out.append(Detection(nx, ny, nw, nh, nex, ney, float(f[14]),
                                 {"tongue_out": tongue, "smile_landmarks": smile,
                                  "mouth_ratio": min(1.0, mouth_ratio(f) / 2.0)}, landmarks))
        return out


class HaarDetector:
    """Plano B (menos preciso) caso o modelo YuNet não esteja disponível."""
    name = "Haar"

    def __init__(self, mirror: bool = True, det_width: int = 320):
        if not hasattr(cv2, "CascadeClassifier"):
            raise RuntimeError("Este OpenCV não inclui o detector Haar (CascadeClassifier).")
        path = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
        self.cc = cv2.CascadeClassifier(path)
        self.mirror = mirror
        self.det_width = det_width

    def detect(self, frame) -> List[Detection]:
        h, w = frame.shape[:2]
        sw = self.det_width
        sh = int(round(h * sw / w))
        gray = cv2.cvtColor(cv2.resize(frame, (sw, sh)), cv2.COLOR_BGR2GRAY)
        rects = self.cc.detectMultiScale(gray, 1.15, 5, minSize=(int(sw * 0.08), int(sw * 0.08)))
        out = []
        for (x, y, bw, bh) in rects:
            nx, ny, nw, nh = x / sw, y / sh, bw / sw, bh / sh
            nex, ney = nx + nw / 2, ny + nh * 0.40
            if self.mirror:
                nx, nex = 1.0 - (nx + nw), 1.0 - nex
            out.append(Detection(nx, ny, nw, nh, nex, ney, 0.8))
        return out


def make_detector(model: Optional[Path] = None, det_width: int = 416, mirror: bool = True,
                  hands=True, objects=True, sensitivity=1.0, tongue=True, smile=True):
    """Escolhe o melhor detector disponível. Devolve (detector, aviso|None)."""
    if cv2 is None:
        raise RuntimeError("OpenCV não instalado (pip install opencv-python).")
    path = ensure_model(Path(model) if model else DEFAULT_MODEL)
    if path is not None and hasattr(cv2, "FaceDetectorYN"):
        try:
            from auxiliary import AuxiliaryDetector
            detector = AuxiliaryDetector(YuNetDetector(path, det_width=det_width, mirror=mirror, sensitivity=sensitivity),
                                           hands=hands, objects=objects, sensitivity=sensitivity, tongue=tongue, smile=smile)
            return detector, None  # ausência de módulo opcional aparece no diagnóstico, não é falha do YuNet
        except Exception as e:  # modelo corrompido, OpenCV antigo...
            warn = f"YuNet indisponível ({e}); usando Haar."
    else:
        warn = "Modelo YuNet não encontrado; usando Haar (menos preciso)."
    try:
        from auxiliary import AuxiliaryDetector
        return AuxiliaryDetector(HaarDetector(mirror=mirror), hands=hands, objects=objects, sensitivity=sensitivity,
                                 tongue=tongue, smile=smile), warn
    except Exception as error:
        raise RuntimeError(f"Falha ao iniciar o detector: {warn} {error}") from error


# --------------------------------------------------------------------------
# Fontes de detecções
# --------------------------------------------------------------------------
class CameraSource:
    """Captura e detecção em thread própria: o desenho nunca espera pela câmera.

    Privacidade: ao pausar, a câmera é *liberada* (a luz do hardware apaga);
    os quadros são processados na hora e descartados. Exceção explícita (Nuria):
    `capture_still()` entrega UMA cópia do quadro atual quando o usuário faz uma pergunta.
    """
    kind = "camera"

    def __init__(self, detector, index: Optional[int] = None, width: int = 640, height: int = 480):
        self.detector = detector
        self.requested_index = index
        self.index = index
        self.device_name = ""
        self.devices = []
        self._enumerated_at = 0.0
        self.width, self.height = width, height
        self.status = "starting"          # starting | ok | paused | error
        self.error = ""
        self.detect_fps = 0.0
        self.backend = ""
        self._lock = threading.Lock()
        self._latest: Optional[List[Detection]] = None
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._still_req = threading.Event()
        self._still_ready = threading.Event()
        self._still = None
        self._switch = threading.Event()
        self._pending_features = {}
        self._health = CaptureHealth()
        self._thread = threading.Thread(target=self._run, name="camera", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)
        if hasattr(self.detector, "close"):
            self.detector.close()

    def set_paused(self, paused: bool) -> None:
        if hasattr(self.detector, "reset"):
            self.detector.reset()
        if paused:
            self._paused.set()
            self.status = "paused"
            with self._lock:
                self._latest = None
                self._health.reset()
        else:
            self._paused.clear()
            self.status = "starting"

    def set_camera(self, index: Optional[int]) -> None:
        if hasattr(self.detector, "reset"):
            self.detector.reset()
        with self._lock:
            self.requested_index, self.index = index, index
            self.device_name, self.backend, self.error = "", "", ""
            self._latest = None
            self._health.reset()
            if index is None:
                self.devices = []
        self._paused.clear()
        self.status = "starting"
        self._switch.set()

    def set_feature(self, name, enabled):
        with self._lock:
            self._pending_features[name] = bool(enabled)

    def capture_info(self, now=None):
        with self._lock:
            info = self._health.snapshot(time.perf_counter() if now is None else now)
            info.update(index=self.index, name=self.device_name, backend=self.backend,
                        choices=[(device.index, device.name) for device in self.devices])
            info["modules"] = self.detector.module_statuses() if hasattr(self.detector, "module_statuses") else {}
            if self.paused:
                info.update(state="paused", note="")
            elif self.status == "error":
                info.update(state="error", note=self.error)
            return info

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def poll(self) -> Optional[List[Detection]]:
        with self._lock:
            out, self._latest = self._latest, None
        return out

    def capture_still(self, timeout: float = 2.0):
        """Foto única (BGR) do próximo quadro lido, sob pedido; None se pausada/indisponível."""
        if self.paused or not self._thread.is_alive():
            return None
        with self._lock:
            self._still = None
        self._still_ready.clear()
        self._still_req.set()
        if not self._still_ready.wait(timeout):
            self._still_req.clear()
            return None
        with self._lock:
            image, self._still = self._still, None
        return image

    def _open(self):
        if not self.devices or time.perf_counter() - self._enumerated_at > 10:
            self.devices = enumerate_cameras()
            self._enumerated_at = time.perf_counter()
        requested = self.requested_index
        candidates = ([next((device for device in self.devices if device.index == requested),
                            CameraDevice(requested, f"Câmera {requested}"))]
                      if requested is not None else preferred_cameras(self.devices))
        backends = [("DSHOW", cv2.CAP_DSHOW), ("MSMF", cv2.CAP_MSMF), ("padrão", 0)] if os.name == "nt" else [("padrão", 0)]
        failures = []
        waiting_candidate = None
        for device in candidates:
            self.index, self.device_name = device.index, device.name
            for name, backend in backends:
                if self._stop.is_set() or self._switch.is_set() or self.paused:
                    return None
                cap = None
                try:
                    cap = cv2.VideoCapture(device.index, backend)
                    if not cap.isOpened():
                        failures.append(f"#{device.index} {name}: não abriu")
                    else:
                        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                        with self._lock:
                            self._health.reset()
                        readable = False
                        for _ in range(8):
                            ok, first_frame = cap.read()
                            if not ok or first_frame is None:
                                del first_frame
                                break
                            if self._stop.is_set() or self._switch.is_set() or self.paused:
                                del first_frame
                                cap.release()
                                return None
                            readable = True
                            with self._lock:
                                self._health.observe(first_frame, time.perf_counter())
                                info = self._health.snapshot(time.perf_counter())
                            del first_frame
                            if info["state"] == "ok":
                                break
                            if self._stop.is_set() or self._switch.is_set():
                                break
                        if readable and (info["state"] == "ok" or requested is not None):
                            self.backend = cap.getBackendName() if hasattr(cap, "getBackendName") else name
                            self.error = ""
                            return cap
                        if readable and waiting_candidate is None:
                            waiting_candidate = (device, name, backend)
                        failures.append(f"#{device.index} {name}: " + (info["note"] if readable else "sem quadro"))
                except Exception as error:
                    failures.append(f"#{device.index} {name}: {str(error).splitlines()[0]}")
                if cap is not None:
                    cap.release()
        # Se nenhuma fonte ficou saudável, manter uma física que entrega quadros
        # permite aquecer a exposição e recuperar imagem sem reiniciar o app.
        if waiting_candidate is not None and not self._stop.is_set() and not self._switch.is_set():
            device, name, backend = waiting_candidate
            cap = cv2.VideoCapture(device.index, backend)
            if cap.isOpened():
                self.index, self.device_name = device.index, device.name
                self.backend = cap.getBackendName() if hasattr(cap, "getBackendName") else name
                self.error = ""
                with self._lock:
                    self._health.reset()
                return cap
            cap.release()
        hint = "Câmera em uso por outro app ou indisponível: feche OBS/Teams; confira acesso e --camera N."
        if not candidates:
            hint = "Nenhuma webcam física encontrada. Escolha uma câmera no Menu ou use --camera N."
        if os.name == "nt":
            try:
                import winreg
                key = r"SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\webcam"
                for hive, suffix in ((winreg.HKEY_LOCAL_MACHINE, ""), (winreg.HKEY_CURRENT_USER, ""),
                                     (winreg.HKEY_CURRENT_USER, r"\NonPackaged")):
                    try:
                        with winreg.OpenKey(hive, key + suffix) as access:
                            if winreg.QueryValueEx(access, "Value")[0] == "Deny":
                                hint = "Windows bloqueia a câmera: permita acesso e apps da área de trabalho em Privacidade > Câmera."
                    except OSError:
                        pass
            except ImportError:
                pass
        self.error = f"{hint} {self.device_name or 'Nenhuma câmera física encontrada'} (#{self.index}): {'; '.join(failures)}"
        return None

    def _run(self) -> None:
        cap = None
        t_prev, ema = time.perf_counter(), 0.0
        while not self._stop.is_set():
            with self._lock:
                pending, self._pending_features = self._pending_features, {}
            for name, enabled in pending.items():
                if hasattr(self.detector, "set_feature"):
                    self.detector.set_feature(name, enabled)
            if self._switch.is_set():
                if cap is not None:
                    cap.release()
                    cap = None
                self._switch.clear()
                with self._lock:
                    self._health.reset()
                self.detect_fps, ema, t_prev = 0.0, 0.0, time.perf_counter()
            if self._paused.is_set():
                if cap is not None:
                    cap.release()
                    cap = None
                time.sleep(0.05)
                continue
            if cap is None:
                cap = self._open()
                if cap is None:
                    if self._switch.is_set() or self.paused:
                        continue
                    self.status = "error"
                    self._stop.wait(1.0)
                    continue
            ok, frame = cap.read()
            if self._switch.is_set() or self.paused or self._stop.is_set():
                del frame
                continue
            if ok and frame is not None and self._still_req.is_set():
                with self._lock:
                    self._still = frame.copy()
                self._still_req.clear()
                self._still_ready.set()
            if not ok or frame is None:
                if hasattr(self.detector, "reset"):
                    self.detector.reset()
                self.status, self.error = "error", "A câmera parou de enviar imagens."
                cap.release()
                cap = None
                time.sleep(0.5)
                continue
            with self._lock:
                self._health.observe(frame, time.perf_counter())
                health = self._health.snapshot(time.perf_counter())
            if health["state"] != "ok":
                del frame
                if hasattr(self.detector, "reset"):
                    self.detector.reset()
                self.status, self.error, self.detect_fps = "waiting", "", 0.0
                with self._lock:
                    self._latest = []
                continue
            try:
                dets = self.detector.detect(frame)
            except Exception as e:
                del frame
                if hasattr(self.detector, "reset"):
                    self.detector.reset()
                self.status, self.error = "error", f"Falha na detecção: {e}"
                time.sleep(0.5)
                continue
            del frame                                  # nada de imagem é guardada
            if self._switch.is_set() or self.paused:
                continue
            self.status, self.error = "ok", ""
            with self._lock:
                self._latest = dets
            t = time.perf_counter()
            ema = 0.9 * ema + 0.1 / max(t - t_prev, 1e-3)
            self.detect_fps, t_prev = ema, t
        if cap is not None:
            cap.release()


class SimulatedSource:
    """Rostos sintéticos para testar sem câmera: o mouse é um rosto; `+` / `-` criam
    e removem pessoas que passeiam; a roda do mouse muda a distância (tamanho)."""
    kind = "sim"

    def __init__(self, seed: Optional[int] = None):
        self.rng = random.Random(seed)
        self.mouse = (0.5, 0.45)
        self.mouse_size = 0.32
        self.mouse_face = True
        self.roamers: List[dict] = []
        self.status = "ok"
        self.paused = False
        self.error = ""
        self.detect_fps = 30.0
        self._t = 0.0
        self._acc = 0.0
        self.cues = {}
        self._gesture = None
        self._object_until = 0.0

    def set_cue(self, kind: str, score: float) -> None:
        self.cues[kind] = max(0.0, min(1.0, score)) if math.isfinite(score) else 0.0

    def gesture(self, kind: str) -> None:
        if kind == "dog":
            self._object_until = self._t + 2.0
            return
        self._gesture = (kind, self._t)

    def set_mouse(self, nx: float, ny: float) -> None:
        self.mouse = (max(0.0, min(1.0, nx)), max(0.0, min(1.0, ny)))

    def zoom(self, delta: float) -> None:
        self.mouse_size = max(0.12, min(0.7, self.mouse_size + delta))

    def toggle_mouse_face(self) -> None:
        self.mouse_face = not self.mouse_face

    def add_roamer(self) -> None:
        r = self.rng
        self.roamers.append(dict(ax=r.uniform(0.18, 0.38), ay=r.uniform(0.04, 0.12),
                                 wx=r.uniform(0.12, 0.30), wy=r.uniform(0.2, 0.5),
                                 px=r.uniform(0, 6.28), py=r.uniform(0, 6.28),
                                 cy=r.uniform(0.42, 0.55), size=r.uniform(0.20, 0.34)))

    def remove_roamer(self) -> None:
        if self.roamers:
            self.roamers.pop()

    def set_paused(self, paused: bool) -> None:
        self.paused = paused

    def stop(self) -> None:
        pass

    def _make(self, cx, cy, size) -> Detection:
        n = lambda: self.rng.gauss(0, 0.0015)
        h = size
        w = h * 0.78
        return Detection(cx - w / 2 + n(), cy - h / 2 + n(), w, h, cx + n(), cy - h * 0.12 + n(), 0.95,
                         dict(self.cues))

    def poll(self, dt: float = 1 / 30) -> Optional[List[Detection]]:
        """Entrega ~30 quadros/s de detecções sintéticas."""
        self._t += dt
        self._acc += dt
        if self._acc < 1 / 30:
            return None
        self._acc = 0.0
        if self.paused:
            return []
        out = []
        if self.mouse_face:
            out.append(self._make(self.mouse[0], self.mouse[1], self.mouse_size))
        for r in self.roamers:
            cx = 0.5 + r["ax"] * math.sin(r["wx"] * self._t + r["px"])
            cy = r["cy"] + r["ay"] * math.sin(r["wy"] * self._t + r["py"])
            out.append(self._make(cx, cy, r["size"]))
        from auxiliary import Observation, ObservedBatch
        hands = []
        if self._gesture is not None:
            kind, start = self._gesture
            age = self._t - start
            if kind == "wave" and age <= 1.4:
                hands.append(Observation("hand", .95, .5 + .20 * math.sin(age * math.tau * 2), .5, .18))
            elif kind == "tap" and age <= .5:
                hands.append(Observation("hand", .95, .5, .5, .18 + .55 * min(1, age / .25)))
            else:
                self._gesture = None
        if self._t < self._object_until:
            hands.append(Observation("dog", .95, .5, .5, .3))
        return ObservedBatch(out, hands, "Simulação · observações numéricas", {"hand", "dog", "cat", "bottle", "cup"})
