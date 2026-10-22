"""Testes do caminho Realtime com conexão e dispositivos SIMULADOS (não é validação real).

Cobre: ordem dos eventos, imagem antes da resposta, áudio progressivo (entrada 24 kHz em blocos e
saída tocada enquanto chega), expressões por função, interrupção com truncate, reconexão,
encerramento e o transporte WebSocket real contra um servidor WebSocket local.
Uso: python test_realtime.py
"""
from __future__ import annotations

import base64
import json
import os
import sys
import threading
import time
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np

import credenciais
import realtime as rt
from assistente_realtime import RealtimeAssistant, build_session
from voz import StreamPlayer, VoiceError

RESULTS = []


def check(name, cond):
    RESULTS.append((name, bool(cond)))
    print(f"  [{'OK' if cond else 'FALHOU'}] {name}")


def scene():
    img = np.zeros((480, 640, 3), np.uint8)
    img[:] = (60, 140, 200)
    return img


def cfg(**kw):
    base = credenciais.load(Path("/nao/existe"), environ={"OPENAI_API_KEY": "sk-teste"})
    return credenciais.AIConfig(**{**base.__dict__, **kw})


class FakeMic:
    """Envia 1 s de "voz" em blocos de 40 ms a 24 kHz; ou espera Espaço (manual)."""
    def __init__(self, seconds=1.0, manual=False, error=None):
        self.seconds, self.manual, self.error, self.level, self.runs = seconds, manual, error, 0.0, 0
        self.active = False

    def run(self, stop, on_chunk):
        self.runs += 1
        if self.error:
            raise self.error
        self.active = True
        try:
            n = int(self.seconds / 0.04)
            for _ in range(n):
                if stop.is_set():
                    return "manual"
                on_chunk((np.random.default_rng(1).normal(0, 3000, 960)).astype(np.int16))
                time.sleep(0.004)
            if self.manual:
                stop.wait(5)
                return "manual"
            return "speech" if n else "silence"
        finally:
            self.active = False


class FakePlayer(StreamPlayer):
    """StreamPlayer real, mas o "dispositivo" é uma thread que consome a 8× o tempo real."""
    def __init__(self, speed=8.0):
        super().__init__()
        self.speed = speed
        self._run = False

    def start(self):
        if self._run:
            return
        self._run = True

        def loop():
            out = np.zeros((480, 1), np.int16)
            while self._run:
                self._callback(out, 480, None, None)
                time.sleep(0.02 / self.speed)
        threading.Thread(target=loop, daemon=True).start()

    def close(self):
        self._run = False


class Harness:
    def __init__(self, server_cls=rt.FakeRealtimeServer, mic=None, still=scene, config=None, **server_kw):
        self.servers = []
        self.config = config or cfg()

        def factory(c):
            s = server_cls(**server_kw)
            self.servers.append(s)
            return s
        self.mic = mic or FakeMic()
        self.player = FakePlayer()
        self.a = RealtimeAssistant(lambda: still() if still else None, transport_factory=factory, mic=self.mic,
                                   player=self.player, config_loader=lambda: self.config, log=False)
        self.states = []
        self._watch = True
        threading.Thread(target=self._watcher, daemon=True).start()

    def _watcher(self):
        while self._watch:
            st = self.a.state
            if not self.states or self.states[-1] != st:
                self.states.append(st)
            time.sleep(0.001)

    @property
    def server(self):
        return self.servers[-1]

    def sent(self, kind=None):
        evs = [e for s in self.servers for e in s.received]
        return [e for e in evs if kind is None or e["type"] == kind]

    def wait(self, cond, timeout=5.0):
        t = time.perf_counter()
        while time.perf_counter() - t < timeout:
            if cond():
                return True
            time.sleep(0.005)
        return False

    def idle(self, timeout=6.0):
        return self.wait(lambda: self.a.state in ("idle", "error") and not (self.a._thread and self.a._thread.is_alive())
                         and not (self.a._drain_thread and self.a._drain_thread.is_alive()), timeout)

    def close(self):
        self._watch = False
        self.a.shutdown()


def test_session_config():
    print("Configuração da sessão")
    s = build_session(cfg())
    check("sessão GA: type realtime, áudio PCM 24 kHz entrada/saída, só áudio",
          s["type"] == "realtime" and s["audio"]["input"]["format"] == {"type": "audio/pcm", "rate": 24000}
          and s["audio"]["output"]["format"]["rate"] == 24000 and s["output_modalities"] == ["audio"])
    check("modelo e voz configuráveis (padrão gpt-realtime-2.1 / marin)",
          s["model"] == "gpt-realtime-2.1" and s["audio"]["output"]["voice"] == "marin"
          and build_session(cfg(realtime_voice="cedar"))["audio"]["output"]["voice"] == "cedar")
    check("detecção de turno local (turn_detection nulo, controles manuais)", s["audio"]["input"]["turn_detection"] is None)
    check("transcrição da pergunta em pt para a interface; pode desligar",
          s["audio"]["input"]["transcription"]["language"] == "pt"
          and "transcription" not in build_session(cfg(transcription="desligada"))["audio"]["input"])
    check("ferramenta expressar com emoções e ações", s["tools"][0]["name"] == "expressar"
          and "chorar" in s["tools"][0]["parameters"]["properties"]["acao"]["enum"])
    check("identidade: protótipo chamado apenas Nuria", "Seu nome é Nuria" in s["instructions"]
          and "Ayra" not in s["instructions"] and "protótipo" in s["instructions"])
    check("respostas curtas (1–2 frases, ~20 palavras)", "CURTAS" in s["instructions"] and "20 palavras" in s["instructions"])
    check("só fala de si quando perguntarem", "Só fale de si mesma" in s["instructions"])
    check("chave fora da sessão e do repr", "sk-teste" not in json.dumps(s) and "sk-teste" not in repr(cfg()))


def test_voice_flow():
    print("Pergunta por voz (simulada)")
    h = Harness()
    h.a.press_voice()
    ok = h.idle()
    s = h.a.snapshot()
    types = [e["type"] for e in h.sent()]
    check("fluxo termina em Pronta", ok and s.state == "idle")
    seq = [x for x in h.states if x != "idle"]
    core = [x for x in seq if x not in ("capturing", "connecting")]   # etapas breves podem escapar da amostragem
    check("estados: (conectando) → ouvindo → (capturando) → pensando → falando",
          core[:3] == ["listening", "thinking", "speaking"] and h.a.connection == "conectado")
    check("session.update é o primeiro evento", types[0] == "session.update")
    appends = h.sent("input_audio_buffer.append")
    pcm = np.frombuffer(base64.b64decode(appends[0]["audio"]), np.int16)
    check("áudio enviado em blocos de 40 ms a 24 kHz durante a escuta", len(appends) >= 20 and len(pcm) == 960)
    i_commit, i_image, i_resp = types.index("input_audio_buffer.commit"), None, types.index("response.create")
    i_image = next(i for i, e in enumerate(h.sent()) if e["type"] == "conversation.item.create"
                   and any(p.get("type") == "input_image" for p in e["item"].get("content", [])))
    check("ordem: blocos → commit → imagem → response.create",
          types.index("input_audio_buffer.append") < i_commit < i_image < i_resp)
    img = [p for p in h.sent()[i_image]["item"]["content"] if p["type"] == "input_image"][0]
    check("imagem JPEG em data URL", img["image_url"].startswith("data:image/jpeg;base64,"))
    check("pergunta transcrita aparece na interface", s.question == "O que você está vendo?")
    check("resposta aparece em texto progressivo", "Modo simulado" in s.answer)
    check("áudio da resposta tocado (blocos reproduzidos)", h.player.played > 0 and h.player.played == h.player.received)
    check("tempo até o primeiro áudio medido", "primeiro_audio" in s.timings and "resposta_completa" in s.timings)
    check("microfone fechado enquanto fala (meia-duplex, sem eco)", h.mic.runs == 1 and not h.mic.active)
    imgs = [e["item"]["id"] for e in h.sent("conversation.item.create") if e["item"].get("id", "").startswith("img_")]
    check("voz: imagem apagada da sessão ao fim da resposta (cobrada uma vez)",
          len(imgs) == 1 and [e["item_id"] for e in h.sent("conversation.item.delete")] == imgs)
    check("histórico guarda a troca", h.a.history and h.a.history[-1][0] == "O que você está vendo?")
    n_conn = len(h.servers)
    h.a.ask_text("e agora?")
    h.idle()
    check("conexão persiste entre perguntas", len(h.servers) == n_conn)
    h.close()


def test_text_and_errors():
    print("Pergunta digitada, falhas e cancelamento (simulados)")
    h = Harness()
    h.a.ask_text("o que tem na mesa?")
    h.idle()
    items = h.sent("conversation.item.create")
    types = [e["type"] for e in h.sent()]
    q_item = [e for e in items if e["item"]["content"][0].get("text") == "o que tem na mesa?"]
    img_items = [e for e in items if any(p["type"] == "input_image" for p in e["item"]["content"])]
    i_q, i_img = h.sent().index(q_item[0]), h.sent().index(img_items[0])
    check("digitada: pergunta e imagem em itens separados, antes do response.create",
          len(q_item) == 1 and len(img_items) == 1 and i_q < i_img < types.index("response.create"))
    deleted = [e["item_id"] for e in h.sent("conversation.item.delete")]
    check("após a resposta, só a imagem sai da sessão (pergunta fica)",
          deleted == [img_items[0]["item"]["id"]] and types.index("conversation.item.delete") > types.index("response.create"))
    check("digitada não usa microfone", h.mic.runs == 0 and not h.sent("input_audio_buffer.append"))
    h.close()

    h = Harness(mic=FakeMic(seconds=0.0))
    h.a.press_voice()
    h.idle()
    check("nada dito: buffer limpo, sem commit nem resposta",
          not h.sent("input_audio_buffer.commit") and not h.sent("response.create") and h.a.snapshot().message_kind == "warning")
    h.close()

    h = Harness(still=None)
    h.a.ask_text("oi")
    h.idle()
    check("câmera indisponível: erro claro e nenhuma resposta pedida", h.a.state == "error" and not h.sent("response.create")
          and "Câmera" in h.a.snapshot().message)
    h.close()

    h = Harness(mic=FakeMic(error=VoiceError("Microfone indisponível: teste")))
    h.a.press_voice()
    h.idle()
    check("microfone indisponível: erro sugere digitar", h.a.state == "error" and "digitar" in h.a.snapshot().message)
    h.close()

    h = Harness(mic=FakeMic(seconds=0.4, manual=True))
    h.a.press_voice()
    h.wait(lambda: h.a.state == "listening" and len(h.sent("input_audio_buffer.append")) > 5)
    check("Espaço durante a escuta envia na hora", h.a.press_voice() == "send")
    h.idle()
    check("envio manual gera commit e resposta", h.sent("input_audio_buffer.commit") and h.sent("response.create"))
    h.close()

    h = Harness(mic=FakeMic(seconds=0.4, manual=True))
    h.a.press_voice()
    h.wait(lambda: h.a.state == "listening")
    h.a.cancel()
    h.idle()
    check("Esc cancela a escuta sem pedir resposta", h.a.state == "idle" and not h.sent("response.create"))
    h.close()


def test_vision_pause():
    print("Visão pausada: só conversa (simulado)")
    shots = []

    def still():
        shots.append(1)
        return scene()
    h = Harness(still=still)
    h.a.set_vision(False)
    h.a.ask_text("tudo bem?")
    h.idle()
    h.a.press_voice()
    h.idle()
    items = [e for e in h.sent("conversation.item.create")]
    has_image = any(p.get("type") == "input_image" for e in items for p in e["item"].get("content", []))
    notes = [e for e in items if "Visão pausada" in json.dumps(e, ensure_ascii=False)]
    check("visão pausada: nenhuma foto tirada nem enviada (texto e voz)", not shots and not has_image)
    check("modelo é avisado de que não há imagem", len(notes) == 2)
    check("respostas seguem normalmente", h.a.state == "idle" and h.a.snapshot().answer and len(h.sent("response.create")) == 2)
    h.a.set_vision(True)
    h.a.ask_text("e agora, o que vê?")
    h.idle()
    check("retomar a visão volta a enviar a imagem", shots and any(p.get("type") == "input_image"
          for p in h.sent("conversation.item.create")[-1]["item"]["content"]))
    h.close()
    h = Harness(still=None)
    h.a.set_vision(False)
    h.a.ask_text("oi")
    h.idle()
    check("com visão pausada, câmera indisponível não impede conversar", h.a.state == "idle" and h.a.snapshot().answer)
    h.close()


def test_expression_tool():
    print("Expressões por função (simulado)")
    import robot as robot_mod
    h = Harness()
    rb = robot_mod.Robot((80, 45))
    h.a.ask_text("você consegue chorar?")
    prev, now, cried, shown = "idle", 0.0, False, []
    t = time.perf_counter()
    while time.perf_counter() - t < 6:
        prev = h.a.drive_robot(rb, now, prev)
        f = rb.step(None, now, 1 / 60)
        cried = cried or "cry" in rb.actions.active()
        if prev == "speaking":
            shown.append(f.shown)
        now += 1 / 60
        time.sleep(1 / 240)
        if h.a.state == "idle" and prev == "idle" and shown:
            break
    outs = [e for e in h.sent("conversation.item.create") if e["item"].get("type") == "function_call_output"]
    creates = h.sent("response.create")
    check("chamada de função devolve function_call_output e pede a fala", len(outs) == 1 and len(creates) == 2)
    t_types = [e["type"] for e in h.sent()]
    check("com função: imagem só sai depois da resposta falada",
          len(h.sent("conversation.item.delete")) == 1
          and t_types.index("conversation.item.delete") > len(t_types) - 1 - t_types[::-1].index("response.create"))
    check("rosto chora durante a resposta", cried)
    check("emoção mantida em toda a fala", shown and all(x == "sad" for x in shown))
    check("fala não lê JSON/comando", "{" not in h.a.snapshot().answer and "expressar" not in h.a.snapshot().answer)
    check("terminada a fala, rosto volta ao automático", rb.expr.mood == "auto" and "cry" not in rb.actions.active())
    h.close()


def test_interrupt():
    print("Interrupção (barge-in) simulada")

    class Slow(rt.FakeRealtimeServer):
        def __init__(self, **kw):
            super().__init__(speed=0.5, **kw)
    h = Harness(server_cls=Slow, mic=FakeMic(seconds=0.2))
    h.player.speed = 1.0
    h.a.ask_text("fale bastante")
    h.wait(lambda: h.a.state == "speaking" and h.player.played > 2400, 12)
    received_before = h.player.received
    t = time.perf_counter()
    r = h.a.press_voice()
    check("Espaço durante a fala volta a ouvir em < 300 ms", r == "listen" and h.wait(lambda: h.a.state == "listening", 0.3)
          and time.perf_counter() - t < 0.5)
    cancel = h.sent("response.cancel")
    trunc = h.sent("conversation.item.truncate")
    check("response.cancel enviado", len(cancel) == 1)
    check("interrompida: imagem da pergunta também sai da sessão", len(h.sent("conversation.item.delete")) == 1)
    check("truncate no trecho ouvido (0 < audio_end_ms < recebido)",
          len(trunc) == 1 and 0 < trunc[0]["audio_end_ms"] < rt.ms(received_before) + 1 and trunc[0]["content_index"] == 0)
    check("áudio pendente descartado", h.player.pending() == 0 or h.a.state != "speaking")
    h.idle(20)
    check("nova pergunta segue normalmente após a interrupção", h.a.state == "idle" and h.a.snapshot().question)
    h.close()


def test_reconnect_and_close():
    print("Reconexão e encerramento (simulados)")
    h = Harness()
    h.a.ask_text("oi")
    h.idle()
    h.server.drop()
    check("queda em repouso: marcada como desconectada", h.wait(lambda: h.a.connection == "desconectado", 1))
    h.a.ask_text("ainda está aí?")
    h.idle()
    restored = [e for e in h.servers[-1].received if e["type"] == "conversation.item.create"
                and "Contexto da conversa anterior" in json.dumps(e)]
    check("próxima pergunta reconecta e responde", len(h.servers) == 2 and h.a.state == "idle" and h.a.snapshot().answer)
    check("contexto anterior levado à nova sessão", len(restored) == 1)
    h.close()

    class Slow(rt.FakeRealtimeServer):
        def __init__(self, **kw):
            super().__init__(speed=0.5, **kw)
    h = Harness(server_cls=Slow)
    h.a.ask_text("fale")
    h.wait(lambda: h.a.state == "speaking")
    h.server.drop()
    h.idle()
    check("queda durante a fala: erro claro, app segue", h.a.state == "error" and "caiu" in h.a.snapshot().message)
    h.close()

    calls = []

    def failing(c):
        calls.append(1)
        raise rt.TransportClosed("Sem conexão com a OpenAI. Verifique a internet.")
    a = RealtimeAssistant(scene, transport_factory=failing, mic=FakeMic(), player=FakePlayer(),
                          config_loader=lambda: cfg(), log=False)
    a.ask_text("oi")
    t = time.perf_counter()
    while a.state != "error" and time.perf_counter() - t < 5:
        time.sleep(0.01)
    check("sem internet: 3 tentativas e erro de conexão", len(calls) == 3 and "conexão" in a.snapshot().message)
    a.shutdown()

    a = RealtimeAssistant(scene, mic=FakeMic(), player=FakePlayer(), log=False,
                          config_loader=lambda: credenciais.load(Path("/nao/existe"), environ={}))
    a.ask_text("oi")
    t = time.perf_counter()
    while a.state != "error" and time.perf_counter() - t < 3:
        time.sleep(0.01)
    check("sem chave: erro orienta configurar o .env", a.state == "error" and ".env" in a.snapshot().message)
    a.shutdown()

    h = Harness()
    h.a.ask_text("oi")
    h.idle()
    h.config = cfg(transcription="whisper-1")
    h.a.ask_text("de novo")
    h.idle()
    upd = h.sent("session.update")
    check("mudança segura: session.update sem trocar modelo/voz nem reconectar",
          len(h.servers) == 1 and len(upd) == 2 and "voice" not in upd[1]["session"]["audio"]["output"]
          and "model" not in upd[1]["session"])
    h.config = cfg(realtime_voice="cedar")
    h.a.ask_text("e agora?")
    h.idle()
    check("troca de voz recria a sessão", len(h.servers) == 2)
    server = h.server
    h.close()
    check("encerramento fecha a conexão", server._closed and not h.a._client.connected)

    class NoReasoning(rt.FakeRealtimeServer):
        def send(self, text):
            ev = json.loads(text)
            if ev["type"] == "session.update" and "reasoning" in ev["session"]:
                self.received.append(ev)
                self._emit({"type": "error", "error": {"message": "Unknown parameter: 'session.reasoning'.",
                                                       "param": "session.reasoning"}})
                return
            super().send(text)
    h = Harness(server_cls=NoReasoning)
    h.a.ask_text("oi")
    h.idle()
    check("modelo sem 'reasoning': repete a sessão sem o campo e funciona", h.a.state == "idle" and h.a.snapshot().answer)
    h.close()

    h = Harness()
    h.a.warmup()
    check("aquecimento conecta ao abrir o app", h.wait(lambda: h.a.connection == "conectado", 2))
    h.a.ask_text("oi")
    h.idle()
    check("aquecimento + pergunta usam uma única conexão", len(h.servers) == 1)
    h.close()


def test_real_websocket_transport():
    print("Transporte WebSocket real contra servidor WebSocket local (sem internet)")
    try:
        from websockets.sync.server import serve
    except ImportError:
        check("websockets ausente (pulado)", True)
        return
    seen = {}

    def handler(ws):
        seen["auth"] = ws.request.headers.get("Authorization")
        seen["path"] = ws.request.path
        for raw in ws:
            ev = json.loads(raw)
            seen.setdefault("types", []).append(ev["type"])
            if ev["type"] == "session.update":
                ws.send(json.dumps({"type": "session.updated", "session": ev["session"]}))
            if ev["type"] == "response.create":
                pcm = np.zeros(2400, np.int16)
                ws.send(json.dumps({"type": "response.created", "response": {"id": "r1"}}))
                ws.send(json.dumps({"type": "response.output_item.added", "item": {"id": "m1", "type": "message"}}))
                ws.send(json.dumps({"type": "response.output_audio_transcript.delta", "item_id": "m1", "delta": "Olá!"}))
                ws.send(json.dumps({"type": "response.output_audio.delta", "item_id": "m1",
                                    "delta": base64.b64encode(pcm.tobytes()).decode()}))
                ws.send(json.dumps({"type": "response.done", "response": {"id": "r1", "status": "completed",
                                                                          "output": [{"type": "message", "id": "m1"}]}}))
    with serve(handler, "127.0.0.1", 0) as server:
        port = server.socket.getsockname()[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        c = cfg(realtime_url=f"ws://127.0.0.1:{port}/v1/realtime?model={{model}}")
        player = FakePlayer()
        a = RealtimeAssistant(scene, mic=FakeMic(), player=player, config_loader=lambda: c, log=False)
        a.ask_text("diga olá")
        t = time.perf_counter()
        while time.perf_counter() - t < 5 and not (a.state == "idle" and a.snapshot().answer):
            time.sleep(0.01)
        check("WebSocket real: Bearer no cabeçalho e modelo na URL",
              seen.get("auth") == "Bearer sk-teste" and seen.get("path", "").endswith("model=gpt-realtime-2.1"))
        check("WebSocket real: sessão, item e resposta trafegam; áudio tocado",
              seen.get("types", [])[:1] == ["session.update"] and "response.create" in seen.get("types", [])
              and a.snapshot().answer == "Olá!" and player.played == 2400)
        a.shutdown()
        server.shutdown()


def test_audio_devices():
    print("Microfone 24 kHz e reprodução progressiva com PortAudio simulado")
    import voz

    class FakeSD:
        def __init__(self, reject_24k=False):
            self.reject = reject_24k
            self.rates = []

        def InputStream(self, samplerate, channels, dtype, blocksize, callback):
            if self.reject and samplerate == 24000:
                raise OSError("Invalid sample rate")
            self.rates.append(samplerate)
            sd = self

            class S:
                def __enter__(s):
                    s.run = True
                    def feed():
                        t = 0.0
                        while s.run:
                            amp = 0.08 if 2.0 < t < 3.2 else 0.002
                            data = (np.random.default_rng(int(t * 1e3)).normal(0, 1, blocksize) * amp * 32767).astype(np.int16)
                            callback(data.reshape(-1, 1), blocksize, None, None)
                            t += blocksize / samplerate
                            time.sleep(blocksize / samplerate / 4)
                    s.th = threading.Thread(target=feed, daemon=True)
                    s.th.start()
                    return s

                def __exit__(s, *a):
                    s.run = False
                    s.th.join()
            return S()

        def query_devices(self, kind=None):
            return {"default_samplerate": 48000.0, "name": "falso"}

    orig = voz._sd
    try:
        for reject in (False, True):
            fake = FakeSD(reject)
            voz._sd = lambda: fake
            chunks = []
            mic = voz.MicStream(silence_after=0.4)
            reason = mic.run(threading.Event(), chunks.append)
            label = "48 kHz convertido" if reject else "24 kHz direto"
            check(f"microfone {label}: blocos de 40 ms a 24 kHz e fim da fala detectado",
                  reason == "speech" and chunks and all(len(c) == 960 for c in chunks[:5]))
    finally:
        voz._sd = orig
    p = FakePlayer(speed=4)
    p.begin()
    p.start()
    p.feed(np.ones(12000, np.int16) * 9000)          # chega um bloco, já começa a tocar
    time.sleep(0.05)
    lvl, mid = p.level(), p.played
    p.feed(np.ones(12000, np.int16) * 9000)          # outro bloco chega durante a reprodução
    time.sleep(0.4)
    p.close()
    check("reprodução progressiva: toca enquanto chega, conta o tocado e mede o volume real",
          0 < mid < 12000 and p.played == 24000 and lvl > 0.5 and p.level() == 0.0)
    p2 = FakePlayer(speed=0.5)
    p2.begin()
    p2.feed(np.ones(48000, np.int16))
    p2.start()
    time.sleep(0.1)
    played = p2.clear()
    check("clear descarta pendente e informa o trecho ouvido", 0 < played < 48000 and p2.pending() == 0)
    p2.close()


if __name__ == "__main__":
    test_session_config()
    test_voice_flow()
    test_text_and_errors()
    test_vision_pause()
    test_expression_tool()
    test_interrupt()
    test_reconnect_and_close()
    test_real_websocket_transport()
    test_audio_devices()
    ok = sum(1 for _, v in RESULTS if v)
    print(f"\n{ok}/{len(RESULTS)} verificações OK (simuladas; não substituem o teste com API, microfone e webcam reais)")
    sys.exit(0 if ok == len(RESULTS) else 1)
