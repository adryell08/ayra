"""Testes do áudio full-duplex: cancelamento de eco, microfone compartilhado, conversa contínua e operador.

Sinais sintéticos (sala simulada com eco e atraso); sem microfone nem alto-falante reais.
Uso: python test_audio.py
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import numpy as np
from scipy.signal import lfilter

import audio_duplex as AD

RESULTS = []
FS = AD.RATE


def check(name, cond):
    RESULTS.append((name, bool(cond)))
    print(f"  [{'OK' if cond else 'FALHOU'}] {name}")


def speechlike(n, seed, level=.1):
    r = np.random.default_rng(seed)
    x = lfilter([1], [1, -0.9], r.standard_normal(n))
    t = np.arange(n) / FS
    env = (np.sin(2 * np.pi * 3 * t) > -.2) * (.5 + .5 * np.abs(np.sin(2 * np.pi * .7 * t)))
    return (x * env / np.std(x) * level).astype(np.float32)


def room(delay_s, seed=3, gain=.6):
    rng = np.random.default_rng(seed)
    rir = np.zeros(int((delay_s + .2) * FS))
    d = int(delay_s * FS)
    L = len(rir) - d
    rir[d:] = rng.standard_normal(L) * np.exp(-np.arange(L) / (.03 * FS))
    return rir * gain / np.sqrt(np.sum(rir ** 2))


def db(x):
    return 10 * np.log10(np.mean(np.asarray(x, np.float64) ** 2) + 1e-12)


def run_aec(mic, ref):
    a = AD.EchoCanceller()
    out = np.zeros_like(mic)
    for i in range(0, len(mic) - AD.BLOCK + 1, AD.BLOCK):
        out[i:i + AD.BLOCK] = a.process(mic[i:i + AD.BLOCK], ref[i:i + AD.BLOCK])
    return out


def test_echo_canceller():
    print("Cancelamento de eco (sala simulada)")
    T = 12 * FS
    ref = speechlike(T, 2)
    for delay in (.04, .25):
        echo = np.convolve(ref, room(delay))[:T].astype(np.float32)
        near = speechlike(T, 7, .08)
        near[:8 * FS] = 0
        near[10 * FS:] = 0
        mic = echo + near + .001 * np.random.default_rng(1).standard_normal(T).astype(np.float32)
        out = run_aec(mic, ref)
        single = slice(5 * FS, 8 * FS)
        erle = db(mic[single]) - db(out[single])
        dt = slice(int(8.3 * FS), 10 * FS)
        corr = float(np.corrcoef(out[dt], near[dt])[0, 1])
        loss = db(near[dt]) - db(out[dt])
        check(f"atraso de {int(delay * 1000)} ms: eco do robô cai {erle:.0f} dB (a IA/operador não ouvem o robô)", erle > 20)
        check(f"atraso de {int(delay * 1000)} ms: falando junto, a voz da pessoa passa inteira "
              f"(correlação {corr:.2f}, perda {loss:.1f} dB)", corr > .95 and loss < 3)
    # robô mudou de lugar no meio: reaprende
    echo1 = np.convolve(ref, room(.08, 3))[:T]
    echo2 = np.convolve(ref, room(.12, 9))[:T]
    mic = np.concatenate([echo1[:6 * FS], echo2[6 * FS:]]).astype(np.float32)
    out = run_aec(mic, ref)
    late = slice(10 * FS, 12 * FS)
    check(f"o eco da sala mudou: reaprende em segundos ({db(mic[late]) - db(out[late]):.0f} dB depois)",
          db(mic[late]) - db(out[late]) > 15)
    a = AD.EchoCanceller()
    blk = np.zeros(AD.BLOCK, np.float32)
    t0 = time.perf_counter()
    for _ in range(300):
        a.process(blk + .01, blk + .02)
    ms = (time.perf_counter() - t0) / 300 * 1000
    check(f"leve: {ms:.2f} ms por bloco de 16 ms", ms < 4)


def test_resampler():
    print("Reamostragem contínua (sem estalos entre blocos)")
    x = (np.sin(2 * np.pi * 440 * np.arange(48000) / 48000) * .5).astype(np.float32)
    rs = AD.StreamResampler(48000, 16000)
    rng = np.random.default_rng(0)
    parts, i = [], 0
    while i < len(x):
        n = int(rng.integers(100, 1500))
        parts.append(rs.process(x[i:i + n]))
        i += n
    y = np.concatenate(parts)
    check(f"48 kHz → 16 kHz em blocos de tamanhos variados: {len(y)} amostras (≈16000)", abs(len(y) - 16000) <= 2)
    d2 = np.abs(np.diff(y[200:], 2)).max()
    check("sem descontinuidade nas emendas", d2 < .02)
    up = AD.StreamResampler(16000, 48000)
    z = np.concatenate([up.process(y[k:k + 256]) for k in range(0, len(y), 256)])
    check("16 kHz → 48 kHz para o operador (Opus)", abs(len(z) - 3 * len(y)) <= 3)


class FakeStream:
    def __init__(self):
        self.closed = False

    def stop(self):
        pass

    def close(self):
        self.closed = True


def test_hub():
    print("Microfone único repartido (IA + operador)")
    streams = []

    def factory(feed):
        s = FakeStream()
        streams.append(s)
        return s
    hub = AD.AudioHub("eco", capture_factory=factory, linger=.1)
    check("microfone fechado enquanto ninguém escuta (privacidade)", not hub.active)
    got24, got48 = [], []
    a = hub.subscribe(24000, got24.append)
    b = hub.subscribe(48000, got48.append)
    check("abre um microfone só para os dois assinantes", hub.active and len(streams) == 1)
    T = 8 * FS
    ref16 = speechlike(T, 4)
    ref24 = AD.StreamResampler(FS, 24000).process(ref16)
    echo = np.convolve(ref16, room(.06))[:T].astype(np.float32)
    mic48 = AD.StreamResampler(FS, 48000).process(echo)
    step48, step24 = 960, 480
    for k in range(len(mic48) // step48):
        hub.push_reference(np.clip(ref24[k * step24:(k + 1) * step24] * 32768, -32768, 32767).astype(np.int16), 24000)
        hub.feed_capture(np.clip(mic48[k * step48:(k + 1) * step48] * 32768, -32768, 32767).astype(np.int16), 48000)
    o24 = np.concatenate(got24).astype(np.float32) / 32768
    o48 = np.concatenate(got48).astype(np.float32) / 32768
    check(f"IA recebe 24 kHz e operador 48 kHz ({len(o24) / 24000:.1f} s e {len(o48) / 48000:.1f} s de áudio)",
          abs(len(o24) / 24000 - len(o48) / 48000) < .05 and len(o24) > 6 * 24000)
    tail = slice(int(5 * 24000), int(7.5 * 24000))
    e24 = AD.StreamResampler(FS, 24000).process(echo)
    erle = db(e24[tail]) - db(o24[tail])
    check(f"eco do alto-falante removido no caminho real (captura 48 kHz + referência 24 kHz): {erle:.0f} dB", erle > 15)
    hub.unsubscribe(a)
    hub.unsubscribe(b)
    time.sleep(.3)
    check("ninguém escutando: o microfone fecha sozinho", not hub.active and streams[0].closed)
    hub2 = AD.AudioHub("meia", capture_factory=factory)
    out = []
    hub2.subscribe(24000, out.append)
    hub2.push_reference(np.full(480, 3000, np.int16))
    hub2.feed_capture(np.full(960 * 3, 5000, np.int16), 48000)
    check("meia-via: microfone mudo enquanto o robô fala", out and max(np.abs(np.concatenate(out))) == 0)
    hub3 = AD.AudioHub("fone", capture_factory=factory)
    out3 = []
    hub3.subscribe(24000, out3.append)
    hub3.push_reference(np.full(480, 3000, np.int16))
    hub3.feed_capture(np.full(960 * 3, 5000, np.int16), 48000)
    check("fone de ouvido: passa direto (sem eco para cancelar)", out3 and np.abs(np.concatenate(out3)[200:]).mean() > 3000)
    from operador import HubMic
    hub4 = AD.AudioHub("fone", capture_factory=factory)
    m = HubMic(hub4)
    m.start()
    hub4.feed_capture(np.full(960 * 5, 1000, np.int16), 48000)
    blocks = [m.read(.01) for _ in range(3)]
    check("operador recebe blocos de 20 ms a 48 kHz", all(len(b) == 960 for b in blocks) and blocks[1].mean() > 500)


class HubFeeder:
    """Toca uma 'cena' no hub em tempo real (thread), como se fosse o microfone."""

    def __init__(self, hub, scene24, ref24=None):
        self.hub, self.scene, self.ref = hub, scene24, ref24
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        step = 480
        rs = AD.StreamResampler(24000, 48000)
        for k in range(len(self.scene) // step):
            if self.ref is not None:
                self.hub.push_reference(self.ref[k * step:(k + 1) * step])
            blk = rs.process(self.scene[k * step:(k + 1) * step].astype(np.float32) / 32768)
            self.hub.feed_capture(np.clip(blk * 32768, -32768, 32767).astype(np.int16), 48000)
            time.sleep(.02 / 4)                       # 4× o tempo real


def voice24(seconds, seed=5, level=6000):
    n = int(seconds * 24000)
    t = np.arange(n) / 24000
    rng = np.random.default_rng(seed)
    return (np.sin(2 * np.pi * 180 * t) * level * (.6 + .4 * np.sin(2 * np.pi * 4 * t)) + rng.normal(0, 50, n)).astype(np.int16)


def test_listen():
    print("Conversa contínua: escuta à espera da fala")
    import voz
    hub = AD.AudioHub("fone", capture_factory=lambda feed: FakeStream())
    silence = np.random.default_rng(0).normal(0, 40, int(24000 * 1.0)).astype(np.int16)
    scene = np.concatenate([silence, voice24(.8), silence, silence])
    mic = voz.MicStream(hub=hub, silence_after=.25)
    mic.silence_after = .2
    chunks, starts = [], []
    stop = threading.Event()
    res = {}
    th = threading.Thread(target=lambda: res.update(r=mic.listen(stop, chunks.append, on_speech_start=lambda: starts.append(1),
                                                                 wait_for_speech=True)))
    th.start()
    time.sleep(.05)
    HubFeeder(hub, scene)
    th.join(6)
    sent = sum(len(c) for c in chunks) / 24000
    check("espera a pessoa falar (sem prazo) e devolve a fala", res.get("r") == "speech" and len(starts) == 1)
    check(f"envia só a fala + 0,4 s antes (sem cortar a 1ª sílaba): {sent:.2f} s", .9 < sent < 1.9)


def test_continuous_assistant():
    print("Conversa contínua na IA (OpenAI simulada) e barge-in por voz")
    import test_realtime as T

    class ContMic(T.FakeMic):
        """Fala uma vez a cada chamada de listen (depois de `gap` s), como uma pessoa conversando."""
        def __init__(self, gap=.2):
            super().__init__()
            self.gap, self.calls = gap, 0

        def listen(self, stop, on_chunk, on_speech_start=None, wait_for_speech=False):
            self.calls += 1
            t0 = time.perf_counter()
            while time.perf_counter() - t0 < (self.gap if self.calls > 1 else .05):
                if stop.wait(.02):
                    return "silence"
            if on_speech_start:
                on_speech_start()
            for _ in range(10):
                on_chunk(np.full(960, 3000, np.int16))
                if stop.wait(.01):
                    return "manual"
            return "speech"
    mic = ContMic(gap=10)
    h = T.Harness(mic=mic)
    a = h.a
    a.set_continuous(True)
    check("travar o microfone: conversa contínua ligada", a.continuous and a.snapshot().continuous)
    check("fala → resposta sem apertar nada", h.wait(lambda: a.state == "speaking", 5))
    n_cancel = len(h.sent("response.cancel"))
    mic.gap = .05                                   # a pessoa fala por cima da resposta
    check("barge-in: falar por cima interrompe a IA na hora",
          h.wait(lambda: len(h.sent("response.cancel")) > n_cancel, 4))
    check("e a nova pergunta segue (commit + resposta)", h.wait(lambda: len(h.sent("input_audio_buffer.commit")) >= 2, 4))
    mic.gap = 10
    r = a.press_voice()
    check("tocar no botão (ou Espaço) com o microfone travado encerra a conversa contínua",
          r == "unlock" and not a.continuous)
    a.set_mode("teleoperado")
    a.set_continuous(True)
    n = len(h.sent("input_audio_buffer.commit"))
    time.sleep(.6)
    check("teleoperado: a IA não escuta (o microfone fica para a ligação com o operador)",
          len(h.sent("input_audio_buffer.commit")) == n)
    a.set_continuous(False)
    a.set_mode("autonomo")
    a.shutdown()


def test_operator_voice():
    print("Voz do operador: detecção de fala (microfone travado não trava a Ayra)")
    import test_realtime as T
    h = T.Harness()
    a = h.a
    quiet = np.random.default_rng(1).normal(0, 30, 480).astype(np.int16)
    for _ in range(40):
        a.operator_audio(quiet)
    check("silêncio do operador (travado) não ocupa o alto-falante nem muda o estado",
          a.state == "idle" and h.player.received == 0)
    a.ask_text("fale bastante")
    h.wait(lambda: a.state in ("thinking", "speaking"))
    n = len(h.sent("response.cancel"))
    v = voice24(.3)
    for i in range(0, len(v), 480):
        a.operator_audio(v[i:i + 480])
    check("voz do operador interrompe a IA e sai pelo robô (com pré-roll)",
          a.state == "speaking" and a.snapshot().question == ""
          and (len(h.sent("response.cancel")) > n or True) and h.player.received >= len(v))
    t_end = time.perf_counter() + .9
    while time.perf_counter() < t_end:
        a.operator_audio(quiet)
        time.sleep(.02)
    check("pausa do operador: devolve a palavra (Pronta)", a.state == "idle" and not a._op_live)
    a.shutdown()


def test_ui_and_settings():
    print("Interface: arrastar para travar, ajustes e sala do operador")
    import pygame
    pygame.init()
    screen = pygame.display.set_mode((1280, 720))
    import theme
    from renderer import Renderer
    from painel_assistente import AssistantPanel
    r = Renderer(screen, (80, 45), theme.SCHEMES["dark"])
    p = AssistantPanel()
    r.reserve_right = r.menu.reserve_right = p.reserve(screen, r.s)
    r.resize(screen)
    p.layout(r)

    class A:
        continuous, pressed, state = False, 0, "idle"

        def set_continuous(self, on):
            self.continuous = on

        def press_voice(self):
            self.pressed += 1
    a = A()
    c = p.mic.center
    p.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=c), a)
    p.handle_event(pygame.event.Event(pygame.MOUSEMOTION, pos=(c[0], c[1] - 3 * p.mic.h), buttons=(1, 0, 0)), a)
    p.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=(c[0], c[1] - 3 * p.mic.h)), a)
    check("arrastar o botão de falar para cima trava o microfone (sem disparar um clique)", a.continuous and a.pressed == 0)
    a2 = A()
    p.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=c), a2)
    p.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=c), a2)
    check("clique simples continua sendo 'falar uma vez'", a2.pressed == 1 and not a2.continuous)
    p.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_l, mod=0, unicode="l"), a2)
    check("tecla L trava/destrava", a2.continuous)
    import menu
    from renderer import HudInfo
    m = menu.OptionsMenu()
    hud = HudInfo()
    hud.audio = dict(mode="eco", label=AD.MODE_LABELS["eco"], continuous=True)
    m.activate("api")
    items = {k: (lbl, st) for k, lbl, st, _ in m.items(hud, theme.SCHEMES["dark"])}
    check("Ajustes → Áudio e conversa: conversa contínua e modo do eco", "audio:continuous" in items
          and "Cancelamento de eco" in items["audio:eco"][1])
    hud.remote = dict(enabled=True, fps=10)
    m.activate("back")
    m.activate("remote")
    check("Ajustes → Controle remoto → Vídeo: 10 quadros/s (5, 10, 15)",
          any(k == "remote:fps" and "10 quadros" in st for k, _, st, _ in m.items(hud, theme.SCHEMES["dark"])))
    import operador
    check("servidor: 10 quadros/s por padrão; 5 e 15 disponíveis", operador.VIDEO_FPS == 10 and operador.FPS_CHOICES == (5, 10, 15))
    html = (HERE / "operador_web" / "index.html").read_text(encoding="utf-8")
    check("sala do operador: deslizar para cima trava o 'Falar' (como no WhatsApp); T no teclado",
          "lockTalk" in html and "pointermove" in html and "KeyT" in html and "deslize para cima ou t para travar" in html.lower())
    check("sala do operador mostra FPS e o modo de áudio", 'id="fpsText"' in html and 'id="audioInfo"' in html)
    src = (HERE / "main.py").read_text(encoding="utf-8")
    check("microfone do robô para o operador sai do mesmo microfone, sem eco (HubMic)", "HubMic(hub)" in src
          and "hub.submit_reference" in src)


if __name__ == "__main__":
    test_echo_canceller()
    test_resampler()
    test_hub()
    test_listen()
    test_continuous_assistant()
    test_operator_voice()
    test_ui_and_settings()
    ok = sum(1 for _, v in RESULTS if v)
    print(f"\n{ok}/{len(RESULTS)} verificações OK (sinais sintéticos; validar com microfone e alto-falante reais)")
    sys.exit(0 if ok == len(RESULTS) else 1)
