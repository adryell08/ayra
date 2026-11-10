"""Continuação depois de ferramentas só visuais (expressar): no máximo UMA continuação dedicada à fala por pergunta.
Simulado (servidores falsos Realtime e Gemini): sem rede, sem API paga. Uso: python test_ferramentas.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import assistente_realtime as ar
import realtime as rt
import test_realtime as T

RESULTS = []


def check(name, cond):
    RESULTS.append((name, bool(cond)))
    print(f"  [{'OK' if cond else 'FALHOU'}] {name}")


def spy(h):
    """Registra tudo que o assistente envia ao servidor."""
    sent = []
    real = h.a._send

    def send(ev):
        sent.append(ev)
        return real(ev)
    h.a._send = send
    return sent


def creates(sent):
    return [e for e in sent if e.get("type") == "response.create"]


def outputs(sent):
    return [e for e in sent if e.get("type") == "conversation.item.create" and e["item"].get("type") == "function_call_output"]


class ToolServer(rt.FakeRealtimeServer):
    """Servidor falso com roteiro: `plan` lista o que cada resposta faz ("fn", "fn2", "audio", "fn+audio", "func")."""

    def __init__(self, plan, **kw):
        super().__init__(**kw)
        self.plan = list(plan)
        self.n = 0

    def _respond(self, request, cancel):
        step = self.plan[min(self.n, len(self.plan) - 1)]
        self.n += 1
        rid, mid = self._id("resp"), self._id("item")
        self._emit({"type": "response.created", "response": {"id": rid, "status": "in_progress"}})
        out = []
        names = {"fn": ["expressar"], "fn2": ["expressar", "expressar"], "fn+audio": ["expressar"], "func": ["buscar"]}.get(step, [])
        for k, name in enumerate(names):
            cid = f"call_{self.n}_{k}"
            args = json.dumps({"emocao": "happy", "gesto": "tchau"}) if name == "expressar" else "{}"
            self._emit({"type": "response.function_call_arguments.done", "response_id": rid, "call_id": cid, "name": name, "arguments": args})
            out.append({"id": cid, "type": "function_call", "name": name, "call_id": cid, "arguments": args})
        if step in ("audio", "fn+audio"):
            import base64
            import numpy as np
            self._emit({"type": "response.output_item.added", "response_id": rid, "item": {"id": mid, "type": "message", "role": "assistant"}})
            pcm = (np.sin(2 * np.pi * 180 * np.arange(2400) / 24000) * 4000).astype(np.int16)
            self._emit({"type": "response.output_audio_transcript.delta", "response_id": rid, "item_id": mid, "delta": "Olá"})
            self._emit({"type": "response.output_audio.delta", "response_id": rid, "item_id": mid,
                        "delta": base64.b64encode(pcm.tobytes()).decode()})
            self._emit({"type": "response.output_audio.done", "item_id": mid})
            out.append({"id": mid, "type": "message", "role": "assistant", "content": [{"type": "output_audio", "transcript": "Olá"}]})
        self._emit({"type": "response.done", "response": {"id": rid, "status": "completed", "output": out}})


def run(plan, ask="oi", wait=1.2):
    h = T.Harness(server_cls=lambda **kw: ToolServer(plan, **kw))
    sent = spy(h)
    h.a.ask_text(ask)
    h.idle(8.0)
    time.sleep(wait)                                            # pedidos tardios apareceriam aqui
    return h, sent


def test_openai_flow():
    print("Realtime: função → áudio, função → função, várias funções, áudio + função")
    h, sent = run(["fn", "audio"])
    c = creates(sent)
    check("função → áudio: 2 pedidos (pergunta + 1 continuação) e a continuação proíbe novas ferramentas (tool_choice none)",
          len(c) == 2 and c[1].get("response", {}).get("tool_choice") == "none" and h.a.snapshot().state == "idle")
    check("a fala da continuação chegou e o turno terminou bem", h.a._turn.get("had_audio") and h.a.snapshot().message_kind != "warning")
    h.close()

    h, sent = run(["fn"] * 10)
    c = creates(sent)
    check(f"função → função (servidor que NUNCA fala): só {len(c)} pedidos (antes: sem limite)", len(c) == 2)
    snap = h.a.snapshot()
    check("estado terminal recuperável e neutro (idle + aviso, sem citar ferramenta/operador)",
          snap.state == "idle" and snap.message == ar.NO_SPEECH_NOTICE and snap.message_kind == "warning"
          and not any(w in snap.message.lower() for w in ("ferramenta", "função", "operador", "expressar", "erro")))
    check("as chamadas foram respondidas (sessão consistente) e nenhum pedido novo depois do encerramento", len(outputs(sent)) == 2)
    before = len(creates(sent))
    h.a.ask_text("tenta de novo")
    h.idle(8.0)
    time.sleep(0.8)
    check("recuperável: nova pergunta funciona e o contador reinicia (mais 2 pedidos, não mais)", len(creates(sent)) - before == 2)
    h.close()

    h, sent = run(["fn2", "audio"])
    check("várias funções na mesma resposta: 1 continuação só, ambas respondidas", len(creates(sent)) == 2 and len(outputs(sent)) == 2)
    h.close()

    h, sent = run(["fn+audio"])
    check("áudio + função na mesma resposta: nenhuma continuação (1 pedido)", len(creates(sent)) == 1 and h.a._turn.get("had_audio"))
    h.close()

    h, sent = run(["audio"])
    check("só áudio: 1 pedido, sem mudança", len(creates(sent)) == 1)
    h.close()


def test_functional_not_blocked():
    print("Ferramentas funcionais futuras não são limitadas")
    h, sent = run(["func", "func", "func", "audio"])
    check("ferramenta funcional: cada resultado pede nova resposta, sem o teto das cosméticas (4 pedidos)",
          len(creates(sent)) == 4 and all("response" not in e for e in creates(sent)[1:]))
    h.close()
    h, sent = run(["func", "fn", "audio"])
    c = creates(sent)
    check("mista: funcional depois cosmética ainda cabe 1 continuação cosmética (3 pedidos)", len(c) == 3 and c[2].get("response", {}).get("tool_choice") == "none")
    h.close()
    check("COSMETIC_TOOLS só tem expressar e o teto é 1", ar.COSMETIC_TOOLS == {"expressar"} and ar.MAX_COSMETIC_FOLLOWUPS == 1)


def test_cancel_and_late():
    print("Cancelamento e eventos atrasados")
    h = T.Harness()
    a = h.a
    sent = []
    a._send = lambda ev: sent.append(ev)
    a._new_turn(a._gen, time.perf_counter())
    a._turn["responding"] = True
    a._turn["response_id"] = "r1"
    done = {"id": "r1", "status": "completed", "output": [{"type": "function_call", "call_id": "c1", "name": "expressar",
                                                           "arguments": json.dumps({"emocao": "happy"})}]}
    a._interrupt()
    a._on_response_done(done)
    check("depois de interromper: nenhuma continuação nem resposta de função", creates(sent) == [])
    a._new_turn(a._gen, time.perf_counter())
    a._turn["responding"] = True
    a._turn["response_id"] = "r2"
    a._on_event({"type": "response.done", "response": dict(done, id="r1")})
    check("evento atrasado de resposta cancelada é ignorado (sem pedido)", creates(sent) == [])
    a._turn["response_id"] = "r2"
    a.cancel()
    a._on_response_done(dict(done, id="r2", status="cancelled"))
    check("resposta cancelada: nenhum pedido", creates(sent) == [])
    a._new_turn(a._gen, time.perf_counter())
    a._turn["responding"] = True
    a._gen += 1                                                 # a pergunta foi superada por outra
    a._on_response_done(dict(done, id="r3"))
    check("turno de geração antiga: nenhum pedido", creates(sent) == [])
    h.close()


def test_gemini_parity():
    print("Gemini Live: paridade do adaptador")
    import gemini_live as G
    import test_gemini as TG

    class SilentTool(G.FakeGeminiServer):
        def _answer(self, question, images, turn=0):
            self._answering = True
            self._put({"toolCall": {"functionCalls": [{"id": "fc_1", "name": "expressar", "args": {"emocao": "happy", "gesto": "tchau"}}]}})
            self._pending_tool = None
            self._put({"serverContent": {"turnComplete": True}})

    class H(TG.GHarness):
        def __init__(self):
            self.gservers = []

            def factory():
                s = SilentTool()
                self.gservers.append(s)
                return G.GeminiAdapter(s, "gemini-3.8-live", "Kore", 350)
            T.Harness.__init__(self, server_cls=factory, config=TG.gcfg())

    h = H()
    sent = spy(h)
    h.a.ask_text("oi")
    h.idle(8.0)
    time.sleep(1.0)
    c = creates(sent)
    snap = h.a.snapshot()
    check(f"Gemini que só chama a ferramenta e não fala: {len(c)} pedido (só o da pergunta), sem continuação, termina em idle com aviso neutro",
          len(c) == 1 and snap.state == "idle" and snap.message == ar.NO_SPEECH_NOTICE)
    check("o adaptador do Gemini declara que fala na própria vez (continues_after_tools)", G.GeminiAdapter.continues_after_tools is True)
    check("o adaptador do Gemini ignora tool_choice (usa só instructions) — o teto vale pelo contador local, não pelo campo",
          "tool_choice" not in __import__("inspect").getsource(G.GeminiAdapter._respond))
    h.close()


if __name__ == "__main__":
    test_openai_flow()
    test_functional_not_blocked()
    test_cancel_and_late()
    test_gemini_parity()
    ok = sum(1 for _, v in RESULTS if v)
    print(f"\n{ok}/{len(RESULTS)} verificações OK (simuladas; sem rede nem API paga)")
    sys.exit(0 if ok == len(RESULTS) else 1)
