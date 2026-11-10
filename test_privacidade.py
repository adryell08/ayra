"""Privacidade da tela pública: IA e operador não se distinguem e a presença do operador não aparece.
Detalhes de conexão ficam só no painel privado (sala do operador) e nos Ajustes locais. Tudo simulado.
Uso: python test_privacidade.py
"""
from __future__ import annotations

import os
import queue
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import pygame

import theme

RESULTS = []
FORBIDDEN = re.compile(r"operador|teleop|em liga[cç][aã]o|vendo e ouvindo|operator|\bAna\b", re.I)


def check(name, cond):
    RESULTS.append((name, bool(cond)))
    print(f"  [{'OK' if cond else 'FALHOU'}] {name}")


def capture_texts(renderer):
    seen = []
    original = renderer._text

    def spy(role, text, color):
        seen.append(str(text))
        return original(role, text, color)
    return seen, spy


def test_panel_same_for_ai_and_operator():
    print("Painel e HUD: o mesmo texto com IA ou operador")
    from assistente import Snapshot
    import painel_assistente as pa
    from renderer import HudInfo, Renderer
    from robot import Robot
    pygame.init()
    frame = Robot((80, 45)).step([], 0.0, 1 / 60)

    class Fake:
        def __init__(self, **kw):
            self.snap = Snapshot(**kw)

        def snapshot(self):
            return self.snap

        def backend_label(self):
            return "Realtime"

        def can_repeat(self):
            return False

    all_equal, leaked = True, []
    for state in ("idle", "listening", "thinking", "speaking"):
        for continuous in (False, True):
            texts = []
            for mode, operator in (("autonomo", ""), ("teleoperado", "Ana")):
                screen = pygame.display.set_mode((1280, 720))
                r = Renderer(screen, (80, 45), theme.SCHEMES["dark"])
                panel = pa.AssistantPanel()
                r.reserve_right = r.menu.reserve_right = panel.reserve(screen, r.s)
                r.resize(screen)
                seen, spy = capture_texts(r)
                hud = HudInfo(camera_status="ok")
                hud.remote = dict(enabled=True, operator=operator, badge=True, status="operador conectado")
                with patch.object(r, "_text", spy):
                    r.draw_panel(frame, 1 / 60, False)
                    panel.draw(r, Fake(state=state, mode=mode, operator=operator, continuous=continuous,
                                       answer="resposta", question="pergunta"), 1 / 60, False, "Realtime")
                    r.draw_hud(frame, hud)
                texts.append(sorted(seen))
                leaked += [t for t in seen if FORBIDDEN.search(t)]
            all_equal &= texts[0] == texts[1]
    check("textos desenhados são idênticos com IA (autônomo) e operador (teleoperado), em todos os estados", all_equal)
    check("nenhum texto público cita operador, teleoperação, 'Em ligação' ou o nome (%s)" % (leaked[:3] or "ok"), not leaked)


def test_app_loop():
    print("Aplicativo: conexão do operador e comandos não aparecem na tela do robô")
    import main as app
    import operador
    from renderer import Renderer

    class FakeServer:
        def __init__(self, *a, **k):
            self.status, self.url, self.pin, self.operator, self.memory = "operador conectado · Ana", "http://x", "123456", "Ana", None
            self.fps = 10
            self.commands = queue.Queue()
            for item in (("operator", "Ana"), ("mode", {"modo": "teleoperado"}), ("instruction", {"texto": "seja gentil"}),
                         ("gesture", {"nome": "tchau"}), ("expression", {"nome": "wink"}), ("say", {"texto": "olá"}),
                         ("mode", {"modo": "autonomo"}), ("operator", "")):
                self.commands.put(item)

        def start(self):
            return True

        def stop(self):
            pass

    real_read, real_write = Path.read_text, Path.write_text

    def read_text(self, *a, **k):
        if self.name == "remote.local.json":
            return '{"enabled": true, "port": 8765, "pin": "123456", "badge": true, "fps": 10}'
        return real_read(self, *a, **k)

    def write_text(self, data, *a, **k):
        return len(data) if self.name == "remote.local.json" else real_write(self, data, *a, **k)

    seen, toasts, chips = [], [], []
    orig_text, orig_chip, orig_hud = Renderer._text, Renderer._chip, Renderer.draw_hud

    def spy_text(self, role, text, color):
        seen.append(str(text))
        return orig_text(self, role, text, color)

    def spy_chip(self, lines, *a, **k):
        chips.extend(map(str, lines))
        return orig_chip(self, lines, *a, **k)

    def spy_hud(self, frame, hud):
        if hud.toast:
            toasts.append(hud.toast)
        return orig_hud(self, frame, hud)

    with patch.object(operador, "OperatorServer", FakeServer), patch.object(Path, "read_text", read_text), \
            patch.object(Path, "write_text", write_text), patch.object(Renderer, "_text", spy_text), \
            patch.object(Renderer, "_chip", spy_chip), patch.object(Renderer, "draw_hud", spy_hud):
        try:
            code, error = app.main(["--sim", "--sem-registro", "--run-seconds", "3"]), ""
        except Exception as e:
            code, error = -1, f"{type(e).__name__}: {e}"
    check(f"app roda com operador conectando, trocando de modo e comandando {error}", code == 0 and not error)
    leaked = sorted({t for t in seen + toasts + chips if FORBIDDEN.search(t)})
    check("nenhum texto da tela do robô (toasts, chips, painel, HUD) revela o operador (%s)" % (leaked[:3] or "ok"), not leaked)
    check("o aviso de instrução é genérico", "Instrução registrada" in toasts or not any("nstrução" in t for t in toasts))
    check("a conexão não gera toast público", not any("onect" in t for t in toasts))


def test_static():
    print("Código: superfícies públicas sem presença do operador")
    msrc = (HERE / "main.py").read_text(encoding="utf-8")
    check("main.py não desenha mais a barra 'Operador conectado · nome · vendo e ouvindo'", "vendo e ouvindo" not in msrc
          and "Operador conectado" not in msrc and "Operador desconectado" not in msrc)
    import menu
    import theme as th
    from renderer import HudInfo
    m = menu.OptionsMenu()
    hud = HudInfo()
    hud.remote = dict(enabled=True, operator="Ana", url="https://x", pin="123456", status="ok", fps=10)
    m.activate("remote")
    items = m.items(hud, th.SCHEMES["dark"])
    check("Ajustes (privados) mantêm os detalhes de conexão, mas sem o item da barra pública",
          any(i[0] == "remote:kick" for i in items) and not any(i[0] == "remote:badge" for i in items))
    csrc = (HERE / "conversas_ui.py").read_text(encoding="utf-8")
    check("histórico no robô não distingue a origem das instruções (operador/painel/usuário)",
          '"operator": "instrução", "painel": "instrução", "user": "instrução"' in csrc and '"operador"' not in csrc)
    psrc = (HERE / "painel_assistente.py").read_text(encoding="utf-8")
    check("painel do robô sem 'Em ligação' nem modo teleoperado", "Em ligação" not in psrc and "teleoperado" not in psrc.replace("# mesmo texto", ""))
    html = (HERE / "operador_web" / "index.html").read_text(encoding="utf-8")
    check("o painel privado continua mostrando conexão e diagnósticos", "voiceDiag" in html and "Estado" in html)


if __name__ == "__main__":
    test_panel_same_for_ai_and_operator()
    test_app_loop()
    test_static()
    ok = sum(1 for _, v in RESULTS if v)
    print(f"\n{ok}/{len(RESULTS)} verificações OK (simuladas; sem operador real nem rede)")
    sys.exit(0 if ok == len(RESULTS) else 1)
