"""Testes do painel físico (Arduino + MAX7219 32×8), com porta serial SIMULADA.

Uso: python test_arduino.py   (gera previews/arduino_*.png com a imagem 32×8 ampliada)
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np

import arduino_led as al
from robot import Robot

RESULTS = []


def check(name, cond):
    RESULTS.append((name, bool(cond)))
    print(f"  [{'OK' if cond else 'FALHOU'}] {name}")


def components(mask):
    """Número de regiões acesas (4-vizinhança)."""
    seen, n = np.zeros_like(mask), 0
    for y, x in zip(*np.nonzero(mask)):
        if seen[y, x]:
            continue
        n += 1
        stack = [(y, x)]
        while stack:
            cy, cx = stack.pop()
            if 0 <= cy < mask.shape[0] and 0 <= cx < mask.shape[1] and mask[cy, cx] and not seen[cy, cx]:
                seen[cy, cx] = True
                stack += [(cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1)]
    return n


def frame_for(robot, t=0.5, steps=40):
    f = None
    for i in range(steps):
        f = robot.step([], t + i / 60, 1 / 60)
    return f


class FirmwareParser:
    """Mesma máquina de estados do nuria_leds.ino (para validar o protocolo)."""
    def __init__(self):
        self.frame, self.brightness, self.hellos, self.buf = bytes(32), 2, 0, bytearray()

    def feed(self, data):
        self.buf += data
        while True:
            if not self.buf:
                return
            if self.buf[0] != 0xA5:
                del self.buf[0]
                continue
            if len(self.buf) < 2:
                return
            cmd = self.buf[1]
            n = 32 if cmd == 1 else 1 if cmd == 2 else 0
            if cmd not in (1, 2, 3):
                del self.buf[0]
                continue
            if len(self.buf) < 3 + n:
                return
            payload, chk = bytes(self.buf[2:2 + n]), self.buf[2 + n]
            del self.buf[:3 + n]
            x = 0
            for v in payload:
                x ^= v
            if x != chk:
                continue
            if cmd == 1:
                self.frame = payload
            elif cmd == 2:
                self.brightness = payload[0]
            else:
                self.hellos += 1


class FakeSerial:
    def __init__(self, port, silent=False, fail_after=None, version=2):
        self.port, self.silent, self.fail_after, self.version = port, silent, fail_after, version
        self.fw = FirmwareParser()
        self.out = bytearray()
        self.closed = False
        self.writes = 0

    def write(self, data):
        if self.closed:
            raise OSError("porta fechada")
        self.writes += 1
        if self.fail_after is not None and self.writes > self.fail_after:
            raise OSError("cabo desconectado")
        before = self.fw.hellos
        self.fw.feed(data)
        if self.fw.hellos > before and not self.silent:
            self.out += b"NURIA-LED %d 4\r\n" % self.version

    def read(self, n):
        time.sleep(0.01)
        data, self.out = bytes(self.out[:n]), self.out[n:]
        return data

    def flush(self):
        pass

    def close(self):
        self.closed = True


def wait(cond, t=3.0):
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < t:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_render(png=True):
    print("Rosto 32×8")
    rb = Robot((80, 45), seed=3) if "seed" in Robot.__init__.__code__.co_varnames else Robot((80, 45))
    rb.expr.auto_blink = False
    frame_for(rb)
    c = al.render_canvas(rb.last_render_args)
    check("imagem 32×8", c.shape == (8, 32) and c.dtype == bool)
    left, right = c[:, :16], c[:, 16:]
    check("dois olhos, um em cada metade", components(left) >= 1 and components(right) >= 1
          and 15 <= c.sum() <= 140)
    cxl, cxr = np.nonzero(left)[1].mean(), np.nonzero(right)[1].mean() + 16
    check("layout 16×8 | 16×8: cada olho centralizado na sua metade", abs(cxl - 7.5) < 1.0 and abs(cxr - 23.5) < 1.0)
    rows = np.nonzero(c.any(axis=1))[0]
    check("olhos ocupam a altura do painel (≥ 6 de 8 linhas)", len(rows) >= 6)
    cols_left = np.nonzero(left.any(axis=0))[0]
    check("cada olho tem 5–9 colunas", 5 <= len(cols_left) <= 9)
    rb.attention.look_at(1.0, 0.0, 2.0, 1.2)
    for i in range(60):
        rb.step([], 1.2 + i / 60, 1 / 60)
    c_right = al.render_canvas(rb.last_render_args)
    cx = lambda m: np.nonzero(m)[1].mean()
    check("olhar para o lado desloca os olhos no painel", cx(c_right) > cx(c) + 0.8)
    edge = False
    for gx, gy in ((-1, 0), (1, 1), (-1, -1)):
        rb.attention.look_at(gx, gy, 3.0, 2.0)
        for i in range(60):
            rb.step([], 2.0 + i / 60, 1 / 60)
            m = al.render_canvas(rb.last_render_args)
            edge = edge or m[:, [0, 15, 16, 31]].any()
    check("olhar extremo: cada olho transita sem sair da sua metade", not edge)
    rb.attention.look_at(1.0, 0.0, 2.0, 3.1)
    for i in range(60):
        rb.step([], 3.1 + i / 60, 1 / 60)
    rb.expr.blink_now()
    for i in range(4):
        rb.step([], 4.2 + i / 60, 1 / 60)
    c_blink = al.render_canvas(rb.last_render_args)
    check("piscar apaga a maior parte dos olhos", c_blink.sum() < 0.6 * c_right.sum())
    big = al.render_canvas(rb.last_render_args, "grande").sum() if False else None
    rb2 = Robot((80, 45))
    rb2.expr.auto_blink = False
    frame_for(rb2)
    small, large = al.render_canvas(rb2.last_render_args, "pequeno"), al.render_canvas(rb2.last_render_args, "grande")
    check("tamanho dos olhos ajustável", small.sum() < large.sum())
    rb3 = Robot((80, 45))
    rb3.set_feature("mouth", True)
    frame_for(rb3)
    a_screen = rb3.last.alpha.copy()
    al.render_canvas(rb3.last_render_args)
    check("interface padrão intacta (render do painel não altera o quadro da tela)",
          np.array_equal(a_screen, rb3.last.alpha) and a_screen.shape == (45, 80))
    if png:
        import cv2
        out = Path(__file__).parent / "previews"
        out.mkdir(exist_ok=True)
        for name, img in (("neutro", c), ("olhando_direita", c_right), ("teste", al.test_pattern())):
            big = np.kron(img.astype(np.uint8) * 255, np.ones((24, 24), np.uint8))
            big = cv2.cvtColor(big, cv2.COLOR_GRAY2BGR)
            big[:, ::192] = (90, 90, 90)
            cv2.imwrite(str(out / f"arduino_{name}.png"), big)


def test_encode():
    print("Mapeamento dos módulos e protocolo")
    c = np.zeros((8, 32), bool)
    c[0, 0] = True                                        # canto superior esquerdo
    s = al.ArduinoSettings(input_right=True)
    b = al.encode(c, s)
    check("32 bytes por quadro", len(b) == 32)
    check("entrada à direita: canto esquerdo vai no último módulo, linha 0, bit 7", b[24] == 0x80 and sum(b) == 0x80)
    s.input_right = False
    check("entrada à esquerda: canto esquerdo no módulo 0", al.encode(c, s)[0] == 0x80)
    s.rotation = 90
    check("giro de 90°: canto vai para o fim da linha 0", al.encode(c, s)[0] == 0x01)
    s.rotation, s.mirror = 0, True
    check("espelho: canto vai para o bit 0", al.encode(c, s)[0] == 0x01)
    s.mirror, s.upside_down = False, True
    check("de cabeça para baixo: canto vai para o último módulo, linha 7, bit 0", al.encode(c, s)[31] == 0x01)
    p = al.packet(al.CMD_FRAME, bytes(range(32)))
    x = 0
    for v in range(32):
        x ^= v
    check("pacote: sincronismo, comando, dados e XOR", p[:2] == b"\xA5\x01" and p[-1] == x and len(p) == 35)
    fw = FirmwareParser()
    fw.feed(b"\x00\x13" + al.packet(al.CMD_FRAME, bytes(range(32))) + al.packet(al.CMD_BRIGHT, b"\x07"))
    bad = bytearray(al.packet(al.CMD_FRAME, bytes(32)))
    bad[-1] ^= 0xFF
    fw.feed(bytes(bad))
    check("firmware (lógica espelhada): aceita quadro e brilho, ignora lixo e XOR errado",
          fw.frame == bytes(range(32)) and fw.brightness == 7)
    t = al.test_pattern()
    check("padrão de teste: ponto no canto e seta para a direita", t[0, 0] and t[4, 5] and t[4, 28])


def test_settings():
    print("Configuração da aba Arduino")
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "arduino.local.json"
        s = al.ArduinoSettings.load(p)
        check("padrão: desligado, porta automática, brilho baixo", not s.enabled and s.port == "auto" and s.brightness <= 4)
        s.enabled, s.port, s.rotation, s.test = True, "COM5", 270, True
        s.save(p)
        r = al.ArduinoSettings.load(p)
        check("preferências salvas e recarregadas (teste nunca reabre ligado)",
              r.enabled and r.port == "COM5" and r.rotation == 270 and not r.test)
        p.write_text('{"rotation": 45, "brightness": 99}', encoding="utf-8")
        r = al.ArduinoSettings.load(p)
        check("valores inválidos corrigidos", r.rotation == 0 and r.brightness == 15)
    import menu
    from renderer import HudInfo
    m = menu.OptionsMenu()
    hud = HudInfo()
    hud.arduino = dict(enabled=True, status="conectado · COM5", port_label="COM5", brightness=4, size="medio")
    root = [i[0] for i in m.items(hud, None.__class__) ] if False else None
    m.view = "root"
    import theme
    roots = [i[0] for i in m.items(hud, theme.SCHEMES["dark"])]
    check("aba Arduino no menu de Ajustes", "arduino" in roots)
    consumed, action = m.activate("arduino")
    items = m.items(hud, theme.SCHEMES["dark"])
    check("aba lista painel, porta, brilho, tamanho, teste e montagem",
          m.view == "arduino" and [i[0].split(":")[1] for i in items] ==
          ["enabled", "port", "brightness", "size", "test", "order", "rotate", "mirror", "flip"])
    consumed, action = m.activate("arduino:rotate")
    check("tocar num ajuste mantém a aba aberta e devolve a ação", m.open is False or m.view == "arduino")
    check("ação chega ao app", action == "arduino:rotate")


def test_link():
    print("Conexão serial (simulada)")
    ports = [("COM3", "Bluetooth"), ("COM7", "Arduino Uno")]
    opened = {}

    def factory(port):
        s = FakeSerial(port, silent=(port == "COM3"))
        opened[port] = s
        return s
    link = al.ArduinoLink(factory, ports=lambda: ports, reset_wait=0.05, fps=60)
    link.start("auto")
    check("detecção automática acha o Arduino pelo firmware (ignora outra porta)",
          wait(lambda: link.connected) and link.port == "COM7" and "COM7" in link.status)
    link.set_brightness(6)
    frame = bytes([0x18] * 32)
    link.submit(frame)
    fw = opened["COM7"].fw
    check("brilho e quadro chegam ao firmware", wait(lambda: fw.frame == frame and fw.brightness == 6))
    n = link.frames_sent
    time.sleep(1.3)
    check("quadro parado é reenviado ~1×/s (o firmware não apaga)", link.frames_sent > n)
    link.submit(bytes([0x3C] * 32))
    check("novo quadro substitui o anterior", wait(lambda: fw.frame == bytes([0x3C] * 32)))
    opened["COM7"].fail_after = 0
    link.submit(bytes([0x7E] * 32))
    check("cabo desconectado: status avisa e tenta de novo", wait(lambda: "desconectado" in link.status or not link.connected))
    check("reconecta sozinho quando a porta volta", wait(lambda: link.connected, 6))
    link.stop()
    last = opened["COM7"]
    check("ao sair, apaga o painel e fecha a porta", last.closed and last.fw.frame == bytes(32))

    link = al.ArduinoLink(lambda p: FakeSerial(p, version=1), ports=lambda: [("COM9", "x")], reset_wait=0.05)
    link.start("auto")
    check("firmware antigo (v1): conecta e avisa para gravar de novo",
          wait(lambda: link.connected) and "firmware antigo" in link.status)
    link.stop()

    link = al.ArduinoLink(lambda p: FakeSerial(p, silent=True), ports=lambda: [("COM9", "x")], reset_wait=0.05)
    link.start("auto")
    check("sem firmware gravado: orienta gravar o nuria_leds", wait(lambda: "grave" in link.status, 5))
    link.stop()

    def broken(port):
        raise OSError("Acesso negado")
    link = al.ArduinoLink(broken, ports=lambda: [], reset_wait=0.05)
    link.start("auto")
    check("nenhuma porta: status claro, app segue", wait(lambda: "nenhuma porta" in link.status))
    link.stop()
    link = al.ArduinoLink(broken, ports=lambda: [], reset_wait=0.05)
    link.start("COM4")
    check("porta ocupada (ex.: Monitor Serial aberto): mostra o erro", wait(lambda: "COM4" in link.status and "não abriu" in link.status))
    link.stop()


def test_app_smoke():
    print("App com a aba Arduino ligada (sem placa)")
    import main as app
    with tempfile.TemporaryDirectory() as d:
        orig = al.CONFIG_PATH
        al.CONFIG_PATH = Path(d) / "arduino.local.json"
        al.ArduinoSettings(enabled=True).save()
        try:
            code = app.main(["--sim", "--ia", "simulada", "--sem-registro", "--run-seconds", "2"])
        finally:
            al.CONFIG_PATH = orig
        check("app roda e fecha normalmente com o painel ligado e sem Arduino", code == 0)


if __name__ == "__main__":
    png = "--no-png" not in sys.argv
    test_render(png)
    test_encode()
    test_settings()
    test_link()
    test_app_smoke()
    ok = sum(1 for _, v in RESULTS if v)
    print(f"\n{ok}/{len(RESULTS)} verificações OK (porta serial simulada; validar com a placa real)")
    sys.exit(0 if ok == len(RESULTS) else 1)
