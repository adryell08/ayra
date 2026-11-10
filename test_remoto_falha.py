"""Falha ao subir o servidor do operador (porta ocupada etc.): o app avisa, segue sem operador e NÃO regrava
remote.local.json. Tudo simulado: nenhuma porta real é aberta e o arquivo do usuário não é lido nem alterado.
Uso: python test_remoto_falha.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import pygame

RESULTS = []


def check(name, cond):
    RESULTS.append((name, bool(cond)))
    print(f"  [{'OK' if cond else 'FALHOU'}] {name}")


class FakeServer:
    """Substitui OperatorServer: o bind sempre falha, sem tocar em rede."""
    instances = []

    def __init__(self, *a, **k):
        self.status = "Porta 8765 ocupada (simulada)"
        self.url = self.pin = self.operator = ""
        self.memory = None
        FakeServer.instances.append(self)

    def start(self):
        return False

    def stop(self):
        pass


def test_remote_start_failure():
    print("Controle remoto: falha ao subir o servidor na partida")
    import main as app
    import operador
    real_read, real_write = Path.read_text, Path.write_text
    writes, toasts = [], []

    def read_text(self, *a, **k):
        if self.name == "remote.local.json":
            return '{"enabled": true, "port": 8765, "pin": "123456", "badge": true, "fps": 10}'
        return real_read(self, *a, **k)

    def write_text(self, data, *a, **k):
        if self.name == "remote.local.json":
            writes.append(data)
            return len(data)
        return real_write(self, data, *a, **k)

    original = app.Renderer.draw_hud

    def observe(renderer, frame, hud):
        if hud.toast:
            toasts.append(hud.toast)
        original(renderer, frame, hud)

    with patch.object(operador, "OperatorServer", FakeServer), patch.object(Path, "read_text", read_text), \
            patch.object(Path, "write_text", write_text), \
            patch.object(app.Renderer, "draw_hud", autospec=True, side_effect=observe):
        try:
            code = app.main(["--sim", "--sem-registro", "--run-seconds", "1"])
            error = ""
        except Exception as e:                        # NameError de `toast` era o defeito
            code, error = -1, f"{type(e).__name__}: {e}"
    check(f"partida com servidor remoto que falha não quebra o app {error}", code == 0 and not error)
    check("o servidor simulado foi mesmo tentado (sem abrir porta real)", len(FakeServer.instances) == 1)
    check("o motivo da falha aparece como aviso na tela do robô", any("8765" in t for t in toasts))
    check("a preferência gravada em remote.local.json não é alterada pela falha", writes == [])


if __name__ == "__main__":
    pygame.init()
    test_remote_start_failure()
    ok = sum(1 for _, v in RESULTS if v)
    print(f"\n{ok}/{len(RESULTS)} verificações OK (simuladas; não abre a porta do operador)")
    sys.exit(0 if ok == len(RESULTS) else 1)
