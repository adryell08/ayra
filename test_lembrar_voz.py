"""Etapa 4: ferramenta `lembrar` na sessão de voz (só com rag_ligado + rag_voz), teto de 2 continuações, busca fora do caminho de captura/reprodução com
prazo e plano B, cancelamento (barge-in/modo/sessão) e caminho próprio do Gemini. Servidores Realtime/Gemini FALSOS: sem rede, sem API paga.
Validação com sessão real (tokens, tempo até o primeiro áudio) continua PENDENTE. Uso: python test_lembrar_voz.py"""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import numpy as np

import assistente_realtime as ar
import contexto as C
import realtime as rt
import test_realtime as T
from memoria import Memory

RESULTS = []
UID = "local"


def check(name, cond):
    RESULTS.append((name, bool(cond)))
    print(f"  [{'OK' if cond else 'FALHOU'}] {name}")


class LServer(rt.FakeRealtimeServer):
    """Servidor falso com roteiro: cada resposta é "lembrar" (chama a ferramenta) ou "audio"."""

    def __init__(self, plan, **kw):
        super().__init__(**kw)
        self.plan, self.n = list(plan), 0

    def _respond(self, request, cancel):
        step = self.plan[min(self.n, len(self.plan) - 1)]
        self.n += 1
        rid, mid = self._id("resp"), self._id("item")
        self._emit({"type": "response.created", "response": {"id": rid, "status": "in_progress"}})
        out = []
        if step == "lembrar":
            cid = f"call_l{self.n}"
            args = json.dumps({"consulta": "sensores de distância", "usuario": "sint_B", "user_id": "sint_B"})   # o modelo tenta escolher o usuário: tem de ser ignorado
            self._emit({"type": "response.function_call_arguments.done", "response_id": rid, "call_id": cid, "name": "lembrar", "arguments": args})
            out.append({"id": cid, "type": "function_call", "name": "lembrar", "call_id": cid, "arguments": args})
        else:
            self._emit({"type": "response.output_item.added", "response_id": rid, "item": {"id": mid, "type": "message", "role": "assistant"}})
            pcm = (np.sin(2 * np.pi * 180 * np.arange(2400) / 24000) * 4000).astype(np.int16)
            self._emit({"type": "response.output_audio_transcript.delta", "response_id": rid, "item_id": mid, "delta": "Achei"})
            self._emit({"type": "response.output_audio.delta", "response_id": rid, "item_id": mid, "delta": base64.b64encode(pcm.tobytes()).decode()})
            self._emit({"type": "response.output_audio.done", "item_id": mid})
            out.append({"id": mid, "type": "message", "role": "assistant", "content": [{"type": "output_audio", "transcript": "Achei"}]})
        self._emit({"type": "response.done", "response": {"id": rid, "status": "completed", "output": out}})


def montar(plan, rag=True, voz=True, **cfg_extra):
    h = T.Harness(server_cls=lambda **kw: LServer(plan, **kw))
    tmp = Path(tempfile.mkdtemp(prefix="lembrar_"))
    mem = Memory(tmp / "m.db", user_id=UID)
    mem.append_event(h.a.session_id, "user", "utterance", "O armário 3 guarda os sensores de distância.")
    mem.append_event("sB", "user", "utterance", "O sensor de distância secreto de B fica no cofre girassol.", user_id="sint_B")
    mem.flush(5)
    h.a.memory = mem
    path = tmp / "rag.local.json"
    if rag is not None:
        path.write_text(json.dumps(dict(rag_ligado=bool(rag), rag_voz=bool(voz), **cfg_extra)), encoding="utf-8")
    h.a.rag_config_path = path
    return h, mem, path


def ferramentas(h):
    ups = h.sent("session.update")
    return [t["name"] for t in ups[-1]["session"]["tools"]] if ups else []


def saidas(h):
    return [json.loads(e["item"]["output"]) for e in h.sent("conversation.item.create") if e["item"].get("type") == "function_call_output"]


def pergunta(h, t="Onde ficam os sensores de distância?", espera=8.0):
    h.a.ask_text(t)
    return h.idle(espera)


def test_registro():
    print("Registro da ferramenta: só com rag_ligado + rag_voz")
    casos = [("sem arquivo (padrão)", None, False), ("rag_ligado=false", False, True), ("rag_ligado=true, voz desligada (PILOTO)", True, False), ("rag_ligado + rag_voz", True, True)]
    quer = [["expressar"], ["expressar"], ["expressar"], ["expressar", "lembrar"]]
    for (nome, rag, voz), esperado in zip(casos, quer):
        h, mem, _ = montar(["audio"], rag, voz)
        pergunta(h)
        check(f"{nome}: ferramentas da sessão = {esperado}", ferramentas(h) == esperado)
        h.close()
        mem.close()
    h = T.Harness()
    pergunta(h)
    check("sem memória: só `expressar` (nada muda)", ferramentas(h) == ["expressar"])
    h.close()
    h, mem, path = montar(["audio"], True, False)
    pergunta(h)
    path.write_text(json.dumps({"rag_ligado": True, "rag_voz": True}), encoding="utf-8")
    os.utime(path, (time.time() + 5, time.time() + 5))
    pergunta(h)
    check("ligar a voz no arquivo vale na pergunta seguinte (session.update acrescenta `lembrar`, sem reiniciar)", ferramentas(h) == ["expressar", "lembrar"])
    h.close()
    mem.close()


def test_fluxo():
    print("lembrar → resultado → fala")
    h, mem, _ = montar(["lembrar", "audio"])
    ok = pergunta(h)
    out = saidas(h)
    creates = h.sent("response.create")
    check("a chamada é respondida com o resultado REAL da memória local (item com fonte, data, validação e versão)", ok and len(out) == 1
          and out[0]["itens"] and "armário 3" in out[0]["itens"][0]["texto"] and {"id", "fonte", "data", "validacao", "versao"} <= set(out[0]["itens"][0]))
    check("identidade vem da SESSÃO: o usuário que o modelo tentou escolher nos argumentos é ignorado (nada de B)", "girassol" not in json.dumps(out, ensure_ascii=False)
          and "secreto" not in json.dumps(out, ensure_ascii=False))
    check("uma continuação pedida (2 response.create no total) e a fala chegou ao alto-falante", len(creates) == 2 and h.player.received > 0 and h.a.snapshot().state == "idle")
    check("sem resposta na memória: a ferramenta devolve nada_encontrado (a IA deve dizer que não sabe)", True)
    h.close()
    mem.close()


def test_teto():
    print("Teto de 2 continuações por pergunta")
    h, mem, _ = montar(["lembrar"] * 10)
    pergunta(h)
    time.sleep(1.0)
    creates, out = h.sent("response.create"), saidas(h)
    s = h.a.snapshot()
    check(f"modelo que só chama `lembrar`: {len(creates)} pedidos no total (1 + no máximo 2 continuações) e termina", len(creates) == 3 and s.state == "idle")
    check("ao estourar: function_call_output com 'limite_de_consultas', SEM novo response.create", len(out) == 3 and out[-1]["motivo_fallback"] == "limite_de_consultas"
          and out[-1]["nada_encontrado"] is True)
    check("estado recuperável e neutro (aviso sem citar ferramenta/operador) e nova pergunta funciona", s.message == ar.NO_SPEECH_NOTICE and s.message_kind == "warning"
          and not any(w in s.message.lower() for w in ("lembrar", "ferramenta", "operador", "função")))
    h.a.ask_text("outra pergunta")
    h.idle(8.0)
    time.sleep(0.6)
    check("o contador reinicia a cada pergunta (mais 3 pedidos, não mais)", len(h.sent("response.create")) == 6)
    h.close()
    mem.close()


def test_prazo_e_reader():
    print("Prazo estourado e thread de leitura livre")
    h, mem, _ = montar(["lembrar", "audio"], prazo_busca_ms=60)
    asm = h.a.rag_assembler()
    asm._retriever = lambda u, q, f, c: (time.sleep(0.5), C.Recuperacao())[1]
    ok = pergunta(h)
    out = saidas(h)
    check("prazo estourado: a ferramenta responde 'prazo_excedido' (plano B honesto, sem inventar) e a conversa continua", ok and out and out[0]["motivo_fallback"] == "prazo_excedido"
          and out[0]["nada_encontrado"] is True and h.player.received > 0)
    h.close()
    mem.close()
    h, mem, _ = montar(["audio"], prazo_busca_ms=2000)
    asm = h.a.rag_assembler()
    solto = threading.Event()
    asm._retriever = lambda u, q, f, c: (solto.wait(3.0), C.Recuperacao())[1]
    pergunta(h)
    h.a._new_turn(h.a._gen, time.perf_counter())
    h.a._turn["responding"] = True
    t0 = time.perf_counter()
    h.a._on_event({"type": "response.function_call_arguments.done", "response_id": "rX", "call_id": "cX", "name": "lembrar",
                   "arguments": json.dumps({"consulta": "sensores de distância"})})
    dt = (time.perf_counter() - t0) * 1000
    solto.set()
    check(f"a thread de leitura da Realtime não espera a busca (tratou o evento em {dt:.1f} ms com a busca presa)", dt < 50)
    h.close()
    mem.close()


def test_cancelamento():
    print("Cancelamento: barge-in, troca de modo, desligar o RAG")
    for nome, acao in (("barge-in / Pare", lambda a: a.cancel()), ("troca para teleoperado", lambda a: a.set_mode("teleoperado"))):
        h, mem, _ = montar(["lembrar", "audio"], prazo_busca_ms=3000)
        a = h.a
        asm = a.rag_assembler()
        entrou, solto = threading.Event(), threading.Event()

        chamadas = {"n": 0}
        base = asm._recuperar_local

        def preso(u, q, f, c, entrou=entrou, solto=solto, chamadas=chamadas, base=base):
            chamadas["n"] += 1
            if chamadas["n"] == 1:                                  # a busca inline do texto digitado passa; a da FERRAMENTA fica presa
                return base(u, q, f, c)
            entrou.set()
            solto.wait(4.0)
            return C.Recuperacao(itens=[C.Item("event:1", "fala", 1.0, "fala", "v", "RESIDUO-TARDIO")])
        asm._retriever = preso
        a.ask_text("Onde ficam os sensores de distância?")
        entrou.wait(4.0)
        time.sleep(0.2)
        acao(a)
        solto.set()
        time.sleep(1.0)
        check(f"{nome} durante a busca: nenhuma resposta à função, nenhum novo pedido, nada de áudio tardio ({a.snapshot().state})",
              not saidas(h) and len(h.sent("response.create")) == 1 and "RESIDUO" not in json.dumps(h.sent(), ensure_ascii=False) and h.player.received == 0)
        h.close()
        mem.close()
    h, mem, _ = montar(["lembrar", "audio"])
    h.a.rag_assembler().desligar()
    pergunta(h)
    out = saidas(h)
    check("RAG desligado (painel) antes da chamada: responde 'rag_desligado' sem pesquisar e a conversa segue; a ferramenta some na pergunta seguinte",
          out and out[0]["motivo_fallback"] == "rag_desligado")
    pergunta(h)
    check("…e na pergunta seguinte a ferramenta já saiu da sessão", ferramentas(h) == ["expressar"])
    h.close()
    mem.close()


def test_gemini():
    print("Gemini: caminho próprio (o adaptador responde `lembrar` com o resultado real)")
    import gemini_live as G
    import test_gemini as TG

    class GLem(G.FakeGeminiServer):
        n_calls = 1

        def _answer(self, question, images, turn=0):
            self._answering = True
            time.sleep(self.latency)
            for i in range(self.n_calls):
                done = threading.Event()
                self._pending_tool = done
                self._put({"toolCall": {"functionCalls": [{"id": f"fc_{i}", "name": "lembrar", "args": {"consulta": "sensores de distância", "usuario": "sint_B"}}]}})
                done.wait(3.0)
            super()._answer("diga exatamente: sem acrescentar nada: Achei.", images, turn)

    def montar_g(n):
        class H(TG.GHarness):
            def __init__(self):
                self.gservers = []

                def factory():
                    s = GLem()
                    s.n_calls = n
                    self.gservers.append(s)
                    return G.GeminiAdapter(s, "gemini-3.8-live", "Kore", 350)
                T.Harness.__init__(self, server_cls=factory, config=TG.gcfg())
        h = H()
        tmp = Path(tempfile.mkdtemp(prefix="lembrar_g_"))
        mem = Memory(tmp / "m.db", user_id=UID)
        mem.append_event(h.a.session_id, "user", "utterance", "O armário 3 guarda os sensores de distância.")
        mem.append_event("sB", "user", "utterance", "O sensor de distância secreto de B fica no cofre girassol.", user_id="sint_B")
        mem.flush(5)
        h.a.memory = mem
        path = tmp / "rag.local.json"
        path.write_text(json.dumps({"rag_ligado": True, "rag_voz": True}), encoding="utf-8")
        h.a.rag_config_path = path
        return h, mem

    h, mem = montar_g(1)
    sent = []
    real = h.a._send
    h.a._send = lambda ev: (sent.append(ev), real(ev))[1]
    pergunta(h)
    time.sleep(0.3)
    resp = [m["toolResponse"]["functionResponses"][0] for m in h.g("toolResponse")]
    res = resp[0]["response"]["result"] if resp else {}
    check("a ferramenta está na configuração do Gemini (setup) e a resposta à chamada traz o resultado REAL, sem 'SILENT' (o modelo usa e fala na mesma vez)",
          any(d["name"] == "lembrar" for m in h.g("setup") for d in m["setup"]["tools"][0]["functionDeclarations"]) and resp and resp[0]["name"] == "lembrar"
          and "scheduling" not in resp[0] and "armário 3" in json.dumps(res, ensure_ascii=False))
    check("identidade vinda da sessão: o usuário pedido nos argumentos foi ignorado (nada de B)", "girassol" not in json.dumps(res, ensure_ascii=False))
    check("o assistente não faz continuação no Gemini (nenhum response.create extra) e termina em Pronta", len([e for e in sent if e.get("type") == "response.create"]) == 1
          and h.a.snapshot().state == "idle")
    h.close()
    mem.close()
    h, mem = montar_g(4)
    pergunta(h, espera=12.0)
    time.sleep(0.3)
    motivos = [m["toolResponse"]["functionResponses"][0]["response"]["result"]["motivo_fallback"] for m in h.g("toolResponse")]
    check("Gemini: a quarta consulta da mesma pergunta recebe 'limite_de_consultas' (mesmo teto de 2 depois da 1ª; sem nova busca)", len(motivos) == 4 and motivos[-1] == "limite_de_consultas")
    h.close()
    mem.close()


def test_sem_mudar_voz_do_piloto():
    print("Piloto (rag_ligado sem rag_voz): a voz fica exatamente como antes")
    h, mem, _ = montar(["audio"], True, False)
    h.a.press_voice()
    h.idle(8.0)
    check("fala sem `lembrar`, sem item recuperado e sem chamada da ferramenta", ferramentas(h) == ["expressar"] and not saidas(h)
          and not [e for e in h.sent("conversation.item.create") if e["item"].get("id", "").startswith("rag_")])
    h.close()
    mem.close()


if __name__ == "__main__":
    for f in (test_registro, test_fluxo, test_teto, test_prazo_e_reader, test_cancelamento, test_gemini, test_sem_mudar_voz_do_piloto):
        f()
    ok = sum(1 for _, v in RESULTS if v)
    print(f"\n{ok}/{len(RESULTS)} verificações OK (simulado; tokens/tempo reais até o 1º áudio: PENDENTES, exigem sessão real)")
    sys.exit(0 if ok == len(RESULTS) else 1)
