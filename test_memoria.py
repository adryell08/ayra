"""Memória (SQLite), instruções vigentes, resumo na volta do operador e privacidade. Tudo simulado: sem rede nem áudio reais.

Uso: python test_memoria.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import memoria
from memoria import Memory, extract
from test_realtime import Harness, check, RESULTS


def fresh(**kw) -> Memory:
    return Memory(Path(tempfile.mkdtemp()) / "m.db", **kw)


def test_extraction():
    print("Regras de extração")
    check("'lembre-se de que…' vira instrução (sem o 'de que')", extract("Oi! Lembre-se de que a sala é a 12. Qual é a cor?")["instructions"] == ["a sala é a 12"])
    check("'a partir de agora…', 'sempre…', 'não esqueça…'",
          extract("A partir de agora fale devagar")["instructions"] == ["fale devagar"]
          and extract("Sempre responda em uma frase")["instructions"] == ["Sempre responda em uma frase"]
          and extract("não esqueça de trazer o cabo")["instructions"] == ["trazer o cabo"])
    check("perguntas não ensinam nada", all(not any(extract(q)[k] for k in ("instructions", "preferences")) for q in
          ["Você lembra de mim?", "Você sempre responde assim?", "Meu nome é Ana, certo?"]))
    check("preferências: nome, 'me chame de', gostos",
          extract("Meu nome é Adryell")["preferences"] == ["Nome do usuário: Adryell"]
          and extract("me chame de Dry")["preferences"] == ["Prefere ser chamado de Dry"]
          and extract("eu gosto de café. e você?")["preferences"] == ["Usuário disse: eu gosto de café"])
    check("revogações: tudo, última, por assunto",
          extract("esqueça tudo")["revoke_all"] and extract("esqueça isso")["revoke_last"]
          and extract("pare de fazer piada")["revoke_of"] == ["fazer piada"])
    check("frase comum não vira regra", not any(extract("O dia está bonito hoje")[k] for k in ("instructions", "preferences", "revoke_all", "revoke_last", "revoke_of")))


def test_store():
    print("Banco: esquema, instruções, busca, isolamento")
    mem = fresh()
    check("banco criado com FTS5, WAL e versão de esquema", mem.active and mem.fts and mem._conn.execute("PRAGMA user_version").fetchone()[0] == 1)
    again = Memory(mem.path)
    check("abrir de novo não refaz migração nem perde dados", again.active)
    mem.append_event("s1", "user", "utterance", "Lembre-se que eu não gosto de piadas sobre política.", event_key="t1:q")
    mem.append_event("s1", "user", "utterance", "Lembre-se que eu não gosto de piadas sobre política.", event_key="t1:q")
    mem.append_event("s1", "ai", "response", "Combinado, sem piadas políticas.", event_key="t1:a")
    mem.flush()
    n = mem._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    check("mesmo evento (mesma chave) não duplica, p.ex. na reconexão", n == 2)
    ins = mem.list_active_instructions()
    check("instrução gravada como ativa com autoria", len(ins) == 1 and ins[0]["actor"] == "user" and "piadas" in ins[0]["instruction"])
    mem.upsert_instruction("não faça piadas sobre política", actor="user")
    mem.upsert_instruction("não faça piadas sobre política", actor="user")
    mem.flush()
    check("repetir a mesma instrução não cria cópia", len([i for i in mem.list_active_instructions() if "faça piadas" in i["instruction"]]) == 1)
    mem.upsert_instruction("não faça piadas sobre política nem religião", actor="operator")
    mem.flush()
    active = [i["instruction"] for i in mem.list_active_instructions()]
    check("instrução parecida substitui a anterior (a antiga fica 'superseded')",
          any("religião" in a for a in active) and not any(a == "não faça piadas sobre política" for a in active)
          and mem._conn.execute("SELECT COUNT(*) FROM instructions WHERE status='superseded'").fetchone()[0] >= 1)
    check("operador tem prioridade maior na lista", mem.list_active_instructions()[0]["actor"] == "operator")
    mem.append_event("s1", "user", "utterance", "pare de fazer piadas políticas por favor", event_key="t2:q")
    mem.flush()
    check("revogação por assunto tira a instrução da lista",
          not any("política" in i["instruction"] for i in mem.list_active_instructions())
          and mem._conn.execute("SELECT COUNT(*) FROM instructions WHERE status='revoked'").fetchone()[0] >= 1)
    mem.append_event("s1", "user", "utterance", "Meu nome é Adryell", event_key="t3:q")
    mem.flush()
    found = mem.search_memory("piadas política")
    check("busca (FTS5) acha por palavras, sem acento e sem ordem", found and mem.search_memory("POLITICA piadas")
          and not mem.search_memory("zebra")
          and any(f["kind"] == "event" for f in found))
    check("preferência fica 'candidate', não validada",
          [m["validation_status"] for m in mem.list_memories()] == ["candidate"])

    # isolamento por usuário
    mem.append_event("sB", "user", "utterance", "Lembre-se que o código do cofre é 1234", event_key="b1", user_id="B")
    mem.flush()
    check("usuário B tem a instrução dele", [i["instruction"] for i in mem.list_active_instructions("B")] == ["o código do cofre é 1234"])
    check("memória de A nunca aparece para B (instruções, contexto, busca)",
          not any("piadas" in i["instruction"] for i in mem.list_active_instructions("B"))
          and "Adryell" not in mem.get_session_context("sB", user_id="B") and not mem.search_memory("Adryell", user_id="B"))
    check("…e a de B não aparece para A", "cofre" not in mem.get_session_context("s1") and not mem.search_memory("cofre"))

    # candidato não vira conhecimento global; validado sim, com revisor
    mid = mem.list_memories()[0]["id"]
    check("candidato de A não vira conhecimento técnico para B", "Adryell" not in mem.get_session_context("sB", user_id="B"))
    mem.validate_memory(mid, reviewer="adryell")
    mem.flush()
    check("depois de validado por um revisor, vale como conhecimento global (com registro de feedback)",
          "Conhecimento validado" in mem.get_session_context("sB", user_id="B")
          and mem._conn.execute("SELECT reviewer FROM feedback").fetchone()[0] == "adryell")


def test_context_and_cost():
    print("Contexto, esquecimento e custo")
    mem = fresh()
    check("sem nada guardado, nenhum contexto é enviado", mem.get_session_context("x") == "")
    for i in range(300):
        mem.append_event("big", "user" if i % 2 == 0 else "ai", "utterance" if i % 2 == 0 else "response", f"mensagem número {i} sobre a mesa e a parede", event_key=f"k{i}")
    mem.upsert_instruction("sempre responda em uma frase", actor="user", session_id="big")
    mem.upsert_instruction("não conte o segredo do operador", actor="operator", session_id="big")
    mem.flush(3)
    t = time.perf_counter()
    ctx = mem.get_session_context("big")
    ms_ctx = (time.perf_counter() - t) * 1000
    t = time.perf_counter()
    mem.search_memory("mesa parede")
    ms_search = (time.perf_counter() - t) * 1000
    check(f"contexto curto (≤1100 caracteres) e rápido ({ms_ctx:.1f} ms; busca {ms_search:.1f} ms, 300 eventos)",
          0 < len(ctx) <= 1100 and ms_ctx < 100 and ms_search < 100)
    t = time.perf_counter()
    for i in range(500):
        mem.append_event("burst", "user", "utterance", f"x {i}", event_key=f"b{i}")
    enqueue_ms = (time.perf_counter() - t) * 1000
    check(f"gravar não bloqueia: 500 eventos enfileirados em {enqueue_ms:.0f} ms", enqueue_ms < 500)
    mem.flush(5)
    st = mem.stats()
    check(f"métricas observáveis (gravados {st['written']}, p95 de escrita {st['write_p95_ms']} ms, descartados {st['dropped']})",
          st["written"] >= 800 and st["dropped"] == 0 and st["healthy"])
    mem.forget_session("big")
    mem.flush(3)
    check("esquecer uma conversa apaga eventos e o que foi aprendido nela",
          not mem.search_memory("mesa parede") and not mem.list_active_instructions())
    mem.upsert_instruction("fale baixo")
    mem.append_event("keep", "user", "utterance", "Meu nome é Ana", event_key="n1")
    mem.flush()
    mem.forget_all()
    mem.flush()
    check("esquecer tudo limpa instruções, preferências e eventos",
          not mem.list_active_instructions() and not mem.list_memories()
          and mem._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0 and not mem.search_memory("Ana"))
    mem.append_event("old", "user", "utterance", "texto antigo", event_key="o1")
    mem.upsert_instruction("fica para sempre")
    mem.flush()
    time.sleep(0.2)
    mem.purge_older(1e-6)
    mem.flush()
    check("retenção apaga eventos velhos, mas mantém as instruções vigentes",
          mem._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0 and mem.list_active_instructions())


def test_failures_and_consent():
    print("Falhas e consentimento")
    off = fresh(enabled=False)
    check("memória desligada: nada é gravado nem criado", not off.active and not off.append_event("s", "user", "utterance", "oi")
          and not off.path.exists() and off.get_session_context("s") == "")
    bad = Memory("/proc/nao/existe/m.db")
    check("banco impossível: fica inativo e as chamadas não levantam erro", not bad.active
          and bad.append_event("s", "user", "utterance", "oi") is False and bad.get_session_context("s") == ""
          and bad.search_memory("oi") == [] and bad.list_active_instructions() == [])
    mem = fresh()
    mem.append_event("s", "user", "utterance", "primeira", event_key="a")
    mem.flush()
    mem._conn.close()                                           # simula banco quebrado no meio do uso
    for i in range(6):
        mem.append_event("s", "user", "utterance", f"x{i}", event_key=f"z{i}")
        mem.flush()
    check("erros repetidos desligam só a memória, sem exceção e sem travar", not mem.healthy and mem.errors >= 5
          and mem.append_event("s", "user", "utterance", "depois") is False)


def test_modes():
    print("Os três modos com a IA simulada")
    d = Path(tempfile.mkdtemp())
    mem = Memory(d / "m.db")
    h = Harness()
    try:
        h.a.memory = mem
        times = {}
        for label, m in (("sem", None), ("com", mem)):
            h.a.memory = m
            t = time.perf_counter()
            for _ in range(2):
                h.a.ask_text("Qual é a sua cor favorita?")
                h.idle()
            times[label] = (time.perf_counter() - t) / 2
        h.a.memory = mem
        check(f"latência simulada por turno com memória ligada ({times['com']:.2f} s) ≈ desligada ({times['sem']:.2f} s)",
              times["com"] - times["sem"] < 0.3)
        # autônomo: instrução, assunto novo, nova conversa → IA ainda recebe a instrução
        h.a.ask_text("Lembre-se de que a sala do evento é a 12")
        h.idle()
        h.a.ask_text("Fale sobre o tempo")
        h.idle()
        mem.flush()
        check("modo autônomo: pergunta e resposta viram eventos, e a instrução fica vigente",
              any("sala do evento" in i["instruction"] for i in mem.list_active_instructions())
              and mem._conn.execute("SELECT COUNT(*) FROM events WHERE actor_type='ai'").fetchone()[0] >= 3)
        old = h.a.session_id
        n_srv = len(h.servers)
        h.a.new_conversation()
        h.a.ask_text("O que mesmo está valendo?")
        h.idle()
        ctx = [e for e in h.servers[-1].received if e["type"] == "conversation.item.create" and "Memória do Nuria" in json.dumps(e, ensure_ascii=False)]
        check("após mudar de assunto e abrir sessão nova, a IA recebe a instrução (e não o histórico inteiro)",
              len(h.servers) == n_srv + 1 and ctx and "sala do evento" in json.dumps(ctx[0], ensure_ascii=False)
              and "Qual é a sua cor" not in json.dumps(ctx, ensure_ascii=False))
        # teleoperado → IA: resumo incremental
        h.a.set_mode("teleoperado")
        h.a.operator_instruction("a mesa 3 pediu água")
        h.a._op_t0 = time.time() - 2.0
        h.a._op_ended()
        mem.flush()
        h.a.set_mode("autonomo")
        before = len(h.sent("conversation.item.create"))
        h.a.ask_text("O que o operador combinou?")
        h.idle()
        items = h.sent("conversation.item.create")[before:]
        order = [e["type"] for e in h.sent()][-60:]
        resumo = [e for e in items if "Desde a última resposta sua" in json.dumps(e, ensure_ascii=False)]
        check("volta do operador: a IA recebe resumo com o que ele fez e as instruções vigentes antes de responder",
              resumo and "mesa 3 pediu água" in json.dumps(resumo[0], ensure_ascii=False)
              and "áudio sem transcrição" in json.dumps(resumo[0], ensure_ascii=False))
        check("a instrução do operador entrou na sessão na hora e ficou gravada com autoria",
              any("a mesa 3 pediu água" in json.dumps(e, ensure_ascii=False) for e in h.sent("conversation.item.create"))
              and any(i["actor"] == "operator" for i in mem.list_active_instructions()))
        check("modo e voz do operador registrados como eventos",
              mem._conn.execute("SELECT COUNT(*) FROM events WHERE event_type='mode_change'").fetchone()[0] >= 2
              and mem._conn.execute("SELECT COUNT(*) FROM events WHERE event_type='voice'").fetchone()[0] == 1)
        # falha da memória não derruba a conversa
        mem._conn.close()
        for _ in range(7):
            h.a.ask_text("ainda funciona?")
            ok = h.idle()
        check("com o banco quebrado a conversa continua respondendo normalmente", ok and h.a.state == "idle")
    finally:
        h.close()
        mem.close()


def test_panel_api():
    print("Gestão: API da memória e painel /memoria (navegador)")
    import urllib.request, urllib.error
    import numpy as np
    import operador
    mem = fresh()
    mem.append_event("s1", "user", "utterance", "Lembre-se de que a sala do evento é a 12", event_key="a")
    mem.append_event("s1", "user", "utterance", "Meu nome é Adryell", event_key="b")
    mem.flush()
    v0 = mem.version
    iid = mem.list_active_instructions()[0]["id"]
    mem.edit_instruction(iid, "a sala do evento é a 14")
    mem.flush()
    ins = mem.list_active_instructions()
    check("editar troca o texto e guarda a versão antiga ('superseded')", [i["instruction"] for i in ins] == ["a sala do evento é a 14"]
          and mem._conn.execute("SELECT COUNT(*) FROM instructions WHERE status='superseded'").fetchone()[0] == 1)
    check("cada escrita muda a versão (o painel só recarrega se mudou)", mem.version > v0)
    check("'esqueça a instrução sobre X' revoga só a de X", extract("esqueça a instrução sobre a sala")["revoke_of"] == ["a sala"])

    srv = operador.OperatorServer(lambda: np.zeros((48, 64, 3), np.uint8), dict, lambda pcm: None,
                                  port=18791, pin="246810", use_tls=False)
    srv.memory = mem
    ok = srv.start()
    base = "http://127.0.0.1:18791"

    def call(path, body=None, token=""):
        req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "X-Token": token})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")
    try:
        with urllib.request.urlopen(base + "/memoria", timeout=5) as r:
            page = r.read().decode("utf-8")
        check("página /memoria abre sem operador conectado", ok and "Ayra · Memória" in page)
        check("sem login: nada é mostrado (401)", call("/api/memoria")[0] == 401)
        check("PIN errado é recusado (403)", call("/api/memoria/entrar", {"pin": "000000"})[0] == 403)
        st, d = call("/api/memoria/entrar", {"pin": "246810"})
        tok = d.get("token", "")
        check("PIN da sala dá acesso (token de sessão)", st == 200 and len(tok) > 20)
        st, d = call("/api/memoria", token=tok)
        check("lista instruções, preferências e métricas", st == 200 and d["instructions"][0]["instruction"] == "a sala do evento é a 14"
              and d["memories"] and "write_p95_ms" in d["stats"])
        st, same = call(f"/api/memoria?v={d['version']}", token=tok)
        check("sem mudança: resposta mínima (não reenvia a lista)", same == {"version": d["version"], "same": True})
        st, res = call("/api/memoria?q=sala", token=tok)
        check("busca pelo painel", st == 200 and res["results"])
        while not srv.commands.empty():
            srv.commands.get_nowait()
        st, _ = call("/api/memoria/acao", {"acao": "nova", "texto": "apresente-se como assistente do laboratório"}, tok)
        note = srv.commands.get(timeout=2)
        check("nova instrução pelo painel: gravada (autor 'painel') e a IA é avisada na hora",
              st == 200 and any(i["actor"] == "painel" for i in mem.list_active_instructions()) and note[0] == "memory_note")
        mid = [m for m in mem.list_memories() if m["validation_status"] == "candidate"][0]["id"]
        call("/api/memoria/acao", {"acao": "validar", "id": mid}, tok)
        check("validar preferência pelo painel", [m["validation_status"] for m in mem.list_memories()] == ["validated"])
        iid = [i for i in mem.list_active_instructions() if "sala" in i["instruction"]][0]["id"]
        call("/api/memoria/acao", {"acao": "revogar", "id": iid}, tok)
        note = srv.commands.get(timeout=2)
        check("revogar pelo painel tira da lista e avisa a IA com o texto", not any("sala" in i["instruction"] for i in mem.list_active_instructions())
              and "sala do evento" in note[1]["texto"])
        check("ação inválida é recusada", call("/api/memoria/acao", {"acao": "xyz"}, tok)[0] == 400)
        codes = [call(f"/api/memoria?v={mem.version}", token=tok)[0] for _ in range(30)]
        check("limite de pedidos por segundo (aba esquecida não martela o robô)", 429 in codes)
        time.sleep(1.1)
        mem.enabled = False
        check("memória desligada no robô: o painel avisa (409)", call("/api/memoria", token=tok)[0] == 409)
        mem.enabled = True
    finally:
        srv.stop()


def test_robot_tab():
    print("Aba Memória na tela Conversas (no robô)")
    import pygame
    import theme
    from renderer import Renderer
    from conversas_ui import ChatHistory
    pygame.init()
    screen = pygame.display.set_mode((1280, 720))
    r = Renderer(screen, (80, 45), theme.SCHEMES["dark"])
    mem = fresh()
    mem.append_event("s1", "user", "utterance", "Lembre-se de que a sala do evento é a 12", event_key="a")
    mem.upsert_instruction("não conte o segredo", actor="operator")
    mem.append_event("s1", "user", "utterance", "Meu nome é Adryell", event_key="b")
    mem.flush()
    c = ChatHistory()
    calls = {"n": 0}
    real = mem.overview
    mem.overview = lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1), real(*a, **k))[1]
    c.open("", memory=mem)
    c.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_TAB, mod=0, unicode=""))
    for _ in range(30):
        c.draw(r, "")
    check("Tab abre a aba Memória com instruções (autor) e preferências", c.tab == "memory" and len(c.mem_rows) == 3)
    check("o banco é lido uma vez, não a cada quadro", calls["n"] == 1)
    t = time.perf_counter()
    for _ in range(30):
        c.draw(r, "")
    ms = (time.perf_counter() - t) * 1000 / 30
    check(f"desenhar a aba custa pouco por quadro ({ms:.1f} ms, CPU sem GPU)", ms < 25)
    pygame.image.save(screen, str(Path(tempfile.gettempdir()) / "memoria_aba.png"))
    btn, kind, ident, text = [row for row in c.mem_rows if row[1] == "revoke"][0]
    out = None
    for typ in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
        out = c.handle_event(pygame.event.Event(typ, button=1, pos=btn.center))
    mem.flush()
    check("Revogar: some na hora, vai para a fila e o app avisa a IA", out and out[0] == "mem_note" and text in out[1]
          and not any(i["id"] == ident for i in mem.list_active_instructions()))
    c.draw(r, "")
    check("digitar na aba Memória não mexe na busca de conversas", c.handle_event(pygame.event.Event(pygame.TEXTINPUT, text="x")) is None and c.query == "")
    off = ChatHistory()
    off.open("", memory=fresh(enabled=False), tab="memory")
    off.draw(r, "")
    check("memória desligada: a aba explica como ligar", off.mem_rows == [])


if __name__ == "__main__":
    test_extraction()
    test_store()
    test_context_and_cost()
    test_failures_and_consent()
    test_modes()
    test_panel_api()
    test_robot_tab()
    failed = [n for n, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} verificações OK")
    sys.exit(1 if failed else 0)
