"""Projeto RAG (Etapas 1–2): conjunto de avaliação, linha de base e contrato dos adaptadores (Realtime/Gemini).
Tudo sintético e local: banco temporário, servidores falsos, sem rede, sem API paga. Uso: python test_avaliacao_rag.py

`SITUACAO_BASELINE` guarda o resultado de HOJE por caso e canal. O teste falha se algo que passava passar a falhar (regressão) e só
AVISA quando algo melhora (atualize a tabela quando a melhora for real).
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import avaliar_baseline_rag as A

RESULTS = []


def check(name, cond):
    RESULTS.append((name, bool(cond)))
    print(f"  [{'OK' if cond else 'FALHOU'}] {name}")


# Situação atual por caso e canal (True = atende o esperado). A linha de base de 2026-11-01 (antes da Etapa 2) tinha lacunas em C12–C15, C19 e na busca;
# depois da Etapa 2 o canal "rag" (contexto.py) atende tudo menos a paráfrase (C08). Os canais antigos (contexto legado, busca crua) mudaram só onde a
# ocultação/escopo/preferência corrigiu o dado; o legado continua sem fatos ditos só numa fala (C03, C04, C07), sem corte por categoria (C20) e com ruído na busca crua (C09–C11).
T, F = True, False
SITUACAO_BASELINE = {
    "C01": dict(rag=T, contexto=T, busca_crua=T, busca_palavras=T), "C02": dict(rag=T, contexto=T, busca_crua=T, busca_palavras=T),
    "C03": dict(rag=T, contexto=F, busca_crua=T, busca_palavras=T), "C04": dict(rag=T, contexto=F, busca_crua=T, busca_palavras=T),
    "C05": dict(rag=T, contexto=T), "C06": dict(rag=T, contexto=T), "C07": dict(rag=T, contexto=F, busca_crua=T, busca_palavras=T),
    "C08": dict(rag=F, busca_crua=F, busca_palavras=F),
    "C09": dict(rag=T, busca_crua=F, busca_palavras=T), "C10": dict(rag=T, busca_crua=F, busca_palavras=T), "C11": dict(rag=T, busca_crua=F, busca_palavras=T),
    "C12": dict(rag=T, contexto=T, busca_palavras=T),
    "C13": dict(rag=T, contexto=T, busca_crua=T, busca_palavras=T), "C14": dict(rag=T, contexto=T, busca_crua=T, busca_palavras=T),
    "C15": dict(rag=T, contexto=T, busca_crua=T, busca_palavras=T),
    "C16": dict(rag=T, contexto=T, busca_crua=T, busca_palavras=T), "C17": dict(rag=T, busca_crua=T, busca_palavras=T),
    "C18": dict(rag=T, contexto=T, busca_crua=T, busca_palavras=T), "C19": dict(rag=T, contexto=T),
    "C20": dict(rag=T, contexto=F, busca_palavras=T),
}


def test_dataset():
    print("Conjunto de avaliação")
    d = A.carregar()
    ids = [c["id"] for c in d["casos"]]
    check("rotulado como sintético", "SINTÉTICO" in d["rotulo"] and "nenhum dado real" in d["rotulo"])
    check("ids únicos e todas as categorias pedidas presentes",
          len(ids) == len(set(ids)) and {c["categoria"] for c in d["casos"]} >= {
              "conhecida", "ausente", "preferencia_alterada", "revogacao_exclusao", "isolamento", "interrupcao"})
    check("cada caso tem usuário válido, canais conhecidos e um critério (contém / não contém / vazio / regressão)",
          all(c["usuario"] in d["usuarios"] and set(c["canais"]) <= {"contexto", "busca_crua", "busca_palavras", "regressao"}
              and (c.get("esperado_contem") or c.get("esperado_nao_contem") or c.get("esperado_vazio") or c.get("regressoes")) for c in d["casos"]))
    texto = json.dumps(d, ensure_ascii=False)
    check("sem cara de dado pessoal real (e-mail, telefone, CPF, chave)",
          not re.search(r"[\w.]+@[\w.]+\.\w+|\b\d{3}\.\d{3}\.\d{3}-\d{2}\b|\b\d{4,5}-\d{4}\b|sk-[A-Za-z0-9]{10,}|AIza[0-9A-Za-z_-]{10}", texto))
    bat = (HERE / "rodar_testes.bat").read_text(encoding="utf-8")
    regs = sorted({a for c in d["casos"] for a in c.get("regressoes", [])})
    check(f"regressões de interrupção existem e estão no rodar_testes.bat ({', '.join(regs)})",
          all((HERE / a).exists() and a in bat for a in regs))


def test_baseline():
    print("Linha de base (banco temporário)")
    d = A.carregar()
    res = A.executar(d, (), reps=10)
    check("sem API paga, sem rede, banco temporário", res["api_paga"] is False and res["rede"] is False and res["banco_temporario"] is True)
    real = HERE / "logs" / "memoria.db"
    check("não tocou no banco real do usuário (usou um banco temporário)", Path(res["banco"]).resolve() != real.resolve() and "rag_baseline_" in res["banco"])
    regress, melhoras = [], []
    for cid, canais in res["casos"].items():
        for canal, v in canais.items():
            antes = SITUACAO_BASELINE[cid][canal]
            if antes and not v["ok"]:
                regress.append((cid, canal))
            if not antes and v["ok"]:
                melhoras.append((cid, canal))
    check(f"nenhum canal que atendia o esperado deixou de atender ({regress or 'ok'})", not regress)
    if melhoras:
        print(f"    AVISO: melhoraram (atualize SITUACAO_BASELINE): {melhoras}")
    cats = res["por_categoria"]
    check("isolamento por usuário vale em todos os canais, inclusive a validação (C16–C19 no RAG e no legado)",
          all(res["casos"][c]["rag"]["ok"] for c in ("C16", "C17", "C18", "C19")) and res["casos"]["C19"]["contexto"]["ok"])
    check("Etapa 2: revogação/exclusão some do contexto, da busca antiga e do RAG (C13–C15)",
          all(res["casos"][c][k]["ok"] for c in ("C13", "C14", "C15") for k in ("contexto", "busca_crua", "busca_palavras", "rag")))
    check("Etapa 2: perguntas sem resposta (C09–C11) não devolvem nada no RAG (a busca crua antiga ainda traz ruído)",
          all(res["casos"][c]["rag"]["ok"] and res["casos"][c]["rag"]["resultados_devolvidos"] == 0 for c in ("C09", "C10", "C11"))
          and res["casos"]["C11"]["busca_crua"].get("resultados_devolvidos", 0) > 0)
    check("Etapa 2: preferência alterada fica só com a nova (C12) e o validado não é cortado (C20) no RAG; o contexto legado ainda corta (C20)",
          res["casos"]["C12"]["contexto"]["ok"] and res["casos"]["C20"]["rag"]["ok"] and not res["casos"]["C20"]["contexto"]["ok"]
          and res["contexto"]["sint_cheio"]["truncado"])
    check("fatos ditos só numa fala (C03, C04, C07) chegam pelo RAG; o contexto legado continua sem eles", all(res["casos"][c]["rag"]["ok"] and not res["casos"][c]["contexto"]["ok"] for c in ("C03", "C04", "C07")))
    check("limite conhecido: paráfrase sem palavra em comum (C08) não é achada, sem inventar", not res["casos"]["C08"]["rag"]["ok"] and not res["casos"]["C08"]["rag"]["fontes"])
    ctx = res["contexto"]
    check("medidas presentes: caracteres, tokens estimados, nº de consultas SQL e tempos por usuário",
          all({"chars", "tokens_estimados", "consultas_sql", "consultas_sql_handoff", "tempo"} <= set(v) for v in ctx.values())
          and all(v["consultas_sql"] >= 1 for v in ctx.values()))
    check("medidas de busca com e sem FTS", {"com_fts", "sem_fts_like"} <= set(res["busca"]))
    check("hoje o contexto cabe no limite e é barato (conferência, não meta)", all(v["chars"] <= v["limite_chars"] for v in ctx.values()))


def test_escala_pequena():
    print("Escala (pequena, só para checar o mecanismo)")
    d = A.carregar()
    res = A.executar(d, (300,), reps=10)
    e = res["escalas"]["300"]
    check("ruído sintético acrescentado e medido", e["banco_bytes"] > res["banco_bytes"] and e["busca"]["busca_crua"]["consultas"] > 0)
    check("o conjunto continua achando a maioria depois do ruído", e["canais_ok_depois"] >= e["canais_ok_antes"] - 2)


# ---------------------------------------------------------------------------------------------------------
# contrato dos adaptadores
# ---------------------------------------------------------------------------------------------------------
LACUNAS_CONHECIDAS = set()      # a lacuna de entrega do Gemini foi corrigida na Etapa 2 (thread leitora)


class Coletor:
    """Lê um transporte numa thread e guarda (tempo, evento); termina quando o transporte fecha."""
    def __init__(self, transport):
        self.t, self.events, self.error = transport, [], None
        self.t0 = time.perf_counter()
        self.th = threading.Thread(target=self._run, daemon=True)
        self.th.start()

    def _run(self):
        import realtime as rt
        while True:
            try:
                raw = self.t.recv()
            except rt.TransportClosed as e:
                self.error = e
                return
            except Exception as e:
                self.error = e
                return
            self.events.append((time.perf_counter() - self.t0, json.loads(raw)))

    def kinds(self):
        return [e["type"] for _, e in self.events]

    def done_for(self, rid):
        return [e for _, e in self.events if e["type"] == "response.done" and (e.get("response") or {}).get("id") == rid]

    def wait(self, cond, timeout=3.0):
        end = time.perf_counter() + timeout
        while time.perf_counter() < end:
            if cond():
                return True
            time.sleep(0.01)
        return False


def make_openai():
    import realtime as rt
    return rt.FakeRealtimeServer(latency=0.02, speed=8.0)


def make_gemini():
    import gemini_live as G
    return G.GeminiAdapter(G.FakeGeminiServer(latency=0.02, speed=8.0), "gemini-3.8-live", "Kore", 350)


def send(t, ev):
    t.send(json.dumps(ev))


def ask(t, text="olá"):
    send(t, {"type": "session.update", "session": {"type": "realtime"}})
    send(t, {"type": "conversation.item.create", "item": {"type": "message", "role": "user",
                                                          "content": [{"type": "input_text", "text": text}]}})
    send(t, {"type": "response.create"})


def contrato(nome, make):
    print(f"Contrato do adaptador: {nome}")
    # 1) conclusão: um created e exatamente um done(completed) com o mesmo id, áudio só do mesmo id
    t = make()
    c = Coletor(t)
    ask(t)
    ok = c.wait(lambda: any(k == "response.done" for k in c.kinds()), 5.0)
    created = [e["response"]["id"] for _, e in c.events if e["type"] == "response.created"]
    done = [e for _, e in c.events if e["type"] == "response.done"]
    check(f"{nome}: uma resposta = um created e um done(completed) com o mesmo id",
          ok and len(created) == 1 and len(done) == 1 and done[0]["response"]["id"] == created[0] and done[0]["response"]["status"] == "completed")
    audio_ids = {e.get("response_id") for _, e in c.events if e["type"] == "response.output_audio.delta"}
    check(f"{nome}: todo áudio traz o id da resposta", audio_ids <= {created[0]} and len(audio_ids) >= 1)
    t.close()
    # 2) cancelamento: exatamente um done(cancelled), nada completo nem áudio depois dele
    t = make()
    c = Coletor(t)
    ask(t, "conte uma história longa")
    c.wait(lambda: "response.output_audio.delta" in c.kinds(), 5.0)
    send(t, {"type": "response.cancel"})
    c.wait(lambda: any(e["type"] == "response.done" for _, e in c.events), 3.0)
    time.sleep(0.6)
    dones = [(ts, e) for ts, e in c.events if e["type"] == "response.done"]
    status = [e["response"]["status"] for _, e in dones]
    t_done = dones[0][0] if dones else 1e9
    late_audio = [e for ts, e in c.events if e["type"] == "response.output_audio.delta" and ts > t_done + 0.05]
    check(f"{nome}: cancelar gera um único done(cancelled), sem done(completed) depois", status == ["cancelled"])
    check(f"{nome}: nenhum áudio da resposta cancelada depois do done", not late_audio)
    t.close()
    # 3) falha: fechar o transporte faz recv levantar TransportClosed (nunca silêncio eterno)
    t = make()
    c = Coletor(t)
    ask(t)
    time.sleep(0.15)
    t.close()
    c.th.join(2.0)
    import realtime as rt
    check(f"{nome}: fechar o transporte termina a leitura com TransportClosed", not c.th.is_alive() and isinstance(c.error, rt.TransportClosed))
    try:
        t.send("{}")
        check(f"{nome}: enviar depois de fechar levanta TransportClosed", False)
    except rt.TransportClosed:
        check(f"{nome}: enviar depois de fechar levanta TransportClosed", True)
    # 4) entrega: evento gerado por um envio chega sem depender de outra mensagem do servidor
    t = make()
    c = Coletor(t)
    send(t, {"type": "session.update", "session": {"type": "realtime"}})
    time.sleep(0.2)
    n0 = len(c.events)
    send(t, {"type": "response.create"})                          # nada novo a dizer em ambos? (Gemini: sem texto/áudio pendente)
    entregue = c.wait(lambda: len(c.events) > n0, 0.6)
    chave = f"{nome.split()[0].lower()}:entrega_durante_send"
    if chave in LACUNAS_CONHECIDAS:
        if entregue:
            check(f"{nome}: lacuna de entrega CORRIGIDA — remova '{chave}' de LACUNAS_CONHECIDAS e do doc", True)
        else:
            check(f"{nome}: lacuna conhecida confirmada (evento de um send só chega com a próxima mensagem do servidor)", True)
    else:
        check(f"{nome}: evento gerado por um envio é entregue sem outra mensagem do servidor", entregue)
    t.close()


def test_contratos():
    contrato("OpenAI Realtime (simulado)", make_openai)
    contrato("Gemini Live (simulado)", make_gemini)


if __name__ == "__main__":
    test_dataset()
    test_baseline()
    test_escala_pequena()
    test_contratos()
    ok = sum(1 for _, v in RESULTS if v)
    print(f"\n{ok}/{len(RESULTS)} verificações OK (sintético e local; sem rede nem API paga)")
    sys.exit(0 if ok == len(RESULTS) else 1)
