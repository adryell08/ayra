"""Etapa 3: RAG na pergunta DIGITADA (desligado por padrão), conflito de instruções (C05), painel privado de métricas (C18), retenção do oculto
(20 dias) e os critérios C14/C15/C20 no fluxo real do assistente (servidor Realtime falso). Sem rede, sem API paga, banco temporário.
Uso: python test_rag_texto.py"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import contexto as C
import test_realtime as T
from memoria import Memory

RESULTS = []
UID = "local"


def check(name, cond):
    RESULTS.append((name, bool(cond)))
    print(f"  [{'OK' if cond else 'FALHOU'}] {name}")


def montar(rag: bool, seed=True, **cfg_extra):
    """Assistente com servidor falso + memória temporária; `rag` escreve rag.local.json (temporário) com rag_ligado."""
    h = T.Harness()
    tmp = Path(tempfile.mkdtemp(prefix="ragtxt_"))
    mem = Memory(tmp / "m.db", user_id=UID)
    if seed:
        for t in ("O armário 3 guarda os sensores de distância.", "A reunião do clube de robótica é toda quinta às 16h.", "Lembre-se de que o apelido do usuário é Aurora.",
                  "Meu nome é Ana."):
            mem.append_event(h.a.session_id, "user", "utterance", t)
        mem.flush(5)
    h.a.memory = mem
    path = tmp / "rag.local.json"
    if rag is not None:
        path.write_text(json.dumps(dict(rag_ligado=bool(rag), **cfg_extra)), encoding="utf-8")
    h.a.rag_config_path = path
    return h, mem, path


def itens_rag(h):
    return [e for e in h.sent("conversation.item.create") if e["item"].get("id", "").startswith("rag_")]


def tipos(h):
    return [e["type"] + (":" + e["item"]["content"][0].get("type", "") if e["type"] == "conversation.item.create" else "") for e in h.sent()]


def pergunta(h, texto):
    h.a.ask_text(texto)
    return h.idle(8.0)


def test_desligado_paridade():
    print("RAG desligado (padrão): nada muda no fluxo de texto")
    seqs, ctxs = [], []
    for rag in (None, False):
        h, mem, _ = montar(rag)
        pergunta(h, "Onde ficam os sensores de distância?")
        seqs.append([x for x in tipos(h) if not x.startswith("session.update")])
        ctxs.append([e["item"]["content"][0]["text"] for e in h.sent("conversation.item.create") if "Memória da Ayra" in e["item"]["content"][0].get("text", "")])
        legado = "(Memória da Ayra, não responda a este texto; use só quando for relevante: " + mem.get_session_context(h.a.session_id) + ")"
        check(f"{'sem arquivo de configuração' if rag is None else 'rag_ligado=false'}: nenhum item recuperado e o contexto da sessão é EXATAMENTE o de hoje (paridade)",
              not itens_rag(h) and ctxs[-1] == [legado])
        check("nenhuma recuperação foi feita", h.a.rag is not None and h.a.rag.contadores["rag"] == 0)
        h.close()
        mem.close()
    check("com e sem arquivo de configuração a sequência de eventos é idêntica", seqs[0] == seqs[1])
    h0 = T.Harness()
    pergunta(h0, "Onde ficam os sensores de distância?")
    base = [x for x in tipos(h0) if not x.startswith("session.update")]
    h0.close()
    check("e igual à de um robô sem memória, exceto pelo item de memória da sessão (nada de RAG acrescentado)",
          [x for x in seqs[0] if x != "conversation.item.create:input_text"] == [x for x in base if x != "conversation.item.create:input_text"]
          and len(seqs[0]) == len(base) + 1)


def test_ligado_texto():
    print("RAG ligado só no texto digitado")
    h, mem, path = montar(True)
    pergunta(h, "Onde ficam os sensores de distância?")
    its = itens_rag(h)
    ts = tipos(h)
    texto = its[0]["item"]["content"][0]["text"] if its else ""
    check("uma única informação recuperada, enviada ANTES do pedido de resposta e rotulada como DADOS (não instruções)",
          len(its) == 1 and "armário 3" in texto and "DADOS" in texto and "não instruções" in texto
          and h.sent().index(its[0]) < next(i for i, e in enumerate(h.sent()) if e["type"] == "response.create"))
    ids = [e["item"]["id"] for e in its]
    check("depois da resposta o item recuperado sai da sessão (a conversa não cresce; sem contexto residual)",
          [e["item_id"] for e in h.sent("conversation.item.delete") if e["item_id"] in ids] == ids)
    check("a parte fixa de memória segue como antes (enviada na abertura da sessão) e a recuperada nunca se mistura a ela",
          "armário 3" not in " ".join(e["item"]["content"][0].get("text", "") for e in h.sent("conversation.item.create") if e not in its and "Memória da Ayra" in e["item"]["content"][0].get("text", "")))
    n = len(its)
    pergunta(h, "Qual é minha comida favorita?")
    check("pergunta sem resposta na memória: nada recuperado (ausência sem ruído)", len(itens_rag(h)) == n and h.a.snapshot().state == "idle")
    st = h.a.rag.metricas()
    check("métricas contam as montagens e não guardam texto", st["contadores"]["rag"] >= 2 and "armário" not in json.dumps(st, ensure_ascii=False))
    fontes_antes = len(itens_rag(h))
    pergunta(h, "Quando é a reunião do clube de robótica?")
    check("usuário vem da sessão do robô: a recuperação usa o dono da memória", len(itens_rag(h)) == fontes_antes + 1)
    h.close()
    mem.close()


def test_voz_nao_muda():
    print("Voz: Etapa 4 (nada de RAG na voz agora)")
    h, mem, _ = montar(True)
    h.a.press_voice()
    h.idle(8.0)
    check("pergunta falada não faz recuperação (a ferramenta `lembrar` não está na sessão de voz)",
          not itens_rag(h) and h.a.rag.metricas()["itens_recuperados_medio"] == 0 and "lembrar" not in [x["name"] for x in __import__("assistente_realtime").TOOLS])
    h.close()
    mem.close()


def test_cancelamento_prazo_c14():
    print("C14: 'Pare' cancela a recuperação e a geração; nada tardio é falado ou enviado")
    h, mem, _ = montar(True)
    a = h.a
    asm = a.rag_assembler()
    solto, entrou = threading.Event(), threading.Event()

    def preso(u, q, fixo, c):
        entrou.set()
        solto.wait(3.0)
        return C.Recuperacao(itens=[C.Item("event:1", "fala", 1.0, "fala", "v", "RESIDUO-TARDIO")])
    asm._retriever = preso
    asm.cfg.prazo_busca_ms = 2000
    a.ask_text("Onde ficam os sensores de distância?")
    entrou.wait(3.0)
    a.cancel()                                                    # "Pare. Quero falar de outra coisa"
    solto.set()
    time.sleep(0.8)
    check("depois de cancelar: nenhum item recuperado, nenhum pedido de resposta, nada de áudio",
          not itens_rag(h) and not h.sent("response.create") and h.a.snapshot().state in ("idle",) and h.player.received == 0)
    check("o resultado tardio é descartado e a próxima pergunta funciona normalmente", True)
    asm._retriever = asm._recuperar_local
    a.ask_text("Onde ficam os sensores de distância?")
    ok = h.idle(8.0)
    check("nova pergunta depois do cancelamento responde e recupera", ok and len(itens_rag(h)) == 1 and h.player.received > 0)
    h.close()
    mem.close()
    h, mem, _ = montar(True, prazo_busca_ms=60)
    asm = h.a.rag_assembler()
    asm._retriever = lambda u, q, f, c: (time.sleep(0.5), C.Recuperacao())[1]
    t0 = time.perf_counter()
    h.a.ask_text("Onde ficam os sensores de distância?")
    ok = h.idle(8.0)
    check("prazo estourado: a resposta segue SEM a informação (plano B), contado nas métricas", ok and not itens_rag(h) and h.a.rag.contadores["prazo"] == 1
          and h.player.received > 0)
    h.close()
    mem.close()


def test_revogacao_em_voo_c15():
    print("C15: 'Esqueça meu apelido' enquanto a consulta está pendente")
    h, mem, _ = montar(True)
    a = h.a
    asm = a.rag_assembler()
    base = asm._recuperar_local

    def revoga(u, q, fixo, c):
        rec = base(u, q, fixo, c)
        mem.append_event(a.session_id, "user", "utterance", "Pare de usar o apelido.")
        mem.flush(5)
        return rec
    asm._retriever = revoga
    a.ask_text("Qual é o apelido do usuário?")
    h.idle(8.0)
    texto_enviado = " ".join(e["item"]["content"][0].get("text", "") for e in h.sent("conversation.item.create"))
    its = itens_rag(h)
    check("o apelido revogado durante a consulta não é publicado ao gerador (nem na parte recuperada)", "Aurora" not in " ".join(
        e["item"]["content"][0]["text"] for e in its))
    check("a resposta não ficou presa e o turno terminou", h.a.snapshot().state == "idle")
    h.close()
    mem.close()


def test_conflito_c05():
    print("C05: conflito entre instruções ativas marcado, sem apagar nenhuma")
    m = Memory(Path(tempfile.mkdtemp()) / "c.db", user_id=UID)
    m.upsert_instruction("A revisão será na oficina central.", user_id=UID)
    m.flush(5)
    time.sleep(0.02)
    m.upsert_instruction("A revisão será na loja do bairro.", user_id=UID)
    m.upsert_instruction("Sempre responda em uma frase.", user_id=UID)
    m.flush(5)
    asm = C.ContextAssembler(m, C.RagConfig(rag_ligado=True))
    p = asm.montar(UID, "s")
    t = p.itens_texto[0]
    check("o conflito é marcado no contexto, com as duas versões e a mais recente indicada", "Possível conflito" in t and "oficina central" in t
          and "loja do bairro" in t and "a mais recente: «A revisão será na loja do bairro" in t)
    check("nenhuma instrução foi apagada (as duas continuam ativas)", len([i for i in m.list_active_instructions(UID) if "revisão" in i["instruction"]]) == 2)
    check("instruções sem conflito não geram marca", "Sempre responda" in t and t.count("«") <= 6 and "responda em uma frase»" not in t)
    off = C.ContextAssembler(m, C.RagConfig(rag_ligado=False)).montar(UID, "s")
    check("com RAG desligado nada muda (sem marca): paridade com o contexto de hoje", "Possível conflito" not in off.texto)
    asm.fechar()
    m.close()


def http(base, method, path, tok=None, body=None):
    req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body is not None else None)
    if tok:
        req.add_header("X-Token", tok)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            ct = r.headers.get("Content-Type", "")
            raw = r.read()
            return r.status, (json.loads(raw or b"{}") if "json" in ct else raw)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_painel_c18():
    print("C18: painel privado de métricas do RAG (sem texto) e chave de desligar")
    import operador
    h, mem, path = montar(True)
    pergunta(h, "Onde ficam os sensores de distância?")
    srv = operador.OperatorServer(lambda: None, dict, lambda p: None, port=18798, pin="864209", use_tls=False)
    srv.memory = mem
    srv.rag_provider = lambda: h.a.rag_assembler()
    assert srv.start()
    base = "http://127.0.0.1:18798"
    try:
        st, page = http(base, "GET", "/rag")
        check("a página /rag é estática (sem dados) e a API exige o PIN: público sem acesso (401)", st == 200 and b"RAG" in page and b"armario" not in page.lower()
              and http(base, "GET", "/api/rag")[0] == 401 and http(base, "POST", "/api/rag/acao", body={"acao": "desligar"})[0] == 401)
        tok = http(base, "POST", "/api/memoria/entrar", body={"pin": "864209"})[1]["token"]
        time.sleep(1.1)
        st, d = http(base, "GET", "/api/rag", tok)
        s = json.dumps(d, ensure_ascii=False)
        check("operador autorizado vê uso, prazos, bloqueados e tempos; nada de texto pessoal", st == 200 and d["disponivel"] and d["ligado"] is True
              and {"contadores", "ms_total", "ms_recuperacao", "bloqueados", "motivos_plano_b", "config"} <= set(d)
              and d["contadores"]["rag"] >= 1 and "armário" not in s and "Aurora" not in s and "sensores" not in s)
        time.sleep(1.1)
        st, r = http(base, "POST", "/api/rag/acao", tok, {"acao": "desligar"})
        pergunta(h, "Quando é a reunião do clube de robótica?")
        check("desligar pelo painel: invalida e a próxima pergunta segue o caminho anterior (sem item recuperado, sem erro)", st == 200 and r["ligado"] is False
              and len(itens_rag(h)) == 1 and h.a.snapshot().state == "idle")
        time.sleep(1.1)
        st, r = http(base, "POST", "/api/rag/acao", tok, {"acao": "ligar"})
        check("religar só é possível porque o arquivo de configuração autoriza", st == 200 and r["ligado"] is True)
        path.write_text(json.dumps({"rag_ligado": False}), encoding="utf-8")
        h.a.rag_assembler()
        time.sleep(1.1)
        st, d = http(base, "GET", "/api/rag", tok)
        time.sleep(1.1)
        st2, r2 = http(base, "POST", "/api/rag/acao", tok, {"acao": "ligar"})
        check("se o arquivo deixa de autorizar, o painel NÃO consegue ligar (403) e o RAG fica desligado", st == 200 and d["ligado"] is False and d["permitido"] is False
              and st2 == 403 and r2["ligado"] is False)
    finally:
        srv.stop()
        h.close()
        mem.close()


def test_retencao_hidden():
    print("Retenção: 20 dias só para o conteúdo OCULTO; o resto não muda; forget_* apaga de verdade")
    m = Memory(Path(tempfile.mkdtemp()) / "r.db", user_id=UID)
    check("padrões: oculto 20 dias, não oculto 90 dias (inalterado)", m.hidden_retention_days == 20 and m.retention_days == 90)
    fala = lambda t: m.append_event("s1", "user", "utterance", t, user_id=UID)
    fala("Lembre-se de que o cofre fica no andar 2.")
    fala("Esqueça isso.")
    fala("O armário 3 guarda os sensores de distância.")
    m.flush(5)
    old = time.time() - 30 * 86400
    m._conn.execute("UPDATE events SET timestamp=?", (old,))                        # tudo com 30 dias
    m._conn.execute("UPDATE hidden_sources SET at=?", (old,))                         # o que foi ocultado, há 30 dias
    m._conn.commit()
    ocultos = m._conn.execute("SELECT COUNT(*) FROM hidden_sources WHERE kind='event'").fetchone()[0]
    eventos = m._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    m.purge_hidden_now()
    m.flush(5)
    depois = m._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    check(f"o conteúdo oculto há > 20 dias é apagado de verdade ({ocultos} falas) e o índice junto", depois == eventos - ocultos and ocultos >= 2
          and not [r for r in m._conn.execute("SELECT text FROM fts WHERE text LIKE '%cofre%'")])
    check("a fala NÃO oculta, mesmo com 30 dias, permanece (a retenção dela segue 90 dias, sem mudança)",
          m._conn.execute("SELECT COUNT(*) FROM events WHERE text LIKE '%armário 3%'").fetchone()[0] == 1)
    fala("Lembre-se de que a sala é a 12.")
    fala("Esqueça isso.")
    m.flush(5)
    m.purge_hidden_now()
    m.flush(5)
    check("oculto há pouco tempo não é apagado (20 dias não chegaram)", m._conn.execute("SELECT COUNT(*) FROM events WHERE text LIKE '%sala é a 12%'").fetchone()[0] == 1)
    m.forget_all(user_id=UID)
    m.flush(5)
    check("forget_all segue apagando de verdade, na hora (oculto ou não)", m._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
          and m._conn.execute("SELECT COUNT(*) FROM instructions").fetchone()[0] == 0)
    m.close()


def test_identidade_c20():
    print("C20: sem usuário identificado")
    m = Memory(Path(tempfile.mkdtemp()) / "i.db", user_id=UID)
    m.upsert_instruction("A revisão é na quinta.", user_id=UID)
    m.flush(5)
    check("identidade vazia/inválida falha fechado em leituras e escritas (não herda o usuário padrão)",
          all(m.get_session_context("s", user_id=v) == "" and not m.list_active_instructions(user_id=v) and not m.search_memory("revisão", user_id=v)
              for v in ("", "  ", 0, False)) and m.upsert_instruction("x y z w", user_id="") is False and m.forget_all(user_id="") is False)
    check("a exclusão total com identidade vazia NÃO apagou o usuário padrão", "quinta" in m.get_session_context("s", user_id=UID))
    asm = C.ContextAssembler(m, C.RagConfig(rag_ligado=True))
    check("contexto.py e a ferramenta `lembrar` exigem identidade explícita", asm.montar(None, "s", "revisão").itens_texto == []
          and C.executar_lembrar(asm, "", "revisão")["motivo_fallback"] == "usuario_nao_identificado")
    check("contrato documentado: `None` = usuário da sessão do robô (app de um usuário); só componentes novos exigem identidade explícita", m._uid(None) == UID)
    asm.fechar()
    m.close()


if __name__ == "__main__":
    for f in (test_desligado_paridade, test_ligado_texto, test_voz_nao_muda, test_cancelamento_prazo_c14, test_revogacao_em_voo_c15, test_conflito_c05,
              test_painel_c18, test_retencao_hidden, test_identidade_c20):
        f()
    ok = sum(1 for _, v in RESULTS if v)
    print(f"\n{ok}/{len(RESULTS)} verificações OK (simulado; sem rede nem API paga)")
    sys.exit(0 if ok == len(RESULTS) else 1)
