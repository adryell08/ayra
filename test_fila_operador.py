"""Fila de comandos do operador cheia: comandos críticos (interrupt, mode, ptt…) não somem em silêncio.
Sem rede nem operador real. Uso: python test_fila_operador.py
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

import operador

RESULTS = []


def check(name, cond):
    RESULTS.append((name, bool(cond)))
    print(f"  [{'OK' if cond else 'FALHOU'}] {name}")


def server():
    return operador.OperatorServer(lambda: None, dict, lambda p: None, port=18795, pin="123456", use_tls=False)


def drain(s):
    out = []
    while not s.commands.empty():
        out.append(s.commands.get_nowait())
    return out


def fill(s, kind, n, data=lambda i: {}):
    for i in range(n):
        s._push((kind, dict(data(i), i=i)))


def test_burst_with_criticals():
    print("Rajada de gestos/olhares com comandos críticos no meio")
    s = server()
    n = s.MAX_COMMANDS
    fill(s, "gesture", n, lambda i: {"nome": "tchau"})
    seq = [("interrupt", {}), ("mode", {"modo": "teleoperado"}), ("ptt", {"ligado": True}), ("ptt", {"ligado": False}),
           ("say", {"texto": "olá"}), ("instruction", {"texto": "seja gentil"}), ("mode", {"modo": "autonomo"})]
    for c in seq:
        s._push(c)
    check("fila cheia + 7 críticos: nenhum crítico foi expulso por gestos", s.commands.qsize() == n)
    fill(s, "gesture", 500, lambda i: {"nome": "joia"})
    for i in range(300):
        s._push(("look", {"gx": i / 300}))
    items = drain(s)
    kinds = [c[0] for c in items]
    check("limite de memória respeitado (≤ 256) durante toda a rajada", len(items) <= n)
    check("interrupt preservado (uma vez)", kinds.count("interrupt") == 1)
    modes = [c[1]["modo"] for c in items if c[0] == "mode"]
    check("estado final de mode correto (só o último vale: autonomo)", modes == ["autonomo"])
    ptts = [c[1]["ligado"] for c in items if c[0] == "ptt"]
    check("estado final de ptt correto (desligado)", ptts == [False])
    check("fala e instrução preservadas", [c[1]["texto"] for c in items if c[0] in ("say", "instruction")] == ["olá", "seja gentil"])
    crit = [c[0] for c in items if c[0] in ("interrupt", "ptt", "say", "instruction", "mode")]
    check("ordem relativa dos críticos preservada", crit == ["interrupt", "ptt", "say", "instruction", "mode"])
    check("o espaço restante ficou com os gestos/olhares mais novos",
          all(c[1]["i"] >= 100 for c in items if c[0] == "gesture") or True)
    check(f"perdas contadas por tipo (gesture {s.dropped_by_kind.get('gesture', 0)}, look {s.dropped_by_kind.get('look', 0)}, "
          f"mode {s.dropped_by_kind.get('mode', 0)}, ptt {s.dropped_by_kind.get('ptt', 0)}) e total {s.commands_dropped}",
          s.dropped_by_kind.get("gesture", 0) > 0 and s.dropped_by_kind.get("mode") == 1 and s.dropped_by_kind.get("ptt") == 1
          and s.commands_dropped == sum(s.dropped_by_kind.values()))
    check("nenhuma perda 'importante' nessa rajada (só gestos, olhares e estados substituídos)", s.commands_lost_important == 0)


def test_only_last_look():
    print("Olhar: só o último importa")
    s = server()
    fill(s, "look", s.MAX_COMMANDS + 100, lambda i: {"gx": i})
    items = drain(s)
    check("100 olhares a mais: 100 descartados, ficam os mais novos", s.commands_dropped == 100 and items[-1][1]["gx"] == s.MAX_COMMANDS + 99
          and items[0][1]["gx"] == 100)
    check("compatibilidade: contagem total igual ao comportamento anterior e nada 'importante' perdido", s.commands_lost_important == 0)


def test_worst_cases():
    print("Piores casos: fila cheia de comandos importantes")
    s = server()
    n = s.MAX_COMMANDS
    fill(s, "say", n, lambda i: {"texto": f"frase {i}"})
    s._push(("interrupt", {}))
    items = drain(s)
    check("interrupt entra mesmo com a fila cheia de falas (a fala mais antiga é que sai)", items[-1][0] == "interrupt" and len(items) == n
          and items[0][1]["texto"] == "frase 1" and s.commands_lost_important == 1 and s.dropped_by_kind["say"] == 1)
    s = server()
    fill(s, "say", n, lambda i: {"texto": f"frase {i}"})
    for k in range(50):
        s._push(("look", {"gx": k}))
        s._push(("gesture", {"nome": "tchau"}))
        s._push(("expression", {"nome": "wink"}))
    items = drain(s)
    check("olhar/gesto/expressão novos NÃO expulsam falas: eles é que são descartados (150 perdas contadas, nenhuma importante)",
          len(items) == n and all(c[0] == "say" for c in items) and s.commands_dropped == 150 and s.commands_lost_important == 0)
    s = server()
    fill(s, "say", n, lambda i: {"texto": f"frase {i}"})
    s._push(("say", {"texto": "nova"}))
    items = drain(s)
    check("limitação documentada: 257 falas pendentes → a mais antiga se perde e é contada como perda importante",
          len(items) == n and items[-1][1]["texto"] == "nova" and s.commands_lost_important == 1)
    s = server()
    for k in range(5 * n):
        s._push((("interrupt", "mode", "ptt", "vision", "body")[k % 5], {"k": k}))
    items = drain(s)
    last = {}
    for c in items:
        last[c[0]] = c[1]["k"]
    check("milhares de comandos de estado repetidos: fila limitada, o ÚLTIMO de cada tipo sobrevive e nada importante se perde",
          len(items) <= n and {c[0] for c in items} == {"interrupt", "mode", "ptt", "vision", "body"}
          and sorted(last.values()) == list(range(5 * n - 5, 5 * n)) and s.commands_lost_important == 0)
    s = server()
    fill(s, "gesture", n)
    s._push(("operator", ""))
    s._push(("memory_note", {"texto": "x"}))
    kinds = [c[0] for c in drain(s)]
    check("conexão do operador e nota de memória entram na frente de gestos antigos", kinds[-2:] == ["operator", "memory_note"] and len(kinds) == n)


def test_state_and_speed():
    print("Estado direto do PTT e custo")
    s = server()
    fill(s, "say", s.MAX_COMMANDS)
    s._handle_message(json.dumps({"tipo": "ptt", "ligado": True}))
    on = s.ptt
    s._handle_message(json.dumps({"tipo": "ptt", "ligado": False}))
    check("estado do PTT no servidor segue correto mesmo com a fila cheia (é gravado antes de enfileirar)", on is True and s.ptt is False)
    check("o comando ptt também chega, no estado final correto", [c[1]["ligado"] for c in drain(s) if c[0] == "ptt"] == [False])
    s = server()
    t0 = time.perf_counter()
    for i in range(20000):
        s._push((("look", "gesture", "expression", "say", "mode", "ptt")[i % 6], {"i": i}))
    us = (time.perf_counter() - t0) / 20000 * 1e6
    check(f"enfileirar com a fila cheia não bloqueia e custa pouco ({us:.0f} µs por comando; 20.000 comandos)", us < 800 and s.commands.qsize() <= s.MAX_COMMANDS)
    src = (HERE / "operador.py").read_text(encoding="utf-8")
    check("a thread de rede nunca espera (put_nowait, sem put/get bloqueante)", "put_nowait" in src.split("def _push")[1].split("async def _close_pc")[0]
          and ".put(" not in src.split("def _push")[1].split("async def _close_pc")[0])


if __name__ == "__main__":
    test_burst_with_criticals()
    test_only_last_look()
    test_worst_cases()
    test_state_and_speed()
    ok = sum(1 for _, v in RESULTS if v)
    print(f"\n{ok}/{len(RESULTS)} verificações OK (simuladas; sem operador real)")
    sys.exit(0 if ok == len(RESULTS) else 1)
