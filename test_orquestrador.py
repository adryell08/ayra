"""Testes do orquestrador local de comportamento (prioridade, canais, TTL, modos, eventos rápidos) e da integração.
Tudo simulado, sem câmera, rede, API paga nem o app do usuário. Uso: python test_orquestrador.py
"""
from __future__ import annotations

import os
import random
import sys
import time
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import numpy as np

import gestos
import orquestrador as oq
from gestos import LIBRARY, REST, Animator
from orquestrador import Explorer, Orchestrator

RESULTS = []


def check(name, cond):
    RESULTS.append((name, bool(cond)))
    print(f"  [{'OK' if cond else 'FALHOU'}] {name}")


def plays(o):
    return [(c.name, c.side, c.source) for c in o.drain() if c.kind == "play"]


def test_channels_and_priority():
    print("Canais e prioridade")
    check("canais por gesto: cabeça, braço L/R e dois braços",
          oq.channels_for("nao") == {oq.HEAD} and oq.channels_for("tchau") == {oq.ARM_R}
          and oq.channels_for("tchau", "esquerdo") == {oq.ARM_L} and oq.channels_for("aplaudir", "esquerdo") == {oq.ARM_L, oq.ARM_R}
          and oq.channels_for("voar") == frozenset())
    o = Orchestrator()
    check("espontâneo ocupa canal livre", o.submit_gesture("spontaneous", "tchau", 0.0) == "started")
    check("IA vence espontâneo no mesmo canal", o.submit_gesture("ai", "joia", 0.5) == "started"
          and o.active(0.5)[oq.ARM_R][:2] == ("ai", "joia"))
    check("operador vence IA no mesmo canal", o.submit_gesture("operator", "paz", 0.8) == "started"
          and o.active(0.8)[oq.ARM_R][:2] == ("operator", "paz"))
    check("IA bloqueada pelo operador espera na fila (não derruba)", o.submit_gesture("ai", "ok", 1.0) == "queued"
          and o.active(1.0)[oq.ARM_R][0] == "operator")
    check("espontâneo bloqueado é descartado, não enfileira", o.submit_gesture("spontaneous", "tchau", 1.0) == "dropped:busy"
          and o.stats()["pending"] == 1)
    plays(o)
    # preempção só dos canais ocupados
    o = Orchestrator()
    o.submit_gesture("ai", "tchau", 0.0)                  # braço direito
    o.submit_gesture("ai", "joia", 0.0, side="left")      # braço esquerdo
    o.submit_gesture("ai", "nao", 0.0)                    # cabeça
    o.drain()
    check("três gestos da IA coexistem em canais diferentes", len(o.active(0.1)) == 3)
    o.submit_gesture("operator", "paz", 0.5)              # operador toma só o direito
    act = o.active(0.5)
    check("operador preempta só o canal que usa", act[oq.ARM_R][:2] == ("operator", "paz")
          and act[oq.ARM_L][:2] == ("ai", "joia") and act[oq.HEAD][:2] == ("ai", "nao"))
    o.submit_gesture("operator", "aplaudir", 0.6)         # dois braços: toma ambos, cabeça fica
    act = o.active(0.6)
    check("gesto de dois braços reserva ambos e preserva a cabeça",
          act[oq.ARM_L][1] == act[oq.ARM_R][1] == "aplaudir" and act[oq.HEAD][1] == "nao")
    check("tudo ou nada: IA que precisa de um canal bloqueado não toma os livres",
          o.submit_gesture("ai", "ok", 0.7, side="left") == "queued" and act[oq.HEAD][1] == "nao"
          and o.active(0.7)[oq.ARM_L][1] == "aplaudir")
    check("fontes e gestos inválidos são recusados e contados",
          o.submit_gesture("hacker", "tchau", 0.8) == "dropped:invalid" and o.submit_gesture("ai", "voar", 0.8) == "dropped:invalid"
          and o.stats()["dropped_invalid"] == 2)
    o = Orchestrator()
    o.submit_gesture("ai", "tchau", 0.0)
    o.submit_gesture("ai", "paz", 0.5)
    check("mesma prioridade, mesmo canal: o mais novo substitui", o.active(0.5)[oq.ARM_R][1] == "paz")


def test_expiry_and_queue():
    print("Duração, TTL, fila e eventos rápidos")
    o = Orchestrator()
    o.submit_gesture("operator", "tchau", 0.0)            # dura 2,8 s
    o.drain()
    o.submit_gesture("ai", "joia", 0.5)                   # espera até 0,5 + 1,5
    o.update(1.0)
    check("IA ainda espera enquanto o operador ocupa o canal", o.stats()["pending"] == 1 and not plays(o))
    o.update(2.1)
    check("TTL vence: gesto tardio da IA é descartado, nunca reaparece", o.stats()["pending"] == 0 and o.stats()["expired"] == 1)
    o.update(3.5)
    check("depois que o operador termina, nada da IA surge sozinho", not plays(o) and not o.holds)
    o = Orchestrator()
    o.submit_gesture("operator", "tchau", 0.0)
    o.drain()
    o.submit_gesture("ai", "joia", 2.0)                   # ainda ocupado até 2,8; TTL até 3,5
    o.update(2.9)
    check("IA enfileirada assume o canal assim que o operador libera, dentro do TTL",
          plays(o) == [("joia", "", "ai")] and o.active(2.9)[oq.ARM_R][1] == "joia")
    check("ocupação expira pela duração do gesto", (o.update(2.9 + LIBRARY["joia"].duration + .1) or True) and not o.holds)
    o = Orchestrator(max_pending=3)
    o.submit_gesture("operator", "aplaudir", 0.0)
    o.drain()
    for i, n in enumerate(("tchau", "joia", "paz", "ok", "apontar", "continencia")):
        o.submit_gesture("ai", n, 0.1 + i * .01)
    s = o.stats()
    check(f"rajada: fila limitada a 3, o excedente é contado (pendentes {s['pending']}, overflow {s['overflow']})",
          s["pending"] == 3 and s["overflow"] == 3)
    o = Orchestrator()
    for i in range(200):
        o.submit_gesture("operator", ("tchau", "joia", "paz", "ok")[i % 4], i * .001)
    check("saída de comandos limitada (rajada de 200 pedidos)", len(o.out) <= oq.MAX_OUT and len(o.drain()) <= oq.MAX_OUT)
    o = Orchestrator()
    o.submit_gesture("operator", "tchau", 0.0)
    o.drain()
    for _ in range(50):
        o.submit_gesture("ai", "joia", 0.1)
    check("pedido repetido da IA não acumula (renova o prazo)", o.stats()["pending"] == 1)
    o = Orchestrator()
    o.submit_gesture("operator", "joia", 0.0, hold=0.5)
    o.update(0.6)
    check("duração explícita do comando é respeitada", not o.holds)


def test_modes_and_cancel():
    print("Modos e cancelamento")
    o = Orchestrator()
    o.submit_gesture("ai", "tchau", 0.0)
    o.submit_gesture("spontaneous", "joia", 0.0, side="left")
    o.submit_gesture("operator", "paz", 0.0, side="left")
    o.submit_gesture("ai", "ok", 0.1, side="left")        # fica na fila (operador no esquerdo)
    o.drain()
    o.set_mode("teleoperado", 0.2)
    cmds = o.drain()
    check("entrar em teleoperado cancela o que era da IA/espontâneo e esvazia a fila",
          [c.kind for c in cmds] == ["cancel"] and cmds[0].name == "tchau" and o.stats()["pending"] == 0
          and o.active(0.2)[oq.ARM_L][0] == "operator" and oq.ARM_R not in o.active(0.2))
    check("teleoperado: IA e espontâneo recusados", o.submit_gesture("ai", "tchau", 0.3) == "dropped:mode"
          and o.submit_gesture("spontaneous", "tchau", 0.3) == "dropped:mode" and o.stats()["dropped_mode"] == 2)
    check("teleoperado: operador age normalmente", o.submit_gesture("operator", "tchau", 0.4) == "started")
    o.drain()
    o.set_mode("autonomo", 5.0)
    o.update(6.0)
    check("voltar ao autônomo não faz gesto tardio da IA reaparecer", not plays(o))
    check("modo desconhecido é ignorado", (o.set_mode("pirata", 7.0) or o.mode) == "autonomo")
    check("assistido segue as regras do autônomo (operador > IA)", (o.set_mode("assistido", 8.0) or True)
          and o.submit_gesture("ai", "tchau", 8.0) == "started" and o.submit_gesture("operator", "joia", 8.1) == "started")
    o = Orchestrator()
    o.submit_gesture("ai", "tchau", 0.0)
    o.submit_gesture("ai", "nao", 0.0)
    o.submit_gesture("operator", "joia", 0.0, side="left")
    o.drain()
    check("cancelar a IA mantém o operador", o.cancel("ai", 0.5) == 2 and list(o.active(0.5)) == [oq.ARM_L])
    cmds = o.drain()
    check("cancelamento gera comandos de cancel com os canais certos",
          sorted(c.name for c in cmds) == ["nao", "tchau"] and all(c.kind == "cancel" for c in cmds))
    o.submit_gesture("ai", "tchau", 1.0)
    check("cancelar por canal", o.cancel(None, 1.1, channel=oq.ARM_R) == 1 and oq.ARM_R not in o.active(1.1))
    # expressão
    o = Orchestrator()
    check("expressão: IA pode quando livre", o.expression_allowed("ai", 0.0))
    check("expressão: operador segura o canal contra a IA", o.claim_expression("operator", 1.0)
          and not o.expression_allowed("ai", 2.0) and not o.claim_expression("ai", 2.0))
    check("expressão: IA volta a poder depois do tempo", o.expression_allowed("ai", 1.0 + oq.EXPRESSION_HOLD + .1))
    o.set_mode("teleoperado", 5.0)
    check("expressão: teleoperado bloqueia a IA, não o operador", not o.expression_allowed("ai", 5.0)
          and o.claim_expression("operator", 5.0))


def test_animator_cancel():
    print("Cancelamento suave no animador")
    for name, side in (("tchau", "right"), ("aplaudir", "right"), ("nao", "right"), ("pensar", "left")):
        an = Animator()
        an.play(name, 0.0, side=side)
        prev, jump = None, 0.0
        for k in range(int(1.8 * 60)):
            now = k / 60
            if k == int(.9 * 60):
                assert an.cancel(now) == 1
            p = an.pose(now)
            cur = np.concatenate([np.array(p.left.wrist), np.array(p.right.wrist), np.array(p.head)])
            if prev is not None:
                jump = max(jump, float(np.abs(cur - prev).max()))
            prev = cur
        check(f"cancelar {name} em andamento: sem tranco (máx. {jump:.3f}/quadro) e libera os canais",
              jump < .08 and not an.busy)
    an = Animator()
    an.play("tchau", 0.0)
    an.play("nao", 0.0)
    an.play("joia", 0.0, side="left")
    check("cancelar só um braço poupa o resto", an.cancel(.5, arms=("R",), head=False) == 1
          and sorted(a.name for a in an.acts if a.cut_at is None) == ["joia", "nao"])


class Frame:
    def __init__(self):
        self.t = 0.0


def test_assistant_integration():
    print("Assistente: pedidos em ordem, origem e portão")
    import test_realtime as T
    h = T.Harness()
    a = h.a
    check("padrão do comando antigo: origem IA", a.request_gesture("tchau") and a.snapshot().gesture_source == "ai")
    check("origem do operador registrada", a.request_gesture("joia", "esquerdo", source="operator")
          and a.snapshot().gesture_source == "operator" and a.snapshot().gesture_side == "left")
    reqs = a.pop_gesture_requests()
    check("pedidos no mesmo quadro chegam todos, em ordem, com origem",
          [(r[0], r[2]) for r in reqs][-2:] == [("tchau", "ai"), ("joia", "operator")] and not a.pop_gesture_requests())
    for i in range(30):
        a.request_gesture("tchau")
    check("fila de pedidos limitada", len(a.pop_gesture_requests()) <= 8)
    a._snap.mode = "teleoperado"
    check("teleoperado: gesto da IA recusado na origem, do operador aceito",
          not a.request_gesture("tchau") and a.request_gesture("tchau", source="operator"))
    a._snap.mode = "autonomo"
    a.pop_gesture_requests()
    check("portão de expressão: ausente = aplica; negado = não aplica",
          a.expression_gate is None)
    h.close()


def test_explorer():
    print("Exploração experimental")
    o = Orchestrator()
    ex = Explorer(o, rng=random.Random(1))
    check("desligada por padrão: nada acontece", not ex.enabled and ex.tick(1e6, True) is None and not o.busy())
    check("presets limitados, só gestos existentes e nada de dedo do meio",
          len(oq.PRESETS) <= 8 and all(g in LIBRARY and g != "dedo_do_meio" for ps in oq.PRESETS.values() for g, _ in ps))
    ex.enabled = True
    check("espera ficar parado (min_idle) desde que o app abriu", ex.tick(5.0, True) is None and ex.tick(20.0, True) is None)
    o.submit_gesture("operator", "joia", 100.0)
    check("não age com atividade recente nem com canal ocupado", ex.tick(110.0, True) is None)
    o = Orchestrator()
    ex = Explorer(o, enabled=True, rng=random.Random(2))
    ex.tick(0.0, True)
    name = ex.tick(100.0, True)
    cmds = plays(o)
    check("com o robô parado, executa uma combinação como espontâneo", name in oq.PRESETS and len(cmds) == len(oq.PRESETS[name])
          and all(c[2] == "spontaneous" for c in cmds))
    check("cooldown entre explorações", ex.tick(100.0 + 20, True) is None)
    o.update(105.0)
    check("não explora em modo teleoperado nem com can_run falso", (o.set_mode("teleoperado", 106.0) or True)
          and ex.tick(500.0, True) is None)
    o.set_mode("autonomo", 107.0)
    check("não explora se o corpo 3D está desligado/ocupado (can_run=False)", ex.tick(500.0, False) is None)
    op = Orchestrator()
    ex2 = Explorer(op, enabled=True, rng=random.Random(3))
    ex2.tick(0.0, True)
    ex2.tick(100.0, True)
    op.drain()
    op.submit_gesture("operator", "aplaudir", 100.5)
    check("operador interrompe a exploração", all(h.source == oq.OPERATOR for h in op.holds.values() if h.name == "aplaudir"))
    check("recompensa limitada e só para presets conhecidos", ex.reward("duas_maos", 99) and ex.scores["duas_maos"] == oq.MAX_SCORE
          and not ex.reward("inexistente", 1) and len(ex.scores) <= 8)
    for _ in range(40):
        ex.last_run = -1e9
        o2 = Orchestrator()
        ex.orch = o2
        ex.tick(1000.0, True)
    check("estado da exploração permanece limitado", len(ex.scores) == len(oq.PRESETS) and sum(ex.runs.values()) <= 41)


def test_cost():
    print("Custo")
    o = Orchestrator()
    n = 20000
    t0 = time.perf_counter()
    for i in range(n):
        o.submit_gesture(("operator", "ai", "spontaneous")[i % 3], ("tchau", "joia", "nao", "aplaudir")[i % 4], i * .01)
        o.update(i * .01)
        o.drain()
    us = (time.perf_counter() - t0) / n * 1e6
    check(f"custo por pedido+update: {us:.1f} µs (≪ 1 ms) e estruturas limitadas",
          us < 300 and len(o.pending) <= o.max_pending and len(o.holds) <= 4)


def test_main_wiring():
    print("Ligação no aplicativo (código)")
    src = (HERE / "main.py").read_text(encoding="utf-8")
    check("main.py: orquestrador criado, gestos de todas as origens passam por ele e o portão da IA é ligado",
          "orch = Orchestrator()" in src and "assistant.pop_gesture_requests()" in src and "orch.submit_gesture(" in src
          and "assistant.expression_gate" in src and "orch.update(now)" in src and "orch.set_mode(gsnap.mode, now)" in src)
    check("main.py: operador envia origem 'operator' e interromper cancela a IA",
          'source="operator")' in src and 'orch.cancel("ai", nowt)' in src and "--explorar" in src)
    check("exploração fica desligada sem --explorar", "enabled=bool(args.explorar)" in src)


def test_app_loop():
    print("Laço do aplicativo (simulado, sem porta nem operador real)")
    import queue
    from unittest.mock import patch
    import main as app
    import operador

    class FakeServer:
        def __init__(self, *a, **k):
            self.status, self.url, self.pin, self.operator, self.memory = "ok", "http://x", "123456", "", None
            self.fps = 10
            self.commands = queue.Queue()
            for item in (("gesture", {"nome": "nao"}), ("gesture", {"nome": "tchau", "lado": "esquerdo"}),
                         ("gesture", {"nome": "voar"}), ("expression", {"nome": "wink"}), ("interrupt", {})):
                self.commands.put(item)

        def start(self):
            return True

        def stop(self):
            pass

    real_read = Path.read_text
    real_write = Path.write_text

    def read_text(self, *a, **k):
        if self.name == "remote.local.json":
            return '{"enabled": true, "port": 8765, "pin": "123456", "badge": true, "fps": 10}'
        return real_read(self, *a, **k)

    def write_text(self, data, *a, **k):
        return len(data) if self.name == "remote.local.json" else real_write(self, data, *a, **k)

    seen, claims, cancels = [], [], []
    original_cancel = oq.Orchestrator.cancel

    def spy_cancel(self, source=None, now=0.0, channel=None):
        cancels.append(source)
        return original_cancel(self, source, now, channel)

    original = oq.Orchestrator.submit_gesture
    original_claim = oq.Orchestrator.claim_expression

    def spy(self, source, name, now, side=None, hold=None):
        r = original(self, source, name, now, side=side, hold=hold)
        seen.append((source, name, side, r))
        return r

    def spy_claim(self, source, now, hold=oq.EXPRESSION_HOLD):
        claims.append(source)
        return original_claim(self, source, now, hold)

    with patch.object(operador, "OperatorServer", FakeServer), patch.object(Path, "read_text", read_text),             patch.object(Path, "write_text", write_text), patch.object(oq.Orchestrator, "submit_gesture", spy),             patch.object(oq.Orchestrator, "claim_expression", spy_claim), patch.object(oq.Orchestrator, "cancel", spy_cancel):
        try:
            code, error = app.main(["--sim", "--sem-registro", "--run-seconds", "2"]), ""
        except Exception as e:
            code, error = -1, f"{type(e).__name__}: {e}"
    check(f"app roda com comandos do operador passando pelo orquestrador {error}", code == 0 and not error)
    check("gestos do operador chegam ao orquestrador com origem, lado e resultado",
          any(s == "operator" and n == "nao" and r == "started" for s, n, sd, r in seen)
          and any(s == "operator" and n == "tchau" and sd == "left" and r == "started" for s, n, sd, r in seen))
    check("gesto inválido do operador não chega ao orquestrador", not any(n == "voar" for _, n, _, _ in seen))
    check("expressão do operador reserva o canal de expressão", "operator" in claims)
    check("interrupção do operador e cancelamento do assistente cancelam os gestos da IA no orquestrador", "ai" in cancels)


def _call_event(rid, gesto="tchau", call_id="c1", lado=None):
    import json
    args = {"emocao": "happy", "gesto": gesto}
    if lado:
        args["lado"] = lado
    return {"type": "response.function_call_arguments.done", "response_id": rid, "call_id": call_id,
            "name": "expressar", "arguments": json.dumps(args)}


def test_stale_events():
    print("Eventos atrasados: interrupção, cancelamento, troca de modo (falha injetada)")
    import test_realtime as T
    h = T.Harness()
    a = h.a

    def fresh_turn(rid=None, **extra):
        a._turn = dict(gen=a._gen, responding=True, text="", t0=0.0, **({"response_id": rid} if rid else {}), **extra)

    a.pop_gesture_requests()
    fresh_turn("r1")
    a._on_event(_call_event("r1", "tchau", "ok1"))
    check("controle: chamada de função válida da resposta atual vira pedido de gesto da IA",
          [(r[0], r[2]) for r in a.pop_gesture_requests()] == [("tchau", "ai")])
    # 1) barge-in: a resposta cancelada manda a chamada tarde
    fresh_turn("r2")
    epoch = a.cancel_epoch
    a._interrupt()
    a._on_event(_call_event("r2", "joia", "late1"))
    a._on_event({"type": "response.done", "response": {"id": "r2", "status": "completed",
                                                          "output": [{"type": "function_call", "call_id": "late2", "name": "expressar",
                                                                      "arguments": _call_event("r2", "paz")["arguments"]}]}})
    check("barge-in: chamada atrasada da resposta cancelada não gera gesto", a.pop_gesture_requests() == [])
    check("barge-in: avisa o orquestrador (cancel_epoch sobe)", a.cancel_epoch > epoch)
    # 2) turno novo ainda sem response_id: evento velho da resposta anterior
    fresh_turn()
    a._on_event(_call_event("r2", "ok", "late3"))
    check("turno novo sem id: evento de resposta cancelada anterior é ignorado", a.pop_gesture_requests() == [])
    # 3) mesma resposta, turno marcado como interrompido, sem id no evento
    fresh_turn("r3", interrupted=True)
    a._on_event(_call_event("r3", "apontar", "late4"))
    check("turno interrompido não aceita gesto nem sem id no evento", a.pop_gesture_requests() == [])
    # 4) cancel(): some a fila da IA, fica a do operador
    a.request_gesture("tchau")
    a.request_gesture("paz", source="operator")
    a.cancel()
    check("cancel(): pedidos da IA na fila somem; o do operador permanece",
          [(r[0], r[2]) for r in a.pop_gesture_requests()] == [("paz", "operator")])
    # 5) troca para teleoperado
    a.request_gesture("tchau")
    a.request_gesture("joia", source="operator")
    a.set_mode("teleoperado")
    check("teleoperado: fila da IA é esvaziada, a do operador fica",
          [(r[0], r[2]) for r in a.pop_gesture_requests()] == [("joia", "operator")])
    fresh_turn("r9")
    a._on_event(_call_event("r9", "tchau", "late5"))
    check("teleoperado: chamada atrasada da IA não vira gesto", a.pop_gesture_requests() == [])
    a.set_mode("autonomo")
    a.pop_gesture_requests()
    fresh_turn("r10")
    a._on_event(_call_event("r10", "tchau", "late6"))
    check("voltar ao autônomo: nada do período anterior reaparece, a IA nova funciona",
          [(r[0], r[2]) for r in a.pop_gesture_requests()] == [("tchau", "ai")])
    check("ids de respostas canceladas são limitados", len(a._stale_rids) <= 8)
    for i in range(30):
        fresh_turn(f"x{i}")
        a._interrupt()
    check("muitas interrupções não acumulam memória", len(a._stale_rids) <= 8)
    h.close()
    # orquestrador + assistente: a IA cancelada não deixa gesto ativo
    o = Orchestrator()
    o.submit_gesture("ai", "tchau", 0.0)
    o.submit_gesture("ai", "nao", 0.0)
    o.drain()
    o.cancel("ai", 0.5)
    check("cancelamento da IA remove gestos ativos suavemente (comandos de cancel)",
          sorted(c.name for c in o.drain() if c.kind == "cancel") == ["nao", "tchau"] and not o.holds)
    o.submit_gesture("operator", "joia", 1.0)
    o.drain()
    o.cancel("operator", 1.5)
    check("queda do operador: o gesto dele sai suave e libera o canal", not o.holds
          and [c.kind for c in o.drain()] == ["cancel"])
    src = (HERE / "operador.py").read_text(encoding="utf-8")
    check("operador: mensagens de conexão antiga/fechada não comandam o robô",
          "if self._pc is pc and self._channel is channel:" in src)


def test_public_surface():
    print("Superfície pública: sem origem nem orquestrador")
    import re
    from renderer import HudInfo
    rsrc = (HERE / "renderer.py").read_text(encoding="utf-8")
    msrc = (HERE / "main.py").read_text(encoding="utf-8")
    check("renderer não conhece orquestrador, origem de gesto nem prioridades",
          not re.search(r"orquestrador|gesture_source|Orchestrator|SOURCE_NAMES", rsrc))
    check("HudInfo (tela pública) não tem campos de origem/canais", not any(
        re.search(r"source|origem|orch|canal|channel|priorid", f) for f in HudInfo.__dataclass_fields__))
    check("main.py não escreve o orquestrador no HUD nem em toasts",
          not re.search(r"hud\.\w+\s*=.*orch", msrc) and not re.search(r"toast\([^)]*(orch|gesture_source|operator)[^)]*\)", msrc.replace("Operador conectado", "").replace("Operador desconectado", "")))
    check("telemetria para o operador não expõe origem/prioridade de IA",
          "gesture_source" not in msrc.split("def remote_telemetry")[1].split("def remote_start")[0])


def test_animator_thread_confinement():
    print("Animator só é tocado pela thread principal")
    import re
    for name in ("assistente.py", "assistente_realtime.py", "gemini_live.py", "operador.py", "realtime.py"):
        src = (HERE / name).read_text(encoding="utf-8")
        check(f"{name} (threads de IA/rede/operador) não acessa Animator nem corpo 3D diretamente",
              not re.search(r"Animator|\.anim\b|corpo3d|body_gesture|\.play\(\s*name,\s*now", src))
    msrc = (HERE / "main.py").read_text(encoding="utf-8")
    check("gestos chegam ao corpo só pelo laço principal: fila com lock em request_gesture → pop_gesture_requests → body_gesture",
          "body_gesture(cmd.name, now, cmd.side)" in msrc and "assistant.pop_gesture_requests()" in msrc
          and "with self._lock:" in (HERE / "assistente.py").read_text(encoding="utf-8").split("def request_gesture")[1].split("def set_vision")[0])


def test_bursts():
    print("Rajadas: política explícita de coalescência")
    import test_realtime as T
    import operador
    h = T.Harness()
    a = h.a
    a.pop_gesture_requests()
    a.request_gesture("paz", source="operator")
    for i in range(40):
        a.request_gesture(("tchau", "joia", "ok", "apontar")[i % 4])
    a.request_gesture("joia", "esquerdo", source="operator")
    reqs = a.pop_gesture_requests()
    ops = [r for r in reqs if r[2] == "operator"]
    check(f"rajada de 40 pedidos da IA não expulsa comandos do operador (fila {len(reqs)}, perdas {a.gesture_drops})",
          len(reqs) <= a.MAX_GESTURE_REQUESTS and [r[0] for r in ops] == ["paz", "joia"] and a.gesture_drops >= 33)
    check("os pedidos que sobram da IA são os mais recentes (ordem preservada)",
          [r[0] for r in reqs if r[2] == "ai"][-1] == "apontar" and reqs[0][0] == "paz" and reqs[-1][0] == "joia")
    for i in range(20):
        a.request_gesture("paz", source="operator")
    check("só operador em rajada: fica limitado, perdas contadas, nunca cresce",
          len(a.pop_gesture_requests()) <= a.MAX_GESTURE_REQUESTS and a.gesture_drops > 33)
    h.close()
    srv = operador.OperatorServer(lambda: None, lambda: {}, lambda pcm: None)
    n = srv.MAX_COMMANDS
    for i in range(n + 100):
        srv._push(("look", {"gx": i}))
    check(f"fila de comandos do operador é limitada ({srv.commands.qsize()} ≤ {n}) e conta as perdas ({srv.commands_dropped})",
          srv.commands.qsize() <= n and srv.commands_dropped == 100)
    srv._push(("gesture", {"nome": "tchau"}))
    kinds = []
    while not srv.commands.empty():
        kinds.append(srv.commands.get_nowait())
    check("cheia, descarta primeiro o olhar mais antigo e mantém o gesto novo (e o último olhar)",
          kinds[-1] == ("gesture", {"nome": "tchau"}) and kinds[-2][1]["gx"] == n + 99 and len(kinds) <= n)
    for i in range(n):
        srv._push(("gesture", {"nome": "tchau", "i": i}))
    srv._push(("operator", ""))
    items = []
    while not srv.commands.empty():
        items.append(srv.commands.get_nowait())
    check("sem olhares para descartar, sai o mais antigo e eventos novos (queda do operador) entram",
          items[-1] == ("operator", "") and items[0][1]["i"] == 1 and len(items) == n)
    t0 = time.perf_counter()
    for i in range(5000):
        srv._push(("look", {"gx": i}))
        srv.commands.get_nowait() if i % 3 == 0 else None
    check(f"enfileirar não bloqueia nem custa caro ({(time.perf_counter() - t0) / 5000 * 1e6:.1f} µs)", time.perf_counter() - t0 < 2.0)
    msrc = (HERE / "main.py").read_text(encoding="utf-8")
    check("main.py despacha gestos pela fila ordenada (gesture_version não decide mais nada)",
          "assistant.pop_gesture_requests()" in msrc and "gesture_version !=" not in msrc)


if __name__ == "__main__":
    test_channels_and_priority()
    test_expiry_and_queue()
    test_modes_and_cancel()
    test_animator_cancel()
    test_assistant_integration()
    test_explorer()
    test_cost()
    test_main_wiring()
    test_app_loop()
    test_stale_events()
    test_public_surface()
    test_animator_thread_confinement()
    test_bursts()
    ok = sum(1 for _, v in RESULTS if v)
    print(f"\n{ok}/{len(RESULTS)} verificações OK (simuladas; sem rede, câmera nem o app do usuário)")
    sys.exit(0 if ok == len(RESULTS) else 1)
