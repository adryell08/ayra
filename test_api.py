"""Testes da aba Ajustes → API (chave pela interface). Sem rede: conexão simulada.

Uso: python test_api.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pygame

import credenciais
import entrada_chave
import realtime as rt
from assistente_realtime import check_connection

RESULTS = []
KEY = "sk-teste-0123456789abcdefWXYZ"


def check(name, cond):
    RESULTS.append((name, bool(cond)))
    print(f"  [{'OK' if cond else 'FALHOU'}] {name}")


def test_env_file():
    print("Arquivo .env")
    with tempfile.TemporaryDirectory() as d:
        env = Path(d) / ".env"
        env.write_text("# minha nota\nOPENAI_API_KEY=sk-velha-000000000000000\nNURIA_RUIDO=near_field\n", encoding="utf-8")
        credenciais.set_env_values({"OPENAI_API_KEY": KEY}, env)
        text = env.read_text(encoding="utf-8")
        check("troca a chave preservando comentários e outras opções",
              f"OPENAI_API_KEY={KEY}" in text and "# minha nota" in text and "NURIA_RUIDO=near_field" in text
              and "sk-velha" not in text)
        credenciais.set_env_values({"NURIA_MODELO_REALTIME": "gpt-realtime-2.1-mini"}, env)
        check("acrescenta opção nova sem duplicar", env.read_text().count("NURIA_MODELO_REALTIME") == 1
              and credenciais.load(env, environ={}).realtime_model == "gpt-realtime-2.1-mini")
        credenciais.set_env_values({"OPENAI_API_KEY": None}, env)
        check("remover apaga só a chave", "OPENAI_API_KEY" not in env.read_text() and "NURIA_RUIDO" in env.read_text()
              and not credenciais.load(env, environ={}).configured)
        new = Path(d) / "novo" / ".env"
        new.parent.mkdir()
        credenciais.set_env_values({"OPENAI_API_KEY": KEY}, new)
        check("cria o .env se não existir", credenciais.load(new, environ={}).api_key == KEY)
    check("máscara mostra só prefixo e 4 últimos", credenciais.mask(KEY) == "sk-…WXYZ" and KEY[5:20] not in credenciais.mask(KEY))
    check("validação: sk-, tamanho e sem espaços", credenciais.looks_valid(KEY) and not credenciais.looks_valid("abc")
          and not credenciais.looks_valid("sk-com espaço 12345678901"))
    gi = (Path(__file__).parent / ".gitignore").read_text(encoding="utf-8")
    check(".env continua fora do Git", ".env" in gi)


def test_entry():
    print("Folha de entrada da chave")
    pygame.init()
    pygame.display.set_mode((1280, 720))
    e = entrada_chave.KeyEntry()
    e.open()
    for ch in KEY[:10]:
        e.handle_event(pygame.event.Event(pygame.TEXTINPUT, text=ch))
    entrada_chave.clipboard_text = lambda: "  " + KEY[10:] + "\n"
    e.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_v, mod=pygame.KMOD_CTRL))
    check("digitar + colar (Ctrl+V, sem espaços/quebras)", e.text == KEY)
    check("tela nunca mostra a chave inteira", KEY[:-4] not in e.masked() and e.masked().endswith("WXYZ"))
    q = e.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_q, mod=0))
    check("teclas de atalho não vazam (Q não fecha o app)", q is None and e.active)
    r = e.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, mod=0))
    check("Enter salva e fecha", r == ("save", KEY) and not e.active and e.text == "")
    e.open()
    for ch in "chave errada":
        e.handle_event(pygame.event.Event(pygame.TEXTINPUT, text=ch))
    r = e.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, mod=0))
    check("chave inválida: mensagem e continua aberta", r is None and e.active and "sk-" in e.error)
    r = e.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE, mod=0))
    check("Esc cancela", r == ("cancel", "") and not e.active)
    import theme
    from renderer import Renderer
    rnd = Renderer(pygame.display.get_surface(), (80, 45), theme.SCHEMES["dark"])
    e.open()
    e.text = KEY
    e.draw(rnd, "windows")
    check("botões com alvo ≥ 44 pt dentro da folha", all(b.h >= 44 and e.rect.contains(b)
                                                          for b in (e.save_btn, e.paste_btn, e.cancel_btn)))
    pygame.image.save(rnd.screen, str(Path(__file__).parent / "previews" / "nuria_api_chave.png"))
    down = pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=e.save_btn.center)
    up = pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=e.save_btn.center)
    e.handle_event(down)
    check("botão Salvar (pressionar e soltar)", e.handle_event(up) == ("save", KEY))


def test_menu_and_connection():
    print("Aba API e teste de conexão")
    import menu
    import theme
    from renderer import HudInfo
    m = menu.OptionsMenu()
    hud = HudInfo()
    hud.api = dict(configured=True, masked="sk-…WXYZ", model="gpt-realtime-2.1", voice="marin")
    roots = m.items(hud, theme.SCHEMES["dark"])
    check("aba API no menu, com a chave mascarada", any(a == "api" and "sk-…WXYZ" in st for a, _, st, _ in roots))
    m.activate("api")
    names = [a for a, *_ in m.items(hud, theme.SCHEMES["dark"])]
    check("itens: chave, testar, remover, modelo, voz",
          names == ["api:key", "api:test", "api:remove", "api:model", "api:voice"])
    check("tocar num item mantém a aba aberta", m.activate("api:test") == (True, "api:test") and m.view == "api")
    check("nenhum texto da aba contém a chave", KEY not in str(m.items(hud, theme.SCHEMES["dark"])))
    cfg = credenciais.load(Path("/nao/existe"), environ={"OPENAI_API_KEY": KEY})
    ok, msg = check_connection(cfg, transport_factory=lambda: rt.FakeRealtimeServer())
    check("testar conexão: OK com servidor (simulado)", ok and "conectou" in msg)

    def down():
        raise rt.TransportClosed("Chave da OpenAI inválida ou revogada. Confira o arquivo .env.")
    ok, msg = check_connection(cfg, transport_factory=down)
    check("testar conexão: mostra o motivo da falha", not ok and "inválida" in msg)
    ok, msg = check_connection(credenciais.load(Path("/nao/existe"), environ={}))
    check("sem chave: avisa sem tentar conectar", not ok and "sem chave" in msg)
    check("repr da configuração não vaza a chave", KEY not in repr(cfg))


if __name__ == "__main__":
    test_env_file()
    test_entry()
    test_menu_and_connection()
    ok = sum(1 for _, v in RESULTS if v)
    print(f"\n{ok}/{len(RESULTS)} verificações OK")
    sys.exit(0 if ok == len(RESULTS) else 1)
