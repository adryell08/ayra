"""Teleoperado = ZERO tráfego para o modelo (OpenAI Realtime/Gemini): operador e usuário conversam sem passar pela API paga.
Servidor Realtime FALSO (sem rede, sem chave). Também garante que instruções/notas dadas em teleoperado NÃO se perdem: viram
contexto e chegam ao modelo quando o robô volta ao autônomo/assistido.

Uso: python test_operador_sem_api.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import medir_custo_operador as M
import test_realtime as T

RESULTS = []


def check(name, cond):
    RESULTS.append((name, bool(cond)))
    print(f"  [{'OK' if cond else 'FALHOU'}] {name}")


def texts(events):
    out = []
    for e in events:
        if e.get("type") == "conversation.item.create":
            for c in e["item"].get("content", []) or []:
                out.append(c.get("text", ""))
    return out


def run():
    print("Teleoperado: nenhuma mensagem ao modelo")
    tele = M.cenario("teleoperado")
    for acao, contagem in tele.items():
        enviados = {k: v for k, v in contagem.items() if k != "aceita"}
        check(f"teleoperado · {acao}: 0 mensagens ao modelo", not enviados)
    print("Autônomo/assistido: comportamento de sempre (sem regressão)")
    auto = M.cenario("autonomo")
    check("autônomo · frase do operador = 1 resposta do modelo (como antes)",
          auto["frase digitada pelo operador (say)"].get("response.create") == 1)
    check("autônomo · voz ao vivo do operador não gera pedido de resposta", not auto["voz ao vivo do operador (25 blocos de 40 ms)"])

    print("Instrução dada em teleoperado chega ao modelo na volta")
    h = T.Harness()
    a = h.a
    a.set_operator("Ana")
    a.ask_text("aquecimento")
    h.idle(8)
    a.set_mode("teleoperado")
    time.sleep(0.4)
    n0 = len(h.sent())
    a.operator_instruction("Chame o visitante de senhor Paulo.")
    a.memory_note("o apelido antigo foi revogado")
    time.sleep(0.4)
    check("em teleoperado a instrução não vai ao modelo", len(h.sent()) == n0)
    a.set_mode("autonomo")
    time.sleep(0.6)
    h.idle(6)
    depois = texts(h.sent()[n0:])
    check("na volta, a instrução chega ao modelo (nada se perdeu)", any("senhor Paulo" in t for t in depois))
    check("na volta, a nota de memória também chega", any("apelido antigo" in t for t in depois))
    h.close()
    ok = sum(1 for _, c in RESULTS if c)
    print(f"\n{ok}/{len(RESULTS)} verificações OK (servidor Realtime falso; sem API paga)")
    return ok == len(RESULTS)


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
