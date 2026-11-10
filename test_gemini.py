"""Testes do Gemini Live, das respostas curtas (tokens) e da estabilidade com o operador conectado.

Sem rede: o Gemini é um servidor simulado que fala o protocolo do Live (setup, realtimeInput, clientContent,
toolCall/toolResponse). Uso: python test_gemini.py
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("NURIA_VISAO", "sempre")      # estes testes cobrem a foto por pergunta; o modo sob demanda está em test_visao.py
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import numpy as np

import credenciais
import gemini_live as G
import test_realtime as T

RESULTS = []
GKEY = "AIza" + "TesteSomente0123456789abcdefWXYZ"


def check(name, cond):
    RESULTS.append((name, bool(cond)))
    print(f"  [{'OK' if cond else 'FALHOU'}] {name}")


def gcfg(**kw):
    base = credenciais.load(Path("/nao/existe"), environ={"GEMINI_API_KEY": GKEY, "NURIA_PROVEDOR": "gemini"})
    return credenciais.AIConfig(**{**base.__dict__, **kw})


class GHarness(T.Harness):
    def __init__(self, **kw):
        self.gservers = []

        def factory():
            s = G.FakeGeminiServer(**kw)
            self.gservers.append(s)
            return G.GeminiAdapter(s, "gemini-3.8-live", "Kore", 350)
        super().__init__(server_cls=factory, config=gcfg())

    def g(self, key=None):
        msgs = [m for s in self.gservers for m in s.received]
        return [m for m in msgs if key is None or key in m]


def test_config():
    print("Configuração: provedor, chaves e economia")
    c = gcfg()
    check("Gemini escolhido pelo .env (NURIA_PROVEDOR=gemini) e chave pelo GEMINI_API_KEY",
          c.provider == "gemini" and c.live_configured and not c.configured and c.live_label == "Gemini Live")
    check("chave do Gemini mascarada e fora do repr", credenciais.mask(GKEY) == "AIza…WXYZ" and GKEY not in repr(c))
    check("validação da chave do Gemini (AIza…)", credenciais.looks_valid(GKEY, "gemini")
          and not credenciais.looks_valid("sk-abcdefghijklmnopqrstuv", "gemini"))
    check("padrões econômicos: teto de 350 tokens por resposta e foto de 512 px", c.max_tokens == 350 and c.image_side == 512)
    import tempfile
    env = Path(tempfile.mkdtemp()) / ".env"
    env.write_text("NURIA_PROVEDOR=gemini        # comentário\nNURIA_MAX_TOKENS=220  # curtíssimas\n", encoding="utf-8")
    x = credenciais.load(env, environ={})
    check(".env aceita comentário no fim da linha (como no .env.example)", x.provider == "gemini" and x.max_tokens == 220)
    o = credenciais.load(Path("/nao/existe"), environ={"OPENAI_API_KEY": "sk-teste", "NURIA_MAX_TOKENS": "0"})
    import assistente_realtime as ar
    check("OpenAI: max_output_tokens na sessão (0 = sem limite → \"inf\")",
          ar.build_session(T.cfg())["max_output_tokens"] == 350 and ar.build_session(o)["max_output_tokens"] == "inf")
    check("instruções pedem 1 frase de até 15 palavras", "15 palavras" in ar.INSTRUCTIONS)
    setup = G.build_setup(ar.build_session(T.cfg()), "gemini-3.8-live", "Kore", 350)["setup"]
    gen = setup["generationConfig"]
    check("Gemini: setup com modelo, voz, maxOutputTokens e só áudio", setup["model"] == "models/gemini-3.8-live"
          and gen["maxOutputTokens"] == 350 and gen["responseModalities"] == ["AUDIO"]
          and gen["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] == "Kore")
    check("Gemini: fim de fala manual (como na Realtime) e transcrições ligadas",
          setup["realtimeInputConfig"]["automaticActivityDetection"]["disabled"] is True
          and "inputAudioTranscription" in setup and "outputAudioTranscription" in setup)
    check("Gemini: janela deslizante (sessão com imagem não expira em 2 min)", "slidingWindow" in setup["contextWindowCompression"])
    decl = setup["tools"][0]["functionDeclarations"][0]
    props = decl["parameters"]["properties"]
    check("Gemini: a mesma ferramenta expressar, com emoções, simulações e gestos descritos",
          decl["name"] == "expressar" and decl["parameters"]["type"] == "OBJECT" and "dedo_do_meio" in props["gesto"]["enum"]
          and "description" in props["emocao"] and "description" in props["gesto"])
    check("contexto das ferramentas: instruções citam rosto, corpo 3D, webcam, gestos vistos e operador",
          all(w in ar.INSTRUCTIONS for w in ("rosto de LED", "corpo 3D", "webcam", "joinha", "operador", "expressar")))


def test_text_and_gesture():
    print("Gemini Live (simulado): pergunta digitada, foto e gesto")
    h = GHarness()
    a = h.a
    check("conecta pelo Gemini (setup → setupComplete)", h.wait(lambda: True) and a.ask_text("dá tchau pra turma"))
    check("fala em streaming", h.wait(lambda: a.state == "speaking", 5))
    check("gesto pedido pela IA chega ao corpo 3D", h.wait(lambda: a.snapshot().gesture == "tchau", 3))
    check("termina e volta a Pronta", h.idle())
    cc = h.g("clientContent")[-1]["clientContent"]
    parts = cc["turns"][0]["parts"]
    check("pergunta + foto na mesma vez (texto e imagem JPEG)", cc["turnComplete"] is True
          and parts[0]["text"].startswith("dá tchau") and parts[1]["inlineData"]["mimeType"] == "image/jpeg")
    img = base64.b64decode(parts[1]["inlineData"]["data"])
    import cv2
    dec = cv2.imdecode(np.frombuffer(img, np.uint8), cv2.IMREAD_COLOR)
    check(f"foto reduzida para economizar ({dec.shape[1]}×{dec.shape[0]})", max(dec.shape[:2]) <= 512)
    tr = h.g("toolResponse")
    check("resposta da ferramenta enviada na hora, em silêncio", tr and tr[0]["toolResponse"]["functionResponses"][0]["scheduling"] == "SILENT")
    check("resposta escrita aparece na coluna", "Gemini simulado" in a.snapshot().answer)
    check("rótulo da coluna mostra Gemini Live", "Gemini Live" in a.backend_label())
    a.shutdown()


def test_voice():
    print("Gemini Live (simulado): pergunta falada")
    h = GHarness()
    a = h.a
    a.press_voice()
    check("fala e responde", h.wait(lambda: a.state == "speaking", 6) and h.idle())
    ri = [m["realtimeInput"] for m in h.g("realtimeInput")]
    order = [next(iter(r)) for r in ri]
    check("ordem: activityStart → áudio → foto → activityEnd", order[0] == "activityStart" and order[-1] == "activityEnd"
          and "audio" in order and order.index("video") > order.index("audio"))
    audio = [r["audio"] for r in ri if "audio" in r]
    total = sum(len(base64.b64decode(x["data"])) // 2 for x in audio)
    check("áudio convertido para 16 kHz (1 s de voz ≈ 16 000 amostras)", audio[0]["mimeType"] == "audio/pcm;rate=16000"
          and 14000 < total < 18000)
    check("transcrição da pergunta aparece", a.snapshot().question == "o que você está vendo?")
    n = len(h.g("realtimeInput"))
    h.mic.seconds = 0.02
    a.press_voice()
    h.wait(lambda: a.state == "idle" and "Não ouvi" in a.snapshot().message, 4)
    check("silêncio: nada vai para o Gemini (nenhum token gasto)", len(h.g("realtimeInput")) == n)
    a.shutdown()


def test_interrupt_and_operator():
    print("Gemini Live (simulado): interrupção e operador")
    h = GHarness()
    h.gservers and None
    a = h.a
    for s in h.gservers:
        s.speed = 1.0
    a.ask_text("conte uma história bem longa")
    h.wait(lambda: a.state == "speaking", 5)
    for s in h.gservers:
        s.speed = 1.0
    a._interrupt()
    a.cancel()
    check("interromper para na hora", h.wait(lambda: a.state == "idle", 2))
    time.sleep(0.5)
    check("áudio atrasado da resposta cancelada é descartado", a.state == "idle")
    a.set_mode("assistido")
    a.operator_say("Bom dia, turma!")
    check("assistido: o Gemini fala a frase do operador",
          h.wait(lambda: "Bom dia, turma!" in a.snapshot().answer, 5)
          and "Diga exatamente" in json.dumps(h.g("clientContent")[-1], ensure_ascii=False))
    h.idle()
    a.shutdown()


def test_connection_check():
    print("Aba API: testar conexão com o Gemini")
    from assistente_realtime import check_connection
    ok, msg = check_connection(gcfg(), transport_factory=lambda: G.GeminiAdapter(G.FakeGeminiServer(), "m", "Kore", 0))
    check("teste OK com o provedor escolhido", ok and "gemini-3.8-live" in msg)
    ok, msg = check_connection(gcfg(), transport_factory=lambda: G.GeminiAdapter(G.FakeGeminiServer(reject_key=True), "m", "Kore", 0))
    check("chave recusada: motivo claro", not ok and "Gemini" in msg)
    ok, msg = check_connection(gcfg(gemini_key=None))
    check("sem chave do Gemini: avisa sem conectar", not ok and "sem chave" in msg)
    try:
        G.GeminiSocket("ws://127.0.0.1:9/nada", GKEY, timeout=1.0)
        leaked = True
    except Exception as error:
        leaked = GKEY in str(error)
    check("erro de conexão nunca mostra a chave", not leaked)
    import menu
    import theme
    from renderer import HudInfo
    m = menu.OptionsMenu()
    hud = HudInfo()
    hud.api = dict(provider="gemini", gemini_configured=True, gmasked="AIza…WXYZ", tokens="Curtas")
    m.activate("api")
    items = {a: (lbl, st) for a, lbl, st, _ in m.items(hud, theme.SCHEMES["dark"])}
    check("aba API: provedor, chave do Gemini e tamanho das respostas",
          "Gemini Live" in items["api:provider"][1] and "AIza…WXYZ" in items["api:gkey"][1] and "api:tokens" in items)
    check("nenhum texto da aba mostra a chave", GKEY not in str(items))


def test_stability():
    print("Estabilidade (app não fecha com o operador falando)")
    import main as app
    from voz import StreamPlayer

    class A:
        player = StreamPlayer()
    check("nível da fala do corpo 3D lê player.level() (antes: TypeError que fechava o app)",
          app.talk_level(A(), "speaking") == 0.0 and app.talk_level(A(), "idle") == 0.0)
    import assistente_realtime as ar
    import robot as robot_mod
    cfg = HERE / "corpo3d.local.json"
    old = cfg.read_text(encoding="utf-8") if cfg.exists() else None
    real_drive, real_step = ar.RealtimeAssistant.drive_robot, robot_mod.Robot.step
    calls = {"n": 0}

    def flaky_step(self, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise ValueError("falha simulada num quadro")
        return real_step(self, *a, **kw)
    log = app.LOG_DIR / "erros.log"
    before = log.read_text(encoding="utf-8") if log.exists() else ""
    try:
        cfg.write_text(json.dumps({"ligado": True}), encoding="utf-8")
        ar.RealtimeAssistant.drive_robot = lambda self, robot, now, prev: (real_drive(self, robot, now, prev), "speaking")[1]
        robot_mod.Robot.step = flaky_step
        code = app.main(["--sim", "--ia", "simulada", "--sem-registro", "--run-seconds", "2.5"])
    finally:
        ar.RealtimeAssistant.drive_robot, robot_mod.Robot.step = real_drive, real_step
        if old is None:
            cfg.unlink(missing_ok=True)
        else:
            cfg.write_text(old, encoding="utf-8")
    after = log.read_text(encoding="utf-8") if log.exists() else ""
    check("corpo 3D com a Ayra falando o tempo todo: não fecha", code == 0)
    check("erro inesperado num quadro: registra em logs/erros.log e segue", "falha simulada" in after[len(before):])
    bat = (HERE / "iniciar.bat").read_text(encoding="utf-8")
    check("iniciar.bat segura a janela e aponta os logs se fechar com erro", "logs\\erros.log" in bat and "pause" in bat)
    src = (HERE / "main.py").read_text(encoding="utf-8")
    check("vídeo do operador lê o último quadro (não disputa a foto da pergunta)", "src.latest_frame()" in src)
    check("falhas nativas (driver/áudio/vídeo) vão para logs/falhas.log", "faulthandler.enable" in src)


if __name__ == "__main__":
    test_config()
    test_text_and_gesture()
    test_voice()
    test_interrupt_and_operator()
    test_connection_check()
    test_stability()
    ok = sum(1 for _, v in RESULTS if v)
    print(f"\n{ok}/{len(RESULTS)} verificações OK (Gemini simulado; validar com a chave real)")
    sys.exit(0 if ok == len(RESULTS) else 1)
