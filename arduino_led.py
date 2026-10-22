"""Painel físico de LEDs (MAX7219, 4 módulos 8×8 = 32×8) ligado a um Arduino Uno pela USB.

O PC faz todo o trabalho: redesenha o rosto do mesmo instante numa grade própria (só os olhos,
proporção 32×8), aplica a montagem física (ordem, giro e espelho dos módulos) e envia 32 bytes por
quadro. O Arduino (firmware em `arduino/nuria_leds/nuria_leds.ino`) só repassa ao MAX7219.
A interface na tela não muda.

Protocolo serial (115200 baud), PC → Arduino:
    0xA5 0x01 <32 bytes> <xor>   quadro: módulo 0 (mais perto da entrada) linhas 0..7, depois módulo 1…
    0xA5 0x02 <brilho 0..15> <xor>
    0xA5 0x03 <xor = 0>          "olá": o Arduino responde a linha "NURIA-LED <versão> <módulos>"
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

import numpy as np

from face import EYE_CY, EYE_DX, render_field

MODULES = 4
WIDTH, HEIGHT = 8 * MODULES, 8
BAUD = 115200
FIRMWARE_VERSION = 2       # nuria_leds.ino reconfigura o MAX7219 a cada quadro
SYNC, CMD_FRAME, CMD_BRIGHT, CMD_HELLO = 0xA5, 0x01, 0x02, 0x03
CONFIG_PATH = Path(__file__).resolve().parent / "arduino.local.json"
BRIGHTNESS_STEPS = (1, 2, 4, 6, 8, 12, 15)
SIZES = {"pequeno": 1.0, "medio": 1.2, "grande": 1.4}       # multiplica a "altura virtual": maior = olhos maiores
SIZE_LABELS = {"pequeno": "Pequenos", "medio": "Médios", "grande": "Grandes"}


@dataclass
class ArduinoSettings:
    enabled: bool = False
    port: str = "auto"                 # "auto" ou "COM4"
    brightness: int = 2                # 0..15 (baixo: a USB do PC alimenta o painel)
    size: str = "medio"
    input_right: bool = True           # entrada (fios) à direita, vista de frente
    rotation: int = 0                  # giro de cada módulo: 0, 90, 180, 270
    mirror: bool = False
    upside_down: bool = False          # painel inteiro girado 180°
    test: bool = False                 # padrão de calibração no lugar do rosto

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "ArduinoSettings":
        path = path or CONFIG_PATH
        s = cls()
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            for key, default in asdict(s).items():
                if key in data and isinstance(data[key], type(default)):
                    setattr(s, key, data[key])
        except (OSError, ValueError, AttributeError):
            pass
        s.test = False                                   # nunca abre em modo teste
        s.brightness = max(0, min(15, s.brightness))
        s.rotation = s.rotation if s.rotation in (0, 90, 180, 270) else 0
        s.size = s.size if s.size in SIZES else "medio"
        return s

    def save(self, path: Optional[Path] = None) -> None:
        path = Path(path or CONFIG_PATH)
        tmp = path.with_suffix(".tmp")
        data = asdict(self)
        data.pop("test")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)


# ---------------------------------------------------------------------------
# Imagem 32×8
# ---------------------------------------------------------------------------
EYE_SPAN = 16          # cada olho tem a própria metade do painel (16×8) e fica centralizado nela
TRAVEL = 1.1           # folga lateral: o olhar anda um pouco mais que na tela, sem sair da metade


def render_canvas(render_args, size: str = "medio", supersample: int = 4) -> np.ndarray:
    """Rosto do mesmo instante, só os olhos, em 32×8 (bool, linha 0 = topo, coluna 0 = esquerda).

    Layout 16×8 | 16×8: o olho esquerdo em repouso fica no centro da metade esquerda (x≈8) e o direito
    no centro da metade direita (x≈24); o olhar desloca cada olho dentro da sua metade.
    """
    pose, blink, gaze, motion, zzz, features, mood, dyn = render_args
    k = supersample
    h_virtual = 20.5 * SIZES.get(size, 1.0)              # altura da grade "virtual": define o tamanho dos olhos
    H = int(round(h_virtual * k))
    W = int(round(2 * (EYE_DX * H + EYE_SPAN * k)))      # largo o bastante para os dois olhos e suas folgas
    style = dict(features or {}, mouth=False, outline=False)   # 8 linhas: sem boca nem contorno fino
    alpha, _ = render_field(W, H, pose, blink, gaze, motion * TRAVEL, zzz=None, style=style, mood=mood, dyn=dyn)
    top = int(round(EYE_CY * H - HEIGHT * k / 2))
    top = max(0, min(H - HEIGHT * k, top))
    band = alpha[top:top + HEIGHT * k, :]
    halves = []
    for side in (-1, 1):                                  # recorta 16 colunas centradas no repouso de cada olho
        cx = W / 2.0 + side * EYE_DX * H
        x0 = int(round(cx - EYE_SPAN * k / 2))
        half = np.zeros((HEIGHT * k, EYE_SPAN * k), np.float32)
        a, b = max(0, x0), min(W, x0 + EYE_SPAN * k)
        if b > a:
            half[:, a - x0:b - x0] = band[:, a:b]
        halves.append(half)
    crop = np.concatenate(halves, axis=1)
    cells = crop.reshape(HEIGHT, k, WIDTH, k).mean(axis=(1, 3))
    return cells > 0.42


def test_pattern(t: float = 0.0) -> np.ndarray:
    """Calibração: canto superior esquerdo aceso, linha no topo e seta → na altura do meio.
    Correto quando o ponto está em cima à esquerda e a seta aponta para a direita."""
    c = np.zeros((HEIGHT, WIDTH), bool)
    c[0, 0] = c[1, 0] = c[0, 1] = True
    c[0, 4:WIDTH - 4:2] = True
    c[4, 3:WIDTH - 3] = True
    for i in range(1, 4):
        c[4 - i, WIDTH - 4 - i] = c[4 + i if 4 + i < HEIGHT else HEIGHT - 1, WIDTH - 4 - i] = True
    for m in range(MODULES):                             # m+1 pontos na base de cada módulo = número do módulo
        c[7, m * 8 + 1:m * 8 + 2 + 2 * m:2] = True
    return c


def encode(canvas: np.ndarray, s: ArduinoSettings) -> bytes:
    """Imagem lógica 32×8 → 32 bytes na ordem da cadeia (módulo 0 = mais perto da entrada)."""
    img = np.asarray(canvas, bool)
    if s.upside_down:
        img = img[::-1, ::-1]
    blocks = [img[:, m * 8:(m + 1) * 8] for m in range(MODULES)]       # da esquerda para a direita
    if s.input_right:
        blocks = blocks[::-1]                                          # entrada à direita: módulo 0 é o da direita
    out = bytearray()
    for b in blocks:
        b = np.rot90(b, -(s.rotation // 90))                           # giro no sentido horário
        if s.mirror:
            b = b[:, ::-1]
        for row in range(8):
            byte = 0
            for col in range(8):
                if b[row, col]:
                    byte |= 0x80 >> col
            out.append(byte)
    return bytes(out)


def packet(cmd: int, payload: bytes = b"") -> bytes:
    x = 0
    for v in payload:
        x ^= v
    return bytes([SYNC, cmd]) + payload + bytes([x])


# ---------------------------------------------------------------------------
# Conexão serial (thread própria; nunca bloqueia a interface)
# ---------------------------------------------------------------------------
def list_ports() -> List[tuple]:
    """[(dispositivo, descrição)], Arduinos/CH340 primeiro."""
    try:
        from serial.tools import list_ports as lp
    except ImportError:
        return []
    found = []
    for p in lp.comports():
        desc = p.description or p.device
        score = 0 if (p.vid in (0x2341, 0x2A03, 0x1A86, 0x0403, 0x10C4) or "arduino" in desc.lower()
                      or "ch340" in desc.lower()) else 1
        found.append((score, p.device, desc))
    return [(d, desc) for _, d, desc in sorted(found)]


class ArduinoLink:
    def __init__(self, serial_factory: Optional[Callable[[str], object]] = None,
                 ports: Callable[[], List[tuple]] = list_ports, reset_wait: float = 2.0, fps: float = 30.0):
        self._factory = serial_factory or self._open_serial
        self._ports = ports
        self.reset_wait = reset_wait                     # o Uno reinicia ao abrir a porta
        self.min_dt = 1.0 / fps
        self.status = "desligado"
        self.port = ""
        self.frames_sent = 0
        self.firmware = 0
        self._want_port = "auto"
        self._frame: Optional[bytes] = None
        self._brightness: Optional[int] = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._ser = None

    @staticmethod
    def _open_serial(port: str):
        import serial
        return serial.Serial(port, BAUD, timeout=0.2, write_timeout=0.5)

    # ---- API ------------------------------------------------------------------
    def start(self, port: str = "auto") -> None:
        self._want_port = port
        if self._thread and self._thread.is_alive():
            self.reconnect(port)
            return
        self._stop.clear()
        self.status = "procurando…"
        self._thread = threading.Thread(target=self._run, name="arduino", daemon=True)
        self._thread.start()

    def reconnect(self, port: str) -> None:
        self._want_port = port
        with self._lock:
            ser, self._ser = self._ser, None
        self._close(ser)
        self.status = "procurando…"
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(1.5)
        with self._lock:
            ser, self._ser = self._ser, None
        if ser is not None:                               # apaga o painel ao sair
            try:
                ser.write(packet(CMD_FRAME, bytes(32)))
                ser.flush()
            except Exception:
                pass
        self._close(ser)
        self.status = "desligado"

    def submit(self, frame: bytes) -> None:
        with self._lock:
            self._frame = frame
        self._wake.set()

    def set_brightness(self, level: int) -> None:
        with self._lock:
            self._brightness = max(0, min(15, int(level)))
        self._wake.set()

    @property
    def connected(self) -> bool:
        return self._ser is not None

    # ---- internos -------------------------------------------------------------
    @staticmethod
    def _close(ser):
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass

    def _handshake(self, ser) -> bool:
        deadline = time.perf_counter() + self.reset_wait + 1.5
        time.sleep(min(self.reset_wait, 0.2))
        ser.write(packet(CMD_HELLO))
        buf = b""
        while time.perf_counter() < deadline and not self._stop.is_set():
            chunk = ser.read(64)
            if chunk:
                buf += chunk
                if b"NURIA-LED" in buf and b"\n" in buf.split(b"NURIA-LED", 1)[1]:
                    try:
                        self.firmware = int(buf.split(b"NURIA-LED", 1)[1].split()[0])
                    except (ValueError, IndexError):
                        self.firmware = 0
                    return True
            elif time.perf_counter() > deadline - 1.5 and b"NURIA-LED" not in buf:
                ser.write(packet(CMD_HELLO))             # após o reset do Uno, pergunta de novo
        return False

    def _connect(self) -> None:
        wanted = self._want_port
        candidates = [wanted] if wanted != "auto" else [d for d, _ in self._ports()]
        if not candidates:
            self.status = "nenhuma porta USB encontrada"
            return
        opened_any = False
        for port in candidates:
            if self._stop.is_set() or wanted != self._want_port:
                return
            try:
                ser = self._factory(port)
                opened_any = True
            except Exception as error:
                self.status = (f"{port}: não abriu ({str(error)[:40]}) · feche o Monitor Serial"
                               if wanted != "auto" else "procurando…")
                continue
            try:
                ok = self._handshake(ser)
            except Exception:
                ok = False
            if ok:
                with self._lock:
                    self._ser = ser
                    if self._brightness is None:
                        self._brightness = 2
                    self._sent_brightness = None
                self.port = port
                self.status = f"conectado · {port}" + ("" if self.firmware >= FIRMWARE_VERSION
                                                       else " · firmware antigo: grave de novo (gravar_arduino.bat)")
                return
            self._close(ser)
        if not opened_any:
            if wanted == "auto":
                self.status = "nenhuma porta disponível (feche o Monitor Serial da IDE)"
            return
        self.status = ("firmware da Nuria não respondeu" + (f" em {wanted}" if wanted != "auto" else "")
                       + " · grave arduino/nuria_leds")

    def _run(self) -> None:
        last_frame_at, last_sent = 0.0, None
        retry_at = 0.0
        while not self._stop.is_set():
            if self._ser is None:
                if time.perf_counter() >= retry_at:
                    self._connect()
                    retry_at = time.perf_counter() + 3.0
                    last_sent = None
                if self._ser is None:
                    self._wake.wait(0.5)
                    self._wake.clear()
                    continue
            self._wake.wait(self.min_dt)
            self._wake.clear()
            with self._lock:
                frame, bright, ser = self._frame, self._brightness, self._ser
            if ser is None:
                continue
            try:
                if bright is not None and bright != getattr(self, "_sent_brightness", None):
                    ser.write(packet(CMD_BRIGHT, bytes([bright])))
                    self._sent_brightness = bright
                now = time.perf_counter()
                stale = now - last_frame_at > 1.0              # reenvia 1×/s: o firmware apaga após 3 s sem quadros
                if frame is not None and (frame != last_sent or stale) and now - last_frame_at >= self.min_dt:
                    ser.write(packet(CMD_FRAME, frame))
                    last_sent, last_frame_at = frame, now
                    self.frames_sent += 1
            except Exception as error:                   # cabo puxado, porta sumiu
                with self._lock:
                    self._ser = None
                self._close(ser)
                self.status = f"desconectado ({str(error)[:30]}) · tentando de novo"
                retry_at = time.perf_counter() + 1.0
