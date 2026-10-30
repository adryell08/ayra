"""Histórico de conversas, repetir resposta e encerrar por voz (tudo simulado; sem rede, câmera ou áudio reais).

Uso: python test_historico.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import assistente
import historico
from assistente_realtime import is_end_command
from test_realtime import Harness, check, RESULTS


def write(d: Path, rows):
    with open(d / "sessoes.jsonl", "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def test_store():
    print("Histórico: leitura, busca, apagar")
    d = Path(tempfile.mkdtemp())
    check("sem arquivo: lista vazia", historico.list_sessions(log_dir=d) == [])
    write(d, [
        dict(quando="2026-10-28 10:00:00", sessao="A", pergunta="Que cor é a camisa?", resposta="Azul."),
        dict(quando="2026-10-28 10:01:00", sessao="A", pergunta="E a parede?", resposta="Branca."),
        dict(quando="2026-10-29 09:00:00", sessao="B", pergunta="Quem é você?", resposta="Sou a Ayra."),
        dict(quando="2026-10-27 08:00:00", pergunta="Antiga", resposta="Registro sem id."),
        dict(quando="2026-10-29 09:05:00", sessao="B", erro="falha de rede"),
    ])
    rows = historico.list_sessions(log_dir=d)
    check("agrupa por conversa, mais recente primeiro", [r["id"] for r in rows] == ["B", "A", "legado-2026-10-27"])
    check("título = primeira pergunta; conta só trocas (erro não conta)",
          rows[1]["title"] == "Que cor é a camisa?" and rows[1]["turns"] == 2 and rows[0]["turns"] == 1)
    check("busca ignora acento e maiúscula, em pergunta e resposta",
          [r["id"] for r in historico.list_sessions("PAREDE branca", log_dir=d)] == ["A"]
          and [r["id"] for r in historico.list_sessions("ayra", log_dir=d)] == ["B"]
          and historico.list_sessions("inexistente", log_dir=d) == [])
    check("carrega as trocas em ordem", historico.load("A", d) == [("Que cor é a camisa?", "Azul."), ("E a parede?", "Branca.")])
    check("apagar remove só aquela conversa e devolve quantos registros",
          historico.delete("A", d) == 2 and [r["id"] for r in historico.list_sessions(log_dir=d)] == ["B", "legado-2026-10-27"])
    check("linha corrompida não derruba a leitura", True)
    with open(d / "sessoes.jsonl", "a", encoding="utf-8") as fh:
        fh.write("{quebrado\n")
    check("…e é preservada ao apagar outra", historico.delete("B", d) >= 1
          and "{quebrado" in (d / "sessoes.jsonl").read_text(encoding="utf-8"))


def test_assistant():
    print("Conversa: id no registro, nova, retomar, repetir")
    d = Path(tempfile.mkdtemp())
    old_dir = assistente.LOG_DIR
    assistente.LOG_DIR = d
    historico.LOG_DIR = d
    h = Harness()
    try:
        h.a._log = True
        h.a.press_voice()
        ok = h.idle()
        rows = [json.loads(x) for x in (d / "sessoes.jsonl").read_text(encoding="utf-8").splitlines()]
        check("pergunta respondida e registrada com o id da conversa", ok and rows and rows[-1]["sessao"] == h.a.session_id)
        check("o histórico enxerga a conversa", [s["id"] for s in historico.list_sessions()] == [h.a.session_id])

        total = h.player.received
        resp_before = len(h.sent("response.create"))
        check("depois de responder, pode repetir", h.a.can_repeat())
        check("repetir toca o áudio de novo", h.a.repeat_last())
        check("…e termina em pronta", h.idle())
        check("…sem pedir nova resposta à IA nem nova imagem",
              len(h.sent("response.create")) == resp_before and h.player.played == h.player.received == total > 0)
        check("…e pode repetir de novo", h.a.can_repeat())

        old_id = h.a.session_id
        n_servers = len(h.servers)
        h.a.new_conversation()
        check("nova conversa: histórico vazio e sem repetição pendente",
              h.a.history == [] and not h.a.can_repeat())
        h.a.press_voice()
        check("a próxima pergunta abre sessão nova no provedor", h.idle() and len(h.servers) == n_servers + 1)
        ctx = [e for e in h.servers[-1].received if e["type"] == "conversation.item.create"
               and "Contexto da conversa anterior" in json.dumps(e)]
        check("…sem levar o assunto antigo", not ctx)

        n_servers = len(h.servers)
        turns = h.a.resume_session(old_id)
        check("retomar devolve o número de trocas e repõe o contexto", turns >= 1 and h.a.history and h.a.session_id == old_id)
        h.a.press_voice()
        check("…e abre sessão nova levando as trocas em texto", h.idle() and len(h.servers) == n_servers + 1
              and any("Contexto da conversa anterior" in json.dumps(e) for e in h.servers[-1].received))
        check("retomar conversa inexistente não mexe em nada", h.a.resume_session("nao-existe") == 0 and h.a.session_id == old_id)
    finally:
        h.close()
        assistente.LOG_DIR = old_dir
        historico.LOG_DIR = old_dir


def test_end_command():
    print("Comando de voz para encerrar")
    yes = ["Nuria, encerrar conversa.", "encerrar conversa", "Pode encerrar a conversa", "Ayra, encerra a escuta por favor",
           "Terminar conversa", "finalizar a ligação"]
    no = ["para", "encerrar", "não encerre a conversa ainda", "como faço para encerrar a conversa com meu chefe",
          "o que é uma conversa", "", "encerrar conversa e me conte uma piada sobre o mar"]
    check("aceita frases inequívocas", all(is_end_command(t) for t in yes))
    check("recusa palavras soltas e frases com outro sentido", not any(is_end_command(t) for t in no))
    h = Harness()
    try:
        h.a._new_turn(h.a._gen, time.perf_counter())
        h.a._turn["responding"] = False
        h.a._on_event({"type": "conversation.item.input_audio_transcription.completed", "transcript": "Nuria, encerrar conversa"})
        h.wait(lambda: h.a._turn.get("ended"))
        s = h.a.snapshot()
        check("encerra o turno, volta a pronta e avisa", h.a._turn.get("ended") and s.state == "idle" and "encerrada" in s.message)
        n = len(h.sent("response.create"))
        h.a._respond(h.a._gen, time.perf_counter(), {})
        check("resposta atrasada de um turno encerrado não é pedida", len(h.sent("response.create")) == n)
        h.a.set_continuous(True)
        h.a._new_turn(h.a._gen, time.perf_counter())
        h.a._on_event({"type": "conversation.item.input_audio_transcription.completed", "transcript": "encerrar conversa"})
        check("com o microfone travado, solta o microfone", h.wait(lambda: not h.a.continuous))
        h.a.set_mode("teleoperado")
        h.a._new_turn(h.a._gen, time.perf_counter())
        h.a._on_event({"type": "conversation.item.input_audio_transcription.completed", "transcript": "encerrar conversa"})
        time.sleep(0.1)
        check("no modo teleoperado o comando é ignorado (quem fala é o operador)", not h.a._turn.get("ended"))
    finally:
        h.close()


def test_tela_conversas():
    print("Tela Conversas (lista + balões)")
    import pygame
    import theme
    from menu import OptionsMenu
    from renderer import Renderer
    from conversas_ui import ChatHistory
    pygame.init()
    d = Path(tempfile.mkdtemp())
    rows = []
    for i in range(12):
        rows.append(dict(quando=f"2026-10-{10 + i:02d} 09:00:00", sessao=f"S{i}", pergunta=f"Pergunta número {i}", resposta=f"Resposta {i}."))
    rows += [dict(quando="2026-10-29 10:00:00", sessao="S11", pergunta="E a parede? " * 12, resposta="Branca, com um quadro pequeno ao fundo. " * 6)]
    write(d, rows)
    old = historico.LOG_DIR
    historico.LOG_DIR = d
    try:
        screen = pygame.display.set_mode((1280, 720))
        r = Renderer(screen, (80, 45), theme.SCHEMES["dark"])
        m = OptionsMenu()
        check("o menu tem 'Conversas' e ela abre a tela (ação devolvida ao app)", m.activate("history") == (True, "history"))
        from renderer import HudInfo
        hud = HudInfo(camera_status="ok")
        r.menu.draw(r, hud)
        check("o dock tem 5 ícones: câmera, aparência, movimento, conversas, ajustes", len(r.menu.dock) == 5)
        pygame.image.save(screen.subsurface(r.menu.dock[0].union(r.menu.dock[-1]).inflate(60, 60).clip(screen.get_rect())),
                          str(Path(tempfile.gettempdir()) / "dock.png"))
        out = None
        for kind in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
            out = r.handle_event(pygame.event.Event(kind, button=1, pos=r.menu.dock[3].center), hud)
        check("tocar no ícone de conversas pede a tela de conversas", out[1] == "history")
        r.menu.dock_focus = 3
        out = r.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, mod=0, unicode=""), hud)
        check("pelo teclado (F6, setas, Enter) também", out[1] == "history")
        c = ChatHistory()
        c.open("S11")
        check("abre na conversa atual", c.selected["id"] == "S11" and c.active)
        c.draw(r, "S11")
        check("desenha lista e balões (retomar/apagar/nova com área clicável)",
              c.resume_btn.w and c.delete_btn.w and c.new_btn.w and len(c.rows) >= 5 and c._bubbles)
        check("balões: do usuário à direita e do robô à esquerda",
              [b[0] for b in c._bubbles] == ["user", "bot", "user", "bot"])
        pygame.image.save(screen, str(Path(tempfile.gettempdir()) / "conversas.png"))
        for ch in "parede":
            c.handle_event(pygame.event.Event(pygame.TEXTINPUT, text=ch))
        check("busca filtra a lista enquanto digita", [s["id"] for s in c._sessions] == ["S11"])
        c.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE, mod=0, unicode=""))
        check("Esc limpa a busca antes de fechar", c.active and c.query == "" and len(c._sessions) == 12)
        c.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_DOWN, mod=0, unicode=""))
        check("setas trocam de conversa", c.selected["id"] == "S10")
        res = c.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, mod=0, unicode=""))
        check("Enter retoma a conversa escolhida e fecha", res == ("resume", "S10") and not c.active)
        c.open("")
        c.draw(r, "")
        def click(rect):
            for typ in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
                out = c.handle_event(pygame.event.Event(typ, button=1, pos=rect.center))
            return out
        check("Apagar pede confirmação (1º toque não apaga)", click(c.delete_btn) is None and c.confirm_delete)
        c.draw(r, "")
        sid = c.selected["id"]
        check("2º toque apaga e avisa o app", click(c.delete_btn) == ("delete", sid))
        c.draw(r, "")
        check("Nova conversa devolve a ação", click(c.new_btn)[0] == "new" and not c.active)
        c.open("")
        c.draw(r, "")
        c.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=-3))
        c.draw(r, "")
        check("rolagem não quebra o desenho", c.active)
    finally:
        historico.LOG_DIR = old


if __name__ == "__main__":
    test_store()
    test_assistant()
    test_end_command()
    test_tela_conversas()
    failed = [n for n, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} verificações OK")
    sys.exit(1 if failed else 0)
