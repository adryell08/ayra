"""Visão sob demanda, fim de fala e memória de rostos (tudo simulado; sem câmera, microfone ou rede).

Uso: python test_visao.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ["NURIA_VISAO"] = "demanda"
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np

import ia
import realtime as rt
import rostos
import test_realtime as T
from assistente_realtime import build_session, vision_mode
from test_realtime import Harness, cfg, check, scene
from voz import MicStream


class CallServer(rt.FakeRealtimeServer):
    """Fala `olhar` / `lembrar_rosto` quando o texto pede; depois responde como o servidor simulado."""

    def _respond(self, request, cancel):
        q = self._pending_text.lower()
        done = any(e.get("item", {}).get("type") == "function_call_output" for e in self.received[-3:])
        call = None
        if not done:
            if "mão" in q:
                call = ("olhar", {})
            elif "lembre" in q:
                call = ("lembrar_rosto", {"nome": "João"})
            elif q.strip() == "sim":
                call = ("lembrar_rosto", {"nome": "João", "confirmado": True})
            elif "esqueça" in q:
                call = ("esquecer_rosto", {"nome": "João"})
        if call is None:
            return super()._respond(request, cancel)
        rid, cid = self._id("resp"), self._id("call")
        time.sleep(self.latency)
        self._emit({"type": "response.created", "response": {"id": rid, "status": "in_progress"}})
        args = json.dumps(call[1])
        self._emit({"type": "response.function_call_arguments.done", "response_id": rid, "item_id": cid,
                    "call_id": cid, "name": call[0], "arguments": args})
        self._emit({"type": "response.done", "response": {"id": rid, "status": "completed", "output": [
            {"id": cid, "type": "function_call", "name": call[0], "call_id": cid, "arguments": args}]}})


def outputs(h):
    return [json.loads(e["item"]["output"]) for e in h.sent("conversation.item.create")
            if e["item"].get("type") == "function_call_output"]


def images(h):
    return [e for e in h.sent("conversation.item.create")
            if any(p.get("type") == "input_image" for p in e["item"].get("content", []))]


def test_fim_de_fala():
    print("Fim de fala tolerante")
    os.environ.pop("NURIA_FIM_FALA", None)
    check("padrão 1,5 s (antes 0,8 s: pausa para pensar cortava a mensagem)", MicStream().silence_after == 1.5)
    os.environ["NURIA_FIM_FALA"] = "2,2"
    check("NURIA_FIM_FALA ajusta (aceita vírgula)", MicStream().silence_after == 2.2)
    os.environ["NURIA_FIM_FALA"] = "0.1"
    check("mínimo 0,6 s", MicStream().silence_after == 0.6)
    os.environ["NURIA_FIM_FALA"] = "abc"
    check("valor inválido volta ao padrão", MicStream().silence_after == 1.5)
    os.environ.pop("NURIA_FIM_FALA")
    check("fala longa cabe (30 s)", MicStream().max_seconds == 30.0)


def test_prompt_e_sessao():
    print("Sessão e prompt")
    s = build_session(cfg())
    names = [t["name"] for t in s["tools"]]
    check("modo padrão é sob demanda", vision_mode() == "demanda")
    check("ferramentas: expressar, olhar, lembrar_rosto, esquecer_rosto",
          names == ["expressar", "olhar", "lembrar_rosto", "esquecer_rosto"])
    ins = s["instructions"]
    check("não descreve o ambiente sozinha", "Nunca descreva o ambiente" in ins and "só quando perguntarem" in ins)
    check("sem foto automática: só vê chamando olhar", "NÃO está vendo nada até chamar a função olhar" in ins
          and "a imagem enviada junto com a pergunta" not in ins)
    check("responde ao conjunto de falas em partes e usa mensagens anteriores", "responda ao conjunto" in ins)
    check("cadastro de rosto exige pergunta e sim", "SÓ DEPOIS do sim" in ins)
    os.environ["NURIA_VISAO"] = "sempre"
    names2 = [t["name"] for t in build_session(cfg())["tools"]]
    check("modo sempre: sem a ferramenta olhar e com foto por pergunta",
          "olhar" not in names2 and "imagem enviada junto" in build_session(cfg())["instructions"])
    os.environ["NURIA_VISAO"] = "demanda"
    check("legado (ia.PERSONA) mantém a foto por pergunta", "imagem enviada junto com a pergunta" in ia.PERSONA)


def test_visao_sob_demanda():
    print("Visão sob demanda")
    h = Harness(server_cls=CallServer)
    check("assistente usa o modo demanda", h.a.vision_mode == "demanda")
    h.a.ask_text("oi, tudo bem?")
    h.idle()
    check("pergunta comum: nenhuma foto enviada", not images(h) and "não recebi imagem" in h.a.snapshot().answer)
    check("nem aviso de visão pausada", not any("Visão pausada" in json.dumps(e) for e in h.sent("conversation.item.create")))
    h.a.ask_text("o que é isso na minha mão?")
    h.idle()
    types = [e["type"] for e in h.sent()]
    out = outputs(h)
    check("pergunta visual: IA chama olhar → resposta da ferramenta ok", out and out[-1].get("ok") is True)
    check("uma única foto, enviada depois da resposta da ferramenta e antes de nova resposta",
          len(images(h)) == 1 and len([t for t in types if t == "response.create"]) == 3)
    i_out = max(i for i, e in enumerate(h.sent()) if e["type"] == "conversation.item.create"
                and e["item"].get("type") == "function_call_output")
    i_img = next(i for i, e in enumerate(h.sent()) if e in images(h))
    check("ordem: saída da ferramenta → imagem → response.create", i_out < i_img < len(types) - 1
          and types[i_img + 1] == "response.create")
    check("resposta usa a foto", "cena" in h.a.snapshot().answer)
    imgs = [e["item"]["id"] for e in images(h)]
    check("foto apagada da sessão ao fim (não é recobrada)", [e["item_id"] for e in h.sent("conversation.item.delete")] == imgs)
    h.a.set_vision(False)
    h.a.ask_text("o que é isso na minha mão?")
    h.idle()
    check("visão pausada: ferramenta recusa e nenhuma foto nova", outputs(h)[-1].get("ok") is False and len(images(h)) == 1)
    h.close()
    h = Harness(server_cls=CallServer, still=lambda: None)
    h.a.ask_text("o que é isso na minha mão?")
    h.idle()
    check("câmera indisponível: ferramenta informa, sem travar", outputs(h) and outputs(h)[-1].get("ok") is False
          and h.a.state == "idle")
    h.close()


def test_voz_sem_foto():
    print("Voz sem foto automática")
    h = Harness()
    h.a.press_voice()
    h.idle()
    check("voz em modo demanda: commit e resposta sem imagem",
          not images(h) and h.sent("input_audio_buffer.commit") and h.a.snapshot().state == "idle")
    h.close()


# ---- rostos ----
def vec(seed, noise=0.0):
    r = np.random.default_rng(seed)
    v = r.normal(0, 1, 128).astype(np.float32)
    return v + (np.random.default_rng(seed + 99).normal(0, noise, 128).astype(np.float32) if noise else 0)


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def test_livro():
    print("Cadastro de rostos")
    d = Path(tempfile.mkdtemp())
    clock = Clock()
    book = rostos.FaceBook(d / "r.json", days=7, clock=clock)
    check("vazio no começo", len(book) == 0 and book.match(vec(1)) == (None, -1.0))
    info = book.add("  João  ", [vec(1), vec(1, 0.2)])
    check("cadastra com nome limpo e validade de 7 dias", info["nome"] == "João" and abs(info["expira"] - (clock.t + 7 * 86400)) < 1)
    raw = (d / "r.json").read_text(encoding="utf-8")
    check("grava só números (128 valores por amostra), sem imagem", len(json.loads(raw)["pessoas"][0]["amostras"][0]) == 128
          and "jpeg" not in raw.lower() and "base64" not in raw.lower())
    p, score = book.match(vec(1, 0.3))
    check("reconhece a mesma pessoa com ruído", p and p["nome"] == "João" and score > 0.5)
    p2, score2 = book.match(vec(2))
    check("não confunde outra pessoa", p2 is None and score2 < 0.40)
    clock.t += 5 * 86400
    book.match(vec(1))
    clock.t += 5 * 86400
    check("reconhecimento renova a validade (10 dias, mas visto aos 5)", book.purge() == 0 and len(book) == 1)
    clock.t += 8 * 86400
    check("sem aparecer por 7 dias: apagado sozinho", book.purge() == 1 and len(book) == 0)
    book.add("Ana", [vec(5)])
    book.add("ana", [vec(5, 0.1)])
    check("mesmo nome (sem acento/caixa) não duplica e acumula amostras", len(book) == 1 and book.people()[0]["amostras"] == 2)
    again = rostos.FaceBook(d / "r.json", days=7, clock=clock)
    check("persiste no disco", len(again) == 1 and again.people()[0]["nome"] == "Ana")
    check("esquecer apaga na hora e persiste", again.forget("ANA") == 1 and len(rostos.FaceBook(d / "r.json", clock=clock)) == 0)
    book.add("Zé", [vec(7)])
    check("esquecer todos", book.forget_all() >= 1 and len(book) == 0)
    forever = rostos.FaceBook(d / "f.json", days=0, clock=clock)
    forever.add("Eu", [vec(8)])
    clock.t += 3650 * 86400
    check("validade 0 = não expira", forever.purge() == 0)


def test_reconhecedor():
    print("Reconhecedor e confirmação")
    d = Path(tempfile.mkdtemp())
    who = {"v": [vec(1)]}
    calls = {"n": 0}

    def embed(frame):
        calls["n"] += 1
        return list(who["v"])
    seen = []
    book = rostos.FaceBook(d / "r.json", days=7)
    rec = rostos.FaceRecognizer(book, lambda: scene(), embedder=embed, on_change=seen.append)
    check("sem cadastro, step() ainda funciona e não anuncia ninguém", rec.step() is None and seen == [])
    r1 = rec.ask("João", "t1")
    check("passo 1 não grava nada e manda a IA perguntar", r1.get("pendente") and len(book) == 0 and "autoriza" in r1["instrucao"])
    r2 = rec.confirm("João", "t1")
    check("confirmar no mesmo turno é recusado (a pessoa ainda não respondeu)", not r2["ok"] and len(book) == 0)
    r3 = rec.confirm("Maria", "t2")
    check("confirmar outro nome reinicia o pedido", r3.get("pendente") and len(book) == 0)
    rec.ask("João", "t3")
    who["v"] = [vec(1), vec(2)]
    r4 = rec.confirm("João", "t4")
    check("dois rostos na câmera: recusa", not r4["ok"] and "mais de um" in r4["motivo"] and len(book) == 0)
    who["v"] = []
    rec.ask("João", "t5")
    r5 = rec.confirm("João", "t6")
    check("sem rosto nítido: recusa", not r5["ok"] and len(book) == 0)
    who["v"] = [vec(1)]
    rec.ask("João", "t7")
    r6 = rec.confirm("João", "t8")
    check("passo 2 em outro turno grava", r6["ok"] and r6["nome"] == "João" and r6["validade_dias"] == 7 and len(book) == 1)
    check("cadastrado já aparece como presente e avisa a IA", rec.current == "João" and seen == ["João"])
    # outra pessoa aparece
    who["v"] = [vec(9)]
    rec.current, rec._cand, rec._streak = None, None, 0
    rec.step()
    check("rosto desconhecido: marca como desconhecido, não inventa nome", rec.current is None and rec.seen_unknown)
    who["v"] = [vec(1, 0.2)]
    rec.step()
    check("histerese: precisa de 2 leituras iguais para trocar", rec.current is None)
    rec.step()
    check("reconhece e avisa uma vez só", rec.current == "João" and seen == ["João", "João"][:len(seen)] and seen.count("João") == 2)
    n = len(seen)
    rec.step(); rec.step()
    check("continua reconhecido sem repetir aviso", len(seen) == n)
    who["v"] = []
    rec._last_face -= 60
    rec.step()
    check("some da câmera: limpa a identidade depois de um tempo", rec.current is None)
    check("esquecer pelo reconhecedor", rec.forget("joão")["ok"] and len(book) == 0)
    # custo zero com lista vazia
    calls["n"] = 0
    rec.PERIOD = 0.02
    rec.start()
    time.sleep(0.2)
    rec.stop()
    check("lista vazia: a thread não processa nenhum quadro", calls["n"] == 0)


def test_ferramentas_de_rosto():
    print("Ferramentas de rosto na conversa")
    d = Path(tempfile.mkdtemp())
    who = {"v": [vec(1)]}
    book = rostos.FaceBook(d / "r.json", days=7)
    h = Harness(server_cls=CallServer)
    h.a.faces = rostos.FaceRecognizer(book, lambda: scene(), embedder=lambda f: list(who["v"]))
    h.a.ask_text("lembre de mim")
    h.idle()
    o = outputs(h)[-1]
    check("1ª chamada: nada gravado, IA manda perguntar", o.get("pendente") and len(book) == 0)
    h.a.ask_text("sim")
    h.idle()
    o = outputs(h)[-1]
    check("depois do 'sim' (outro turno): rosto guardado", o.get("ok") is True and len(book) == 1 and o["validade_dias"] == 7)
    h.a.identity_note("João")
    h.wait(lambda: any("reconheci João" in json.dumps(e, ensure_ascii=False) for e in h.sent("conversation.item.create")))
    notes = [e for e in h.sent("conversation.item.create") if "reconheci João" in json.dumps(e, ensure_ascii=False)]
    check("nota de identidade: só texto, sem pedir resposta", len(notes) == 1 and not images(h))
    h.a.ask_text("esqueça meu rosto")
    h.idle()
    check("esquecer_rosto apaga", outputs(h)[-1].get("ok") is True and len(book) == 0)
    h.a.faces = None
    h.a.ask_text("lembre de mim")
    h.idle()
    check("sem o recurso: informa indisponível e segue", outputs(h)[-1].get("ok") is False and h.a.state == "idle")
    h.close()


if __name__ == "__main__":
    for fn in (test_fim_de_fala, test_prompt_e_sessao, test_visao_sob_demanda, test_voz_sem_foto, test_livro,
               test_reconhecedor, test_ferramentas_de_rosto):
        fn()
    bad = [n for n, ok in T.RESULTS if not ok]
    print(f"\n{len(T.RESULTS) - len(bad)}/{len(T.RESULTS)} verificações OK (simuladas)")
    sys.exit(1 if bad else 0)
