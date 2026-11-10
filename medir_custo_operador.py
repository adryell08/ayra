"""Quantas mensagens ao MODELO (Realtime/Gemini) cada ação do operador gera, por modo (servidor Realtime FALSO, sem API paga).
Conta o que o app envia ao servidor depois de cada ação: `response.create` (resposta paga), itens de conversa (entrada que vira token), áudio.
Uso: python medir_custo_operador.py"""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
import test_realtime as T

AGORA = "agora"


def contar(h, depois_de):
    ev = h.sent()[depois_de:]
    c = {}
    for e in ev:
        k = e["type"]
        if k == "conversation.item.create":
            k += ":" + e["item"].get("type", "?")
        c[k] = c.get(k, 0) + 1
    return c


def cenario(modo):
    h = T.Harness()
    a = h.a
    a.set_operator("Ana")
    h.a.ask_text("aquecimento"); h.idle(8)                  # conecta a sessão (como numa conversa já em curso)
    a.set_mode(modo)
    time.sleep(0.4)
    out = {}
    n0 = len(h.sent())
    a.operator_say("Olá, tudo bem?")
    h.wait(lambda: a.state in ("speaking", "idle"), 4); h.idle(8)
    out["frase digitada pelo operador (say)"] = contar(h, n0)
    n0 = len(h.sent())
    a.operator_instruction("Fale mais devagar.")
    time.sleep(0.4)
    out["instrução do operador"] = contar(h, n0)
    n0 = len(h.sent())
    loud = (np.sin(np.arange(960) / 5) * 8000).astype(np.int16)
    for _ in range(25):
        a.operator_audio(loud)
    time.sleep(0.5)
    out["voz ao vivo do operador (25 blocos de 40 ms)"] = contar(h, n0)
    n0 = len(h.sent())
    a.memory_note("o apelido foi revogado")
    time.sleep(0.3)
    out["nota de memória do painel"] = contar(h, n0)
    n0 = len(h.sent())
    r = a.ask_text("pergunta digitada no robô")
    time.sleep(0.5); h.idle(6)
    out["pergunta digitada pelo usuário no robô"] = dict(aceita=bool(r), **contar(h, n0))
    n0 = len(h.sent())
    a.press_voice()
    time.sleep(0.5)
    out["usuário aperta falar no robô"] = contar(h, n0)
    h.close()
    return out


if __name__ == "__main__":
    res = {m: cenario(m) for m in ("autonomo", "assistido", "teleoperado")}
    print(json.dumps(res, ensure_ascii=False, indent=1))
    Path(__file__).with_name("avaliacao").joinpath("custo_operador.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
