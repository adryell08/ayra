"""Testes da etapa 2 (controle remoto + voz robótica), com rede local de verdade (127.0.0.1) e WebRTC real
(aiortc dos dois lados), mas câmera, microfone e IA SIMULADOS.

Uso: python test_operador.py
"""
from __future__ import annotations

import asyncio
import fractions
import json
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

import operador
from filtro_voz import RobotVoice

RESULTS = []


def check(name, cond):
    RESULTS.append((name, bool(cond)))
    print(f"  [{'OK' if cond else 'FALHOU'}] {name}")


def wait(cond, t=5.0):
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < t:
        if cond():
            return True
        time.sleep(0.02)
    return False


# ---------------------------------------------------------------------------
def test_filter():
    print("Voz robótica")
    R = 24000

    def voice(f0, secs=1.0):
        t = np.arange(int(R * secs)) / R
        s = sum(np.sin(2 * np.pi * f0 * k * t) / k for k in range(1, 25))
        env = 0.5 + 0.5 * np.sin(2 * np.pi * 3 * t)
        return (s * env / np.max(np.abs(s)) * 12000).astype(np.int16)

    def periodicity(y, f):
        y = y.astype(float) - y.mean()
        ac = np.correlate(y[:6000], y[:6000], "full")[5999:]
        return ac[int(round(R / f))] / ac[0]

    outs, costs = [], []
    for f0 in (95, 140, 230):                              # três "vozes" diferentes (grave, média, aguda)
        v, x = RobotVoice(), voice(f0)
        t = time.perf_counter()
        y = np.concatenate([v.process(x[i:i + 960]) for i in range(0, len(x), 960)])
        costs.append((time.perf_counter() - t) / (len(x) / 960))
        outs.append((x, y[2400:]))
    check("vozes diferentes saem com o mesmo tom (periodicidade em 105 Hz)", all(periodicity(y, 105) > 0.5 for _, y in outs))
    check("volume acompanha a voz original (sem estourar)",
          all(0.5 < np.sqrt(np.mean(y.astype(float) ** 2)) / np.sqrt(np.mean(x.astype(float) ** 2)) < 2.0
              and np.abs(y).max() < 32767 for x, y in outs))
    check("leve: < 8 ms por bloco de 40 ms", max(costs) < 0.008)
    v = RobotVoice()
    y = v.process(np.zeros(960, np.int16))
    check("silêncio continua silêncio", np.abs(y).max() < 50)


# ---------------------------------------------------------------------------
class FakeMic:
    def __init__(self):
        self.started = self.stopped = False
        self.level = 0.0

    def start(self):
        self.started = True
        return True

    def read(self, timeout=0.1):
        time.sleep(0.02)
        t = np.arange(960) / 48000
        return (np.sin(2 * np.pi * 440 * t) * 3000).astype(np.int16)

    def stop(self):
        self.stopped = True


def scene():
    img = np.zeros((480, 640, 3), np.uint8)
    img[:] = (40, 120, 200)
    return img


def test_server():
    print("Sala do operador: WebRTC real em 127.0.0.1 (câmera, microfone e IA simulados)")
    from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription
    import aiohttp
    import av

    received_audio = []
    mics = []

    def mic_factory():
        m = FakeMic()
        mics.append(m)
        return m
    srv = operador.OperatorServer(scene, lambda: dict(estado="idle", modo="autonomo", visao=True),
                                  lambda pcm: received_audio.append(pcm), port=18765, pin="123456", use_tls=False,
                                  mic_factory=mic_factory)
    check("servidor sobe na rede local", srv.start() and srv.status == "aguardando operador")
    check("endereço para o operador", srv.url.startswith("http://") and srv.url.endswith(":18765"))

    class Tone(MediaStreamTrack):
        kind = "audio"

        def __init__(self):
            super().__init__()
            self.pts = 0

        async def recv(self):
            await asyncio.sleep(0.02)
            t = (self.pts + np.arange(960)) / 48000
            pcm = (np.sin(2 * np.pi * 300 * t) * 8000).astype(np.int16)
            fr = av.AudioFrame.from_ndarray(pcm.reshape(1, -1), format="s16", layout="mono")
            fr.sample_rate, fr.pts, fr.time_base = 48000, self.pts, fractions.Fraction(1, 48000)
            self.pts += 960
            return fr

    result = {}

    async def client():
        async with aiohttp.ClientSession() as http:
            async with http.get("http://127.0.0.1:18765/") as r:
                page = await r.text()
                result["page"] = r.status == 200 and "Sala do operador" in page
            pc = RTCPeerConnection()
            dc = pc.createDataChannel("controle")
            msgs = []
            dc.on("message", lambda m: msgs.append(json.loads(m)))
            pc.addTrack(Tone())
            pc.addTransceiver("video", direction="recvonly")
            frames = {"video": [], "audio": 0}

            @pc.on("track")
            def on_track(track):
                async def pull():
                    while True:
                        try:
                            f = await track.recv()
                        except Exception:
                            return
                        if track.kind == "video":
                            frames["video"].append((f.width, f.height, time.perf_counter()))
                        else:
                            frames["audio"] += 1
                asyncio.ensure_future(pull())
            await pc.setLocalDescription(await pc.createOffer())
            body = dict(sdp=pc.localDescription.sdp, type=pc.localDescription.type, nome="Teste")
            async with http.post("http://127.0.0.1:18765/offer", json=dict(body, pin="000000")) as r:
                result["wrong_pin"] = r.status == 403
            async with http.post("http://127.0.0.1:18765/offer", json=dict(body, pin="123456")) as r:
                ans = await r.json()
            await pc.setRemoteDescription(RTCSessionDescription(sdp=ans["sdp"], type=ans["type"]))
            for _ in range(100):
                if dc.readyState == "open":
                    break
                await asyncio.sleep(0.05)
            result["dc"] = dc.readyState == "open"
            await asyncio.sleep(2.5)
            result["video"] = list(frames["video"])
            result["robot_audio"] = frames["audio"]
            result["audio_before_ptt"] = len(received_audio)
            dc.send(json.dumps({"tipo": "ptt", "ligado": True}))
            await asyncio.sleep(1.0)
            result["audio_during_ptt"] = len(received_audio)
            dc.send(json.dumps({"tipo": "ptt", "ligado": False}))
            await asyncio.sleep(0.4)
            n = len(received_audio)
            await asyncio.sleep(0.6)
            result["audio_after_ptt"] = len(received_audio) - n
            for cmd in ({"tipo": "mode", "modo": "teleoperado"}, {"tipo": "expression", "nome": "cry"},
                        {"tipo": "look", "gx": 0.5, "gy": -0.2}, {"tipo": "say", "texto": "Olá!"},
                        {"tipo": "vision", "ligado": False}, {"tipo": "robot_voice", "ligado": True},
                        {"tipo": "comando_invalido"}, {"tipo": "ping", "t": 1.0}):
                dc.send(json.dumps(cmd))
            await asyncio.sleep(0.8)
            result["msgs"] = list(msgs)
            await pc.close()
            await asyncio.sleep(0.8)

    asyncio.run(client())
    check("página da sala servida pelo robô", result.get("page"))
    check("PIN errado é recusado", result.get("wrong_pin"))
    check("WebRTC conecta e o canal de comandos abre", result.get("dc"))
    video = result.get("video", [])
    fps = (len(video) - 1) / (video[-1][2] - video[0][2]) if len(video) > 2 else 0
    check("vídeo do robô chega ao operador (~5 quadros/s, 640×480)", video and video[-1][:2] == (640, 480) and 3 <= fps <= 7)
    check("áudio do ambiente (microfone do robô) chega ao operador", result.get("robot_audio", 0) > 20)
    check("voz do operador só passa enquanto segura “Falar”",
          result.get("audio_before_ptt") == 0 and result.get("audio_during_ptt", 0) > 10 and result.get("audio_after_ptt", 1) == 0)
    if received_audio:
        pcm = received_audio[-1]
        check("voz do operador chega convertida para 24 kHz mono", pcm.dtype == np.int16 and 400 <= len(pcm) <= 520
              and np.abs(pcm).max() > 1000)
    cmds = []
    while not srv.commands.empty():
        cmds.append(srv.commands.get())
    kinds = [k for k, _ in cmds]
    check("comandos chegam ao robô (modo, expressão, olhar, frase, visão, voz)",
          all(k in kinds for k in ("mode", "expression", "look", "say", "vision", "robot_voice")))
    check("comando desconhecido é ignorado", "comando_invalido" not in kinds)
    check("operador conectado/desconectado avisado à interface", ("operator", "Teste") in cmds and ("operator", "") in cmds)
    msgs = result.get("msgs", [])
    check("operador recebe estado da Nuria (~4×/s) e mede latência", any(m.get("tipo") == "estado" for m in msgs)
          and any(m.get("tipo") == "pong" for m in msgs))
    check("microfone do robô liberado ao desconectar", mics and mics[0].stopped)
    srv.stop()
    check("servidor encerra limpo", srv.status == "desligado")

    with tempfile.TemporaryDirectory() as d:
        cert, key = operador.ensure_cert(Path(d))
        check("certificado HTTPS gerado (microfone do navegador exige HTTPS)", cert.exists() and key.exists()
              and b"BEGIN CERTIFICATE" in cert.read_bytes())
    tls = operador.OperatorServer(scene, dict, lambda p: None, port=18766, pin="654321", mic_factory=FakeMic)
    ok = tls.start()

    async def https_get():
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
        async with aiohttp.ClientSession() as http:
            async with http.get("https://127.0.0.1:18766/", ssl=ctx) as r:
                return r.status
    check("sala em HTTPS responde", ok and asyncio.run(https_get()) == 200 and tls.url.startswith("https://"))
    tls.stop()
    gi = (Path(__file__).parent / ".gitignore").read_text(encoding="utf-8")
    check("certificado e configurações locais fora do Git", "certs/" in gi and "*.local.json" in gi)


# ---------------------------------------------------------------------------
def test_modes():
    print("Modos de controle no robô (IA simulada)")
    import test_realtime as T
    import filtro_voz
    h = T.Harness()
    a = h.a
    a.set_operator("Ana")
    check("modo inicial autônomo", a.snapshot().mode == "autonomo")
    a.ask_text("fale bastante")
    h.wait(lambda: a.state in ("thinking", "speaking"))
    a.set_mode("teleoperado")
    check("teleoperado: a IA para na hora", h.wait(lambda: a.state == "idle", 2) and a.snapshot().mode == "teleoperado")
    check("teleoperado: Espaço/texto no robô não acionam a IA", a.press_voice() == "teleop" and not a.ask_text("oi"))
    orig = filtro_voz.synthesize_local
    filtro_voz.synthesize_local = lambda text, rate=24000: (np.sin(np.arange(96000) / 10) * 5000).astype(np.int16)
    time.sleep(0.3)                                       # o envio é assíncrono: espera a fila de saída esvaziar
    n_create = len(h.sent("response.create"))
    a.robot_voice = True
    a.operator_say("Olá, eu sou a Nuria.")
    spoke = h.wait(lambda: a.state == "speaking", 3)
    extra = len(h.sent("response.create")) - n_create
    if not (spoke and extra == 0):
        print("    diagnóstico:", spoke, extra, a.state, h.states[-10:])
    check("teleoperado: frase do operador sai pela voz local (sem custo de API)", spoke and extra == 0)
    check("frase do operador aparece na coluna", a.snapshot().answer == "Olá, eu sou a Nuria." and a.snapshot().question == "(operador)")
    h.idle()
    filtro_voz.synthesize_local = orig
    a.operator_audio(np.full(480, 1000, np.int16))
    check("voz ao vivo do operador faz a Nuria falar (boca segue o som)", a.state == "speaking" and h.player.received >= 480)
    import robot as robot_mod
    rb = robot_mod.Robot((80, 45))
    time.sleep(0.8)
    a.drive_robot(rb, 1.0, "speaking")
    check("soltou “Falar”: volta a Pronta", h.wait(lambda: a.state == "idle", 2))
    a.set_mode("assistido")
    a.operator_say("Bom dia, turma!")
    check("assistido: a IA fala a frase sugerida com a voz dela",
          h.wait(lambda: len(h.sent("response.create")) > n_create
                 and "Bom dia, turma!" in json.dumps(h.sent("response.create")[-1], ensure_ascii=False), 3))
    h.idle()
    a.ask_text("fale bastante")
    h.wait(lambda: a.state in ("thinking", "speaking"))
    a.operator_audio(np.full(480, 1000, np.int16))
    check("voz do operador interrompe a resposta da IA (prioridade do humano)",
          len(h.sent("response.cancel")) >= 1 or a.snapshot().question == "(voz do operador)")
    time.sleep(0.8)
    a.drive_robot(rb, 2.0, "speaking")
    a.set_mode("teleoperado")
    a.set_operator("")
    check("operador saiu: a Nuria volta ao modo autônomo", a.snapshot().mode == "autonomo" and a.snapshot().operator == "")
    h.close()


def test_badge_toggle():
    print("Aviso na tela (liga/desliga)")
    import menu
    import theme
    from renderer import HudInfo
    m = menu.OptionsMenu()
    hud = HudInfo()
    m.activate("remote")
    hud.remote = dict(enabled=True, operator="Ana")
    item = [i for i in m.items(hud, theme.SCHEMES["dark"]) if i[0] == "remote:badge"]
    check("item 'Aviso na tela' na aba Controle remoto, ligado por padrão", len(item) == 1 and item[0][3] == "on")
    hud.remote["badge"] = False
    item = [i for i in m.items(hud, theme.SCHEMES["dark"]) if i[0] == "remote:badge"]
    check("desligado aparece como off", item[0][3] == "off")
    check("tocar mantém a aba aberta", m.activate("remote:badge") == (True, "remote:badge") and m.view == "remote")


def test_app_smoke():
    print("App com a sala do operador ligada")
    import main as app
    cfg = Path(__file__).resolve().parent / "remote.local.json"
    old = cfg.read_text(encoding="utf-8") if cfg.exists() else None
    cfg.write_text(json.dumps({"enabled": True, "port": 18767, "pin": "111222", "badge": False}), encoding="utf-8")
    try:
        code = app.main(["--sim", "--ia", "simulada", "--sem-registro", "--run-seconds", "3"])
        check("app abre a sala e fecha normalmente", code == 0)
        check("preferência do aviso é preservada ao salvar",
              json.loads(cfg.read_text(encoding="utf-8")).get("badge") is False)
    finally:
        if old is None:
            cfg.unlink()
        else:
            cfg.write_text(old, encoding="utf-8")


if __name__ == "__main__":
    test_filter()
    test_server()
    test_modes()
    test_badge_toggle()
    test_app_smoke()
    ok = sum(1 for _, v in RESULTS if v)
    print(f"\n{ok}/{len(RESULTS)} verificações OK (rede local real; câmera, microfone e IA simulados)")
    sys.exit(0 if ok == len(RESULTS) else 1)
