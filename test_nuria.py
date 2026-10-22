"""Testes da Nuria (Projeto 1): pipeline pergunta→imagem→IA→voz, falhas e interface.

Sem rede, sem webcam e sem áudio real: gravador, alto-falante e IA são substituídos por dublês.
Uso:  python test_nuria.py            (gera previews/nuria_*.png)
      python test_nuria.py --no-png
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np

import credenciais
import ia
from assistente import Assistant
from voz import VoiceError, envelope, to_wav

RESULTS = []


def check(name, cond):
    RESULTS.append((name, bool(cond)))
    print(f"  [{'OK' if cond else 'FALHOU'}] {name}")


def scene(color=(40, 120, 200)):
    img = np.zeros((480, 640, 3), np.uint8)
    img[:] = color
    img[300:420, 80:300] = (30, 30, 30)            # "notebook"
    img[200:420, 420:470] = (200, 80, 20)          # "garrafa"
    return img


class FakeRecorder:
    def __init__(self, wav=b"RIFFfake", delay=0.05, error=None):
        self.wav, self.delay, self.error, self.level = wav, delay, error, 0.0

    def record(self, stop, on_level=None):
        if self.error:
            raise self.error
        stop.wait(self.delay)
        return self.wav


class FakePlayer:
    def __init__(self, seconds=0.2):
        self.seconds, self.played = seconds, []

    def level(self):
        return 0.5

    def play(self, samples, rate, stop):
        self.played.append(len(samples))
        stop.wait(self.seconds)


class FakeBackend:
    name = "Fake"
    simulated = False

    def __init__(self, fail=None, text="O que está sobre a mesa?", tts_fail=False, delay=0.0):
        self.fail, self.text, self.tts_fail, self.calls, self.delay = fail, text, tts_fail, [], delay

    def transcribe(self, wav):
        self.calls.append("stt")
        if self.fail == "stt":
            raise ia.AIError("network", "Sem conexão com a OpenAI. Verifique a internet.")
        return self.text

    def answer(self, jpeg, question, history=()):
        self.calls.append("llm")
        time.sleep(self.delay)
        assert jpeg[:2] == b"\xff\xd8"
        if self.fail == "llm":
            raise ia.AIError("network", "Sem conexão com a OpenAI. Verifique a internet.")
        if self.fail == "auth":
            raise ia.AIError("auth", "Chave da OpenAI inválida ou revogada. Confira o arquivo .env.")
        return ia.Answer("Vejo um notebook e uma garrafa sobre a mesa.", "happy", "fake")

    def speak(self, text, emotion="neutral"):
        self.calls.append("tts")
        if self.tts_fail:
            raise ia.AIError("network", "Sem conexão com a OpenAI.")
        return np.zeros(2400, np.int16), 24000


def wait_idle(a, timeout=3.0):
    t = time.perf_counter()
    while time.perf_counter() - t < timeout:
        if a.state in ("idle", "error") and not (a._thread and a._thread.is_alive()):
            return True
        time.sleep(0.01)
    return False


def make(backend=None, recorder=None, player=None, still=scene, offline=lambda t: True):
    return Assistant(lambda: still() if still else None, lambda: backend or FakeBackend(),
                     recorder or FakeRecorder(), player or FakePlayer(), offline_tts=offline, log=False)


def test_config():
    print("Credenciais (RNF01)")
    with tempfile.TemporaryDirectory() as d:
        env = Path(d) / ".env"
        env.write_text('# comentário\nOPENAI_API_KEY="sk-teste-123"\nNURIA_MODELO_VISAO=gpt-x\n', encoding="utf-8")
        cfg = credenciais.load(env, environ={})
        check(".env é lido (chave e modelo)", cfg.api_key == "sk-teste-123" and cfg.vision_model == "gpt-x")
        check("repr nunca expõe a chave", "sk-teste" not in repr(cfg) and "definida" in repr(cfg))
        cfg2 = credenciais.load(env, environ={"OPENAI_API_KEY": "sk-do-sistema"})
        check("variável de ambiente prevalece sobre .env", cfg2.api_key == "sk-do-sistema")
        check("sem .env: chave ausente e modelos padrão", not credenciais.load(Path(d) / "x", environ={}).configured)
    src = "".join(p.read_text(encoding="utf-8") for p in Path(__file__).parent.glob("*.py") if p.name != "test_nuria.py")
    check("nenhuma chave 'sk-' no código-fonte", "sk-proj" not in src and 'api_key="sk' not in src)
    gi = Path(__file__).parent / ".gitignore"
    check(".env está no .gitignore", gi.exists() and ".env" in gi.read_text(encoding="utf-8"))
    try:
        ia.OpenAIBackend(credenciais.AIConfig(None, "a", "b", "c", "d", 10))
        check("sem chave: erro claro ao criar backend", False)
    except ia.AIError as e:
        check("sem chave: erro claro ao criar backend", e.kind == "auth" and ".env" in str(e))


def test_parsing():
    print("Resposta da IA")
    a = ia.parse_answer('{"resposta": "Vejo uma garrafa.", "emocao": "happy"}')
    check("JSON → texto e emoção", a.text == "Vejo uma garrafa." and a.emotion == "happy")
    b = ia.parse_answer("Vejo uma garrafa azul.")
    check("texto puro aceito (emoção neutra)", b.text == "Vejo uma garrafa azul." and b.emotion == "neutral")
    d = ia.parse_answer('{"resposta": "Buá!", "emocao": "sad", "acao": "chorar"}')
    check("ação 'chorar' vira simulação cry", d.action == "cry")
    check("ação desconhecida é ignorada", ia.parse_answer('{"resposta": "x", "acao": "voar"}').action == "")
    check("prompt informa as simulações disponíveis", "chorar" in ia.SYSTEM_PROMPT and "NÃO diga que não consegue" in ia.SYSTEM_PROMPT)
    c = ia.parse_answer('{"resposta": "Oi", "emocao": "furioso"}')
    check("emoção desconhecida vira neutra", c.emotion == "neutral")
    jpg = ia.encode_jpeg(np.zeros((1080, 1920, 3), np.uint8))
    import cv2
    dec = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
    check("imagem reduzida a ≤1024 px antes do envio", max(dec.shape[:2]) <= 1024)
    env = envelope(np.r_[np.zeros(2400), 8000 * np.ones(2400)].astype(np.int16), 24000)
    check("envelope de fala 0..1 (boca)", env.min() == 0 and abs(env.max() - 1) < 1e-6)
    check("WAV de 16 bits gerado", to_wav(np.zeros(160, np.int16), 16000)[:4] == b"RIFF")
    try:
        import openai, httpx
        req = httpx.Request("POST", "https://api.openai.com/v1/x")
        cases = [(openai.APIConnectionError(request=req), "network"),
                 (openai.AuthenticationError("x", response=httpx.Response(401, request=req), body=None), "auth"),
                 (openai.RateLimitError("insufficient_quota", response=httpx.Response(429, request=req), body=None), "quota"),
                 (openai.APITimeoutError(request=req), "timeout")]
        check("erros da API mapeados (rede, chave, cota, tempo)", all(ia._map_error(e).kind == k for e, k in cases))
    except ImportError:
        check("erros da API mapeados (openai ausente, pulado)", True)


def test_openai_requests():
    print("Chamadas reais do SDK OpenAI contra servidor simulado (sem rede)")
    try:
        import httpx
        import openai
    except ImportError:
        check("SDK openai ausente (pulado)", True)
        return
    import json as _json
    seen = []

    def handler(request):
        seen.append(request)
        path = request.url.path
        if path.endswith("/audio/transcriptions"):
            return httpx.Response(200, json={"text": "O que tem na mesa?"})
        if path.endswith("/chat/completions"):
            body = _json.loads(request.content)
            if body["model"] == "modelo-inexistente":
                return httpx.Response(404, json={"error": {"message": "model not found", "type": "invalid_request_error",
                                                            "code": "model_not_found"}})
            content = '{"resposta": "Vejo uma garrafa azul.", "emocao": "happy"}'
            return httpx.Response(200, json={"id": "x", "object": "chat.completion", "created": 0, "model": body["model"],
                                             "choices": [{"index": 0, "finish_reason": "stop",
                                                          "message": {"role": "assistant", "content": content}}]})
        if path.endswith("/audio/speech"):
            return httpx.Response(200, content=np.zeros(4800, np.int16).tobytes(),
                                  headers={"content-type": "application/octet-stream"})
        return httpx.Response(500)

    cfg = credenciais.AIConfig("sk-teste", "modelo-inexistente", "gpt-4o-mini-transcribe", "gpt-4o-mini-tts", "coral", 10)
    be = ia.OpenAIBackend(cfg)
    be.client = openai.OpenAI(api_key="sk-teste", max_retries=0,
                              http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    text = be.transcribe(to_wav(np.zeros(1600, np.int16), 16000))
    check("STT: multipart em /audio/transcriptions com idioma pt", text == "O que tem na mesa?"
          and b'name="language"' in seen[-1].content and b"pt" in seen[-1].content)
    ans = be.answer(ia.encode_jpeg(scene()), "O que tem na mesa?", [("oi", "olá")])
    bodies = [_json.loads(r.content) for r in seen if r.url.path.endswith("/chat/completions")]
    last = bodies[-1]
    img = last["messages"][-1]["content"][1]
    check("LLM: modelo ausente → tenta o próximo da lista", [b["model"] for b in bodies] == ["modelo-inexistente", "gpt-4.1-mini"])
    check("LLM: imagem JPEG em base64 + pergunta + histórico + JSON",
          img["image_url"]["url"].startswith("data:image/jpeg;base64,") and last["messages"][0]["role"] == "system"
          and len(last["messages"]) == 4 and last["response_format"] == {"type": "json_object"})
    check("LLM: resposta e emoção interpretadas", ans.text == "Vejo uma garrafa azul." and ans.emotion == "happy")
    samples, rate = be.speak("Vejo uma garrafa azul.", "angry")
    sp = _json.loads(seen[-1].content)
    check("TTS: PCM 24 kHz, voz e entonação da emoção", rate == 24000 and len(samples) == 4800
          and sp["response_format"] == "pcm" and sp["voice"] == "coral" and "bravo" in sp["instructions"])

    def down(request):
        raise httpx.ConnectError("sem rede", request=request)
    be.client = openai.OpenAI(api_key="sk-teste", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(down)))
    try:
        be.answer(ia.encode_jpeg(scene()), "oi")
        check("sem rede: AIError de conexão", False)
    except ia.AIError as e:
        check("sem rede: AIError de conexão", e.kind == "network")

    def unauthorized(request):
        return httpx.Response(401, json={"error": {"message": "Incorrect API key", "type": "invalid_request_error"}})
    be.client = openai.OpenAI(api_key="sk-teste", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(unauthorized)))
    try:
        be.transcribe(b"x")
        check("chave inválida: AIError de autenticação", False)
    except ia.AIError as e:
        check("chave inválida: AIError de autenticação", e.kind == "auth")


def test_audio_devices():
    print("Microfone/alto-falante com PortAudio simulado (RF02)")
    import voz

    class FakeStream:
        def __init__(self, samplerate, channels, dtype, blocksize, callback, pattern):
            self.cb, self.block, self.rate, self.pattern = callback, blocksize, samplerate, pattern
            self.run = False

        def __enter__(self):
            self.run = True
            def feed():
                t = 0.0
                while self.run:
                    amp = self.pattern(t)
                    noise = np.random.default_rng(int(t * 1000)).normal(0, 1, self.block)
                    data = (noise * amp * 32767).clip(-32768, 32767).astype(np.int16).reshape(-1, 1)
                    self.cb(data, self.block, None, None)
                    t += self.block / self.rate
                    time.sleep(self.block / self.rate / 4)       # 4× tempo real
            self.th = threading.Thread(target=feed, daemon=True)
            self.th.start()
            return self

        def __exit__(self, *a):
            self.run = False
            self.th.join()

    class FakeSD:
        def __init__(self, pattern):
            self.pattern, self.played, self.stopped = pattern, None, False

        def InputStream(self, **kw):
            return FakeStream(pattern=self.pattern, **kw)

        def play(self, data, rate):
            self.played = (len(data), rate)

        def stop(self):
            self.stopped = True

    orig = voz._sd
    try:
        speech = lambda t: 0.08 if 0.8 < t < 2.0 else 0.002
        voz._sd = lambda: FakeSD(speech)
        rec = voz.Recorder(silence_after=0.5)
        t0 = time.perf_counter()
        wav = rec.record(threading.Event())
        check("fala detectada e gravação para sozinha no silêncio", wav is not None and wav[:4] == b"RIFF"
              and rec.heard_speech and time.perf_counter() - t0 < 3)
        voz._sd = lambda: FakeSD(lambda t: 0.15 if 0.8 < t < 2.0 else 0.03)     # ventilador alto
        rec = voz.Recorder(silence_after=0.5, max_seconds=6)
        t0 = time.perf_counter()
        wav = rec.record(threading.Event())
        check("sala ruidosa: fim da fala ainda é detectado", wav is not None and time.perf_counter() - t0 < 4)
        voz._sd = lambda: FakeSD(lambda t: 0.002)
        rec = voz.Recorder(no_speech_timeout=0.8)
        check("só ruído: devolve None (sem chamar a API)", rec.record(threading.Event()) is None)
        stop = threading.Event()
        threading.Timer(0.8, stop.set).start()
        check("parada manual envia mesmo com voz baixa", voz.Recorder(no_speech_timeout=5).record(stop) is not None)

        def broken():
            class SD:
                def InputStream(self, **kw):
                    raise OSError("Error querying device -1")
            return SD()
        voz._sd = broken
        try:
            voz.Recorder().record(threading.Event())
            check("sem microfone: VoiceError", False)
        except VoiceError as e:
            check("sem microfone: VoiceError", "Microfone" in str(e))
        fake = FakeSD(speech)
        voz._sd = lambda: fake
        pl = voz.Player()
        stop = threading.Event()
        levels = []
        th = threading.Thread(target=pl.play, args=(np.r_[np.zeros(4800), 9000 * np.ones(9600)].astype(np.int16), 24000, stop))
        th.start()
        for _ in range(30):
            levels.append(pl.level())
            time.sleep(0.02)
        th.join()
        check("alto-falante toca e boca segue o volume", fake.played == (14400, 24000) and max(levels) > 0.9 and fake.stopped)
        stop = threading.Event()
        th = threading.Thread(target=pl.play, args=(np.zeros(240000, np.int16), 24000, stop))
        th.start(); time.sleep(0.1); t = time.perf_counter(); stop.set(); th.join()
        check("interromper a fala para o áudio em < 100 ms", time.perf_counter() - t < 0.1)
    finally:
        voz._sd = orig


def test_camera_still():
    print("Foto sob pedido na câmera (RF01)")
    import tracker

    class Cap:
        def __init__(self):
            self.n = 0

        def read(self):
            self.n += 1
            time.sleep(0.01)
            img = scene()
            img[0, 0] = (self.n % 255, 0, 0)
            return True, img

        def release(self):
            pass

    class Det:
        name = "falso"

        def detect(self, frame):
            return []

    src = tracker.CameraSource(Det(), 0)
    src._open = lambda: Cap()
    src.start()
    time.sleep(0.15)
    img = src.capture_still()
    check("capture_still devolve cópia do quadro atual", img is not None and img.shape == (480, 640, 3))
    check("nenhum quadro fica retido após a foto", src._still is None)
    src.set_paused(True)
    time.sleep(0.05)
    check("câmera pausada: sem foto (privacidade)", src.capture_still(timeout=0.2) is None)
    src.stop()
    check("câmera encerrada: sem foto e sem travar", src.capture_still(timeout=0.2) is None)


def test_flows():
    print("Fluxos (RF01–RF04, RNF02, RNF03)")
    fb, pl = FakeBackend(delay=0.05), FakePlayer()
    a = make(fb, player=pl)
    states = []
    a.press_voice()
    t = time.perf_counter()
    while a._thread.is_alive() and time.perf_counter() - t < 3:
        states.append(a.state)
        time.sleep(0.002)
    s = a.snapshot()
    seq = [x for i, x in enumerate(states) if i == 0 or x != states[i - 1]]
    check("voz: ouvir → transcrever → pensar → falar → pronta",
          seq[:1] == ["listening"] and "thinking" in seq and "speaking" in seq and s.state == "idle")
    check("pergunta transcrita, resposta e imagem disponíveis", s.question.startswith("O que") and "notebook" in s.answer
          and s.image is not None and s.image_version == 1)
    check("ordem de chamadas STT→LLM→TTS e áudio tocado", fb.calls == ["stt", "llm", "tts"] and pl.played)
    check("tempos registrados (transcrição, IA, voz)", {"stt", "ia", "voz"} <= set(s.timings))
    check("histórico guarda a conversa", a.history[-1][0] == s.question)

    a = make(FakeBackend())
    check("pergunta digitada é aceita", a.ask_text("  quantas   garrafas há?  "))
    wait_idle(a)
    check("digitada: sem STT, com imagem e resposta", a.snapshot().question == "quantas garrafas há?" and a.snapshot().answer)

    a = make(FakeBackend(), recorder=FakeRecorder(wav=None))
    a.press_voice(); wait_idle(a)
    check("sem fala: aviso, sem chamar API", a.snapshot().message_kind == "warning" and "Não ouvi" in a.snapshot().message)

    a = make(FakeBackend(), still=None)
    a.press_voice(); wait_idle(a)
    check("câmera indisponível: erro claro, app segue", a.state == "error" and "Câmera" in a.snapshot().message)

    a = make(FakeBackend(fail="llm"))
    a.ask_text("o que é isso?"); wait_idle(a)
    check("sem internet na IA: erro de conexão", a.state == "error" and "conexão" in a.snapshot().message)

    a = make(FakeBackend(fail="stt"))
    a.press_voice(); wait_idle(a)
    check("sem internet na transcrição: erro de conexão", a.state == "error" and "conexão" in a.snapshot().message)

    a = make(FakeBackend(fail="auth"))
    a.ask_text("oi"); wait_idle(a)
    check("chave inválida: erro e backend recriado na próxima", a.state == "error" and a._backend is None)

    spoken = []
    a = make(FakeBackend(tts_fail=True), offline=lambda t: spoken.append(t) or True)
    a.ask_text("oi"); wait_idle(a)
    check("voz online falha → voz do sistema + aviso", spoken and a.snapshot().message_kind == "warning")

    a = make(FakeBackend(tts_fail=True), offline=lambda t: False)
    a.ask_text("oi"); wait_idle(a)
    check("sem nenhuma voz: resposta fica em texto", a.snapshot().answer and "texto" in a.snapshot().message)

    a = make(FakeBackend(), recorder=FakeRecorder(error=VoiceError("Microfone indisponível: teste")))
    a.press_voice(); wait_idle(a)
    check("microfone indisponível: erro sugere digitar", a.state == "error" and "digitar" in a.snapshot().message)

    a = make(FakeBackend(), recorder=FakeRecorder(delay=5.0))
    a.press_voice(); time.sleep(0.05)
    check("tocar de novo durante a escuta envia", a.press_voice() == "send")
    wait_idle(a)
    pl = FakePlayer(seconds=5.0)
    a = make(FakeBackend(), player=pl, recorder=FakeRecorder(delay=5.0))
    a.ask_text("fale bastante")
    t = time.perf_counter()
    while a.state != "speaking" and time.perf_counter() - t < 2:
        time.sleep(0.005)
    t = time.perf_counter()
    r = a.press_voice()
    check("interrupção (barge-in): fala para e volta a ouvir rápido",
          r == "listen" and time.perf_counter() - t < 0.7 and a.state == "listening")
    a.cancel(); wait_idle(a)
    check("Esc cancela e volta a pronta", a.state == "idle")

    class Boom(FakeBackend):
        def answer(self, *a, **k):
            raise ZeroDivisionError("x")
    a = make(Boom())
    a.ask_text("oi"); wait_idle(a)
    check("exceção inesperada não derruba o app", a.state == "error")

    a = make(FakeBackend(delay=0.3))
    a.ask_text("um"); time.sleep(0.05)
    check("ocupada: não aceita outra pergunta ao mesmo tempo", a.state == "thinking" and not a.ask_text("dois"))
    wait_idle(a)

    sb = ia.SimulatedBackend()
    a = make(sb)
    a.ask_text("o que você vê?"); wait_idle(a)
    check("modo simulado responde identificando-se como simulação", "simulado" in a.snapshot().answer.lower())

    # robô reage aos estados
    import robot as robot_mod
    rb = robot_mod.Robot((80, 45), seed=1) if "seed" in robot_mod.Robot.__init__.__code__.co_varnames else robot_mod.Robot((80, 45))
    prev = "idle"
    a = make(FakeBackend(delay=0.3), player=FakePlayer(seconds=0.4))
    a.ask_text("oi")
    seen, now = set(), 0.0
    t = time.perf_counter()
    while time.perf_counter() - t < 3 and not (seen >= {"thinking", "speaking"} and a.state == "idle"):
        prev = a.drive_robot(rb, now, prev)
        rb.step(None, now, 1 / 60)
        seen.add(prev)
        now += 1 / 60
        time.sleep(1 / 120)
    check("rosto acompanha estados (pensar/falar)", {"thinking", "speaking"} <= seen)

    class Crier(FakeBackend):
        def answer(self, *a, **k):
            return ia.Answer("Olha só, estou chorando!", "sad", "fake", "cry")
    rb2 = robot_mod.Robot((80, 45))
    a = make(Crier(), player=FakePlayer(seconds=0.3))
    a.ask_text("chore")
    prev, cried, now = "idle", False, 0.0
    t = time.perf_counter()
    while time.perf_counter() - t < 2 and not cried:
        prev = a.drive_robot(rb2, now, prev)
        rb2.step(None, now, 1 / 60)
        cried = "cry" in rb2.actions.active()
        now += 1 / 60
        time.sleep(1 / 120)
    check("IA pede 'chorar' → rosto executa a simulação de choro", cried)
    wait_idle(a)

    class Angry(FakeBackend):
        def answer(self, *a, **k):
            return ia.Answer("Grr, estou brava!", "angry", "fake")
    rb3 = robot_mod.Robot((80, 45))
    a = make(Angry(), player=FakePlayer(seconds=1.2))
    a.ask_text("fique brava")
    prev, shown, now = "idle", [], 0.0
    t = time.perf_counter()
    while time.perf_counter() - t < 4 and not (shown and a.state == "idle" and prev == "idle"):
        prev = a.drive_robot(rb3, now, prev)
        if prev == "speaking" and len(shown) == 20:
            rb3.expr.pulse("happy", 1.0)              # reação visual concorrente (ex.: sorriso detectado)
        f = rb3.step(None, now, 1 / 60)
        if prev == "speaking":
            shown.append(f.shown)
        now += 1 / 60
        time.sleep(1 / 60)
    check("emoção fica até o fim da fala, mesmo com outra reação", len(shown) > 40 and all(x == "angry" for x in shown))
    check("terminada a fala, volta ao automático", rb3.expr.mood == "auto")


def test_interface(png=True):
    print("Interface (RF05)")
    import pygame
    import theme
    from painel_assistente import AssistantPanel
    from renderer import HudInfo, Renderer
    from robot import Robot
    pygame.init()
    out = Path(__file__).parent / "previews"
    out.mkdir(exist_ok=True)
    rb = Robot((80, 45))
    frame = rb.step([], 0.0, 1 / 60)
    for size in ((1280, 720), (1920, 1080), (800, 600)):
        screen = pygame.display.set_mode(size)
        for key in ("dark", "light", "contrast"):
            r = Renderer(screen, (80, 45), theme.SCHEMES[key])
            panel = AssistantPanel()
            r.reserve_right = r.menu.reserve_right = panel.reserve(screen, r.s)
            r.resize(screen)
            a = make(FakeBackend(), player=FakePlayer(seconds=30))
            states = {}
            a._set(state="idle")
            states["pronta"] = None
            for name, kw in [("pronta", dict(state="idle")),
                             ("ouvindo", dict(state="listening")),
                             ("pensando", dict(state="thinking", question="O que está sobre a mesa?", image=scene(), image_version=1)),
                             ("falando", dict(state="speaking", question="O que está sobre a mesa?",
                                              answer="Vejo um notebook preto e uma garrafa laranja sobre a mesa azul. Parece um bom lugar para estudar!",
                                              image=scene(), image_version=1, timings=dict(stt=.8, ia=1.9, voz=.7, total_ate_falar=3.6))),
                             ("erro", dict(state="error", question="O que é isso?", image=scene(), image_version=1,
                                           message="Sem conexão com a OpenAI. Verifique a internet."))]:
                a._set(**kw)
                r.draw_panel(frame, 1 / 60, False)
                panel.draw(r, a, 1 / 60, False, "OpenAI · gpt-4.1-mini")
                r.draw_hud(frame, HudInfo(camera_status="ok"))
                if True:
                    ok = r.screen.get_rect().contains(panel.rect) and panel.mic.h >= 44 and panel.type_btn.w >= 44
                    ok = ok and not panel.rect.colliderect(r.panel)
                    ok = ok and not any(panel.rect.colliderect(d) for d in r.menu.dock)
                    states[name] = states.get(name, True) and ok
                if png and (key == "dark" or name in ("falando", "erro")):
                    pygame.image.save(screen, str(out / f"nuria_{key}_{name}_{size[0]}x{size[1]}.png"))
            if True:
                check(f"{key} {size[0]}x{size[1]}: coluna cabe na janela, alvos ≥44 pt, rosto e dock não são cobertos", all(v for v in states.values() if v is not None))
            panel.typing, panel.draft = True, "quantas garrafas tem na mesa?"
            r.draw_panel(frame, 1 / 60, False)
            panel.draw(r, a, 1 / 60, False)
            if png and key == "dark":
                pygame.image.save(screen, str(out / f"nuria_{key}_digitando_{size[0]}x{size[1]}.png"))
            a.shutdown()
    # contraste do texto sobre a coluna
    for key, sc in theme.SCHEMES.items():
        check(f"{key}: contraste texto/coluna ≥ 4,5:1", theme.contrast(sc.text, sc.chip) >= 4.5 and theme.contrast(sc.text_dim, sc.chip) >= 4.5)
    check("contraste do botão azul com texto branco ≥ 3:1",
          all(theme.contrast((255, 255, 255), sc.palette["blue"]) >= 3.0 for k, sc in theme.SCHEMES.items() if k != "contrast"))
    # eventos: espaço pergunta, Enter digita, teclas de atalho não vazam enquanto digita
    screen = pygame.display.set_mode((1280, 720))
    r = Renderer(screen, (80, 45), theme.SCHEMES["dark"])
    panel = AssistantPanel()
    panel.layout(r)
    a = make(FakeBackend(), recorder=FakeRecorder(delay=2))
    panel.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE, mod=0), a)
    time.sleep(0.05)
    check("Espaço inicia a escuta", a.state == "listening")
    a.cancel(); wait_idle(a)
    panel.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, mod=0), a)
    q_consumed = panel.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_q, mod=0), a)
    panel.handle_event(pygame.event.Event(pygame.TEXTINPUT, text="oi?"), a)
    check("Enter abre o campo; teclas não acionam atalhos (Q não fecha)", panel.typing and q_consumed and panel.draft == "oi?")
    panel.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, mod=0), a)
    wait_idle(a)
    check("Enter envia a pergunta digitada", not panel.typing and a.snapshot().question == "oi?")
    a3 = make(FakeBackend())
    panel.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_v, mod=0), a3)
    off = a3.snapshot().vision is False
    panel.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=panel.vis_btn.center), a3)
    panel.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=panel.vis_btn.center), a3)
    check("V e o botão da câmera pausam/retomam a visão", off and a3.snapshot().vision is True
          and panel.vis_btn.w >= 44 and not panel.vis_btn.colliderect(panel.mic))
    down = pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=panel.mic.center)
    up = pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=panel.mic.center)
    a2 = make(FakeBackend(), recorder=FakeRecorder(delay=2))
    panel.handle_event(down, a2)
    check("pressionar o botão dá retorno imediato sem disparar", panel._pressed == "mic" and a2.state == "idle")
    panel.handle_event(up, a2); time.sleep(0.05)
    check("soltar sobre o botão inicia a escuta", a2.state == "listening")
    a2.cancel(); wait_idle(a2)
    pygame.quit()


def test_app_smoke():
    print("Aplicativo completo (--sim, IA simulada, sem câmera)")
    import main as app
    with tempfile.TemporaryDirectory() as d:
        import cv2
        p = str(Path(d) / "cena.jpg")
        cv2.imwrite(p, scene())
        import pygame
        holder = {}

        def poster():
            time.sleep(0.8)
            pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, mod=0, unicode="\r", scancode=0))
            time.sleep(0.2)
            pygame.event.post(pygame.event.Event(pygame.TEXTINPUT, text="o que você vê?"))
            time.sleep(0.2)
            pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, mod=0, unicode="\r", scancode=0))
        threading.Thread(target=poster, daemon=True).start()
        t = time.perf_counter()
        code = app.main(["--sim", "--ia", "simulada", "--imagem", p, "--sem-registro", "--run-seconds", "4"])
        check("app abre, responde pergunta digitada e fecha sem erro", code == 0)
        check("laço não travou durante a resposta (≤ 7 s de parede)", time.perf_counter() - t < 7)


if __name__ == "__main__":
    png = "--no-png" not in sys.argv
    test_config()
    test_parsing()
    test_openai_requests()
    test_audio_devices()
    test_camera_still()
    test_flows()
    test_interface(png)
    test_app_smoke()
    ok = sum(1 for _, v in RESULTS if v)
    print(f"\n{ok}/{len(RESULTS)} verificações OK")
    sys.exit(0 if ok == len(RESULTS) else 1)
