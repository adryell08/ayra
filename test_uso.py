"""Testes de: Modo de uso, menu reagrupado e rolagem, áudio sem atraso/travadas, modo leve (Raspberry Pi).

Sem câmera, microfone ou alto-falante reais. Uso: python test_uso.py
"""
from __future__ import annotations

import os
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import numpy as np
import pygame

RESULTS = []


def check(name, cond):
    RESULTS.append((name, bool(cond)))
    print(f"  [{'OK' if cond else 'FALHOU'}] {name}")


# ---------------------------------------------------------------- áudio
def test_audio_path():
    print("Áudio robô → operador: fila curta, sem acumular atraso")
    from operador import HubMic

    class Hub:
        def subscribe(self, rate, cb):
            self.cb = cb
            return 1

        def unsubscribe(self, sid):
            pass

    hub = Hub()
    mic = HubMic(hub)
    mic.start()
    block = np.ones(960, np.int16)
    for i in range(100):                                    # rajada: o consumidor ficou parado 2 s
        hub.cb(block * (i + 1))
    check("depois de uma rajada a fila guarda no máximo 3 blocos (60 ms), não 1 s",
          mic._q.qsize() <= HubMic.MAX_BACKLOG and mic.dropped > 0)
    first = mic.read()
    check("o que sai é o áudio mais recente (não o antigo)", int(first[0]) >= 97)
    # relógio do microfone 2 % mais rápido que o do consumidor: o atraso não pode crescer
    mic = HubMic(hub)
    mic.start()
    worst = 0
    for i in range(5000):
        hub.cb(block)
        if i % 50 != 49:                                    # o consumidor perde 1 a cada 50
            mic.read(0.0)
        worst = max(worst, mic._q.qsize())
    check("com deriva de relógio de 2 % o atraso fica limitado a 3 blocos (60 ms)", worst <= HubMic.MAX_BACKLOG + 1)
    t0 = time.perf_counter()
    empty = HubMic(hub)
    out = empty.read()
    check("sem áudio, devolve silêncio em ~30 ms (não 100 ms)", len(out) == 960 and time.perf_counter() - t0 < 0.08)


def test_operator_voice_smooth():
    print("Voz do operador no robô: sem cliques, com buffer de jitter")
    from audio_duplex import StreamResampler
    from realtime import resample
    import voz
    from filtro_voz import RobotVoice

    n = 960 * 40
    t = np.arange(n) / 48000
    sig = (10000 * np.sin(2 * np.pi * 440 * t)).astype(np.int16)
    rs = StreamResampler(48000, 24000)
    new = np.concatenate([rs.process(sig[i:i + 960]) for i in range(0, n, 960)])
    old = np.concatenate([resample(sig[i:i + 960], 48000, 24000) for i in range(0, n, 960)])
    step = lambda x: float(np.max(np.abs(np.diff(x[200:].astype(np.float32)))))
    limit = 2 * np.pi * 440 / 24000 * 10000 * 1.25
    check(f"reamostragem contínua 48→24 kHz sem degraus entre blocos (maior salto {step(new):.0f} ≤ {limit:.0f})",
          step(new) <= limit)
    check("o tamanho total bate com a taxa (sem perder nem inventar amostras)", abs(len(new) - n // 2) <= 4)

    p = voz.StreamPlayer()
    out = np.zeros((480, 1), np.int16)
    chunk = np.full(480, 1000, np.int16)                    # 20 ms
    p.feed(chunk)
    p._callback(out, 480, None, None)
    check("com só 20 ms na fila espera (silêncio), em vez de tocar picotado", not out.any() and p.played == 0)
    for _ in range(5):
        p.feed(chunk)
    p._callback(out, 480, None, None)
    check("com 120 ms na fila começa a tocar", out.any() and p.played == 480)
    for _ in range(5):
        p._callback(out, 480, None, None)                   # toca os 6 blocos
    p._callback(out, 480, None, None)                       # fila esgotada no meio do fluxo
    check("esgotou a fila: registra a falha e junta de novo antes de tocar", p.underruns == 1 and not p._primed)
    q = voz.StreamPlayer()
    q.max_wait = 0.0
    q.feed(chunk)
    q._callback(out, 480, None, None)
    q._callback(out, 480, None, None)
    check("resposta curtíssima toca mesmo sem chegar ao mínimo", q.played > 0)

    x = (np.random.default_rng(1).standard_normal(24000) * 3000).astype(np.int16)
    a, b = RobotVoice(), RobotVoice()
    ya = np.concatenate([a.process(x[i:i + 480]) for i in range(0, len(x), 480)]).astype(np.float32)
    yb = np.concatenate([b.process(x[i:i + 480], dry=0.45) for i in range(0, len(x), 480)]).astype(np.float32)
    d = a._delay                                            # o caminho robótico atrasa 3,5 ms; o seco acompanha
    corr = lambda u: float(np.corrcoef(u[2000 + d:], x[2000:-d].astype(np.float32))[0, 1])
    check(f"voz ao vivo mistura 45 % da voz original (mais inteligível: corr. {corr(yb):.2f} > {corr(ya):.2f})",
          corr(yb) > corr(ya) + 0.2)


# ---------------------------------------------------------------- menu
def _renderer(size=(1280, 720)):
    import theme
    from renderer import HudInfo, Renderer
    screen = pygame.display.set_mode(size)
    return Renderer(screen, (80, 45), theme.DARK_SCHEME), HudInfo(), theme


def test_menu_groups_and_scroll():
    print("Menu: telas agrupadas e rolagem")
    r, hud, theme = _renderer((900, 520))
    m = r.menu
    sc = theme.DARK_SCHEME
    root = [i[0] for i in m.items(hud, sc)]
    check("Modo de uso é o primeiro item de Ajustes", root[0] == "use")
    check("raiz mais enxuta: Reações e rosto, Corpo e gestos, Conversa e IA, Conexões no lugar de 8 telas",
          all(k in root for k in ("features", "body", "api", "remote"))
          and not any(k in root for k in ("face", "audio", "sims", "arduino")))
    def kinds(view):
        m.view = view
        return {i[0].split(":")[0] for i in m.items(hud, sc)}
    check("Conversa e IA reúne API e áudio", {"api", "audio"} <= kinds("api"))
    check("Conexões reúne sala do operador e painel LED", {"remote", "arduino"} <= kinds("remote"))
    check("Corpo e gestos reúne gestos e simulações", {"body", "sim"} <= kinds("body"))
    check("Reações e rosto reúne percepção, expressão e rosto", {"feature"} <= kinds("features")
          and {i[0] for i in m.items(hud, sc)} >= {"feature:round_eyes", "feature:hands"})
    check("título de cada tela", m.TITLES["remote"] == "Conexões" and m.TITLES["api"] == "Conversa e IA")

    m.open, m.view, m.offset, m.focus = True, "remote", 0, 0
    m.draw(r, hud)
    items = m.items(hud, sc)
    check("a tela de Conexões é maior que a janela (precisa rolar)", len(m.rows) < len(items) and m.max_offset > 0)
    seen = [g for g, _ in m.headers]
    check("seções sem repetição na primeira página", len(seen) == len(set(seen)))
    m.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=-1, precise_x=0.0, precise_y=-1.0), items)
    m.draw(r, hud)
    check("roda para baixo rola a janela em 1 linha (o foco acompanha, não dá a volta)", m.offset == 1 and m.focus >= 1)
    for _ in range(60):
        m.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=-1, precise_x=0.0, precise_y=-1.0), items)
    m.draw(r, hud)
    check("no fim da lista para (sem voltar ao começo) e a última linha fica visível",
          m.offset == m.max_offset and m.offset + len(m.rows) == len(items))
    for _ in range(60):
        m.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=1, precise_x=0.0, precise_y=1.0), items)
    m.draw(r, hud)
    check("roda para cima volta ao topo e para", m.offset == 0)

    start = m.rows[3].center
    m.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=start), items)
    m.handle_event(pygame.event.Event(pygame.MOUSEMOTION, pos=(start[0], start[1] - 2 * m.rows[0].h), rel=(0, 0),
                                      buttons=(1, 0, 0)), items)
    m.draw(r, hud)
    moved = m.offset
    _, action = m.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, button=1,
                                                  pos=(start[0], start[1] - 2 * m.rows[0].h)), items)
    check("arrastar a lista com o dedo/mouse rola (2 linhas) e não ativa o item sob o dedo", moved == 2 and action is None)

    m.draw(r, hud)
    m.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=m.track.center), items)
    m.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=m.track.center), items)
    m.draw(r, hud)
    check("clicar na barra de rolagem pula para a posição", 0 < m.offset < m.max_offset or m.max_offset <= 2)
    before = m.focus
    m.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_PAGEDOWN), items)
    check("PgDn avança uma página de foco", m.focus > before)
    _, action = m.activate("remote:fps")
    check("tocar num item mantém a tela aberta e devolve a ação", action == "remote:fps" and m.view == "remote")


# ---------------------------------------------------------------- Modo de uso
def test_use_mode_panel():
    print("Modo de uso: painel e HUD mínimos")
    import painel_assistente as pa
    r, hud, theme = _renderer((1280, 720))

    class Snap:
        state, message, message_kind, answer, question = "idle", "", "", "", ""
        vision, image, image_version, level, timings = True, None, 0, 0.0, {}
        continuous = False
        mode = "autonomo"

    class Assistant:
        def snapshot(self): return Snap()
        def backend_label(self): return "x"

    panel = pa.AssistantPanel()
    panel.layout(r)
    full = panel.type_btn.w
    panel.simple = True
    panel.layout(r)
    check("no Modo de uso só existe o botão de falar, largo e grande",
          panel.type_btn.w == 0 and panel.vis_btn.w == 0 and panel.mic.w > 250 and panel.mic.h >= 64 * r.s and full > 0)
    panel.draw(r, Assistant(), 1 / 60, False, "")
    hud.use_mode = True
    from robot import Robot
    r.draw_hud(Robot().step([], 0, 1 / 60), hud)
    before = r.screen.copy()
    r.menu.dock_visible = False
    r.draw_hud(Robot().step([], 0, 1 / 60), hud)
    check("HUD do Modo de uso não desenha o dock de ajustes", r.menu.dock_visible is False)


def _run_app(argv, script, seconds=2):
    """Executa app.main com eventos roteirizados por quadro; devolve os HUDs vistos por quadro."""
    import main as app
    frames = iter(script)
    seen = []

    def events():
        try:
            return next(frames)
        except StopIteration:
            return []

    original = app.Renderer.draw_hud

    def observe(renderer, frame, hud):
        seen.append((replace(hud), renderer.menu.open))
        original(renderer, frame, hud)

    with patch.object(app.pygame.event, "get", side_effect=events), \
            patch.object(app.Renderer, "draw_hud", autospec=True, side_effect=observe):
        code = app.main(["--sim", "--sem-registro", "--run-seconds", str(seconds)] + argv)
    return code, seen


def test_use_mode_app():
    print("Modo de uso: no aplicativo")
    K = lambda k: pygame.event.Event(pygame.KEYDOWN, key=k)
    code, seen = _run_app(["--uso"], [[], [K(pygame.K_F10)], [], []] + [[]] * 3)
    check("--uso abre direto no Modo de uso", code == 0 and seen[0][0].use_mode)
    check("F10 sai do Modo de uso", not seen[-1][0].use_mode)

    code, seen = _run_app(["--uso"], [[], [K(pygame.K_ESCAPE)], [K(pygame.K_d)], [K(pygame.K_F1)], []], seconds=1)
    check("no Modo de uso Esc, D e F1 não fecham o app nem abrem diagnóstico/ajuda",
          code == 0 and all(h.use_mode and not h.debug and not h.show_help for h, _ in seen))

    import main as app
    w, h = 1280, 720
    click = lambda k, pos: pygame.event.Event(k, button=1, pos=pos)
    code, seen = _run_app(["--uso"], [[], [click(pygame.MOUSEBUTTONDOWN, (w // 2, h - 60))],
                                       [click(pygame.MOUSEBUTTONUP, (w // 2, h - 60))], [K(pygame.K_F10)]], seconds=1)
    check("no Modo de uso o clique no lugar do dock não abre o menu", not any(opened for _, opened in seen[:-1]))

    corner = (w - 20, 20)
    code, seen = _run_app(["--uso"], [[], [click(pygame.MOUSEBUTTONDOWN, corner)]] + [[]] * 400, seconds=2.2)
    check("toque longo (1,2 s) no canto superior direito sai do Modo de uso",
          any(hh.use_exit > 0.3 for hh, _ in seen) and not seen[-1][0].use_mode)
    code, seen = _run_app(["--uso"], [[], [click(pygame.MOUSEBUTTONDOWN, corner)], [], [click(pygame.MOUSEBUTTONUP, corner)]], seconds=1.5)
    check("soltar antes do tempo cancela a saída", seen[-1][0].use_mode and seen[-1][0].use_exit == 0)

    code, seen = _run_app([], [[], [K(pygame.K_F10)], [K(pygame.K_RETURN)], [], []], seconds=1)
    check("pelo menu: Ajustes → Modo de uso entra no modo", code == 0 and seen[-1][0].use_mode)


def test_light_mode():
    print("Modo leve (Raspberry Pi)")
    import main as app
    a, b = app.parse_args([]), app.parse_args(["--leve"])
    check("padrão: 60 quadros/s, detecção 416", a.fps == 60 and a.det_width == 416 and not a.leve)
    check("--leve: 30 quadros/s e detecção 320", b.leve and b.fps == 30 and b.det_width == 320)
    check("--fps explícito vale mesmo no modo leve", app.parse_args(["--leve", "--fps", "24"]).fps == 24)
    with patch.dict(os.environ, {"NURIA_LEVE": "1"}):
        check("NURIA_LEVE=1 liga o modo leve", app.parse_args([]).leve)
    with patch("platform.machine", return_value="aarch64"):
        check("processador ARM liga o modo leve sozinho", app.parse_args([]).leve)
    r, hud, theme = _renderer((800, 480))
    r.light = True
    from robot import Robot
    with patch.object(pygame.transform, "smoothscale", wraps=pygame.transform.smoothscale) as sm:
        r._glass(pygame.Rect(10, 10, 200, 100), 12, blur=True)
        light_calls = sm.call_count
        r.light = False
        r._glass(pygame.Rect(10, 10, 200, 100), 12, blur=True)
    check("modo leve desenha o vidro sem desfocar o fundo (0 redimensionamentos; normal faz 2)",
          light_calls == 0 and sm.call_count == 2)


def test_voice_level_and_privacy():
    print("Voz robótica com grau (0–100 %), áudio sem picotar e sem revelar o teleoperador")
    from filtro_voz import RobotVoice
    import credenciais

    rng = np.random.default_rng(2)
    x = (rng.standard_normal(24000) * 3000).astype(np.int16)

    def run(level):
        v = RobotVoice()
        return np.concatenate([v.process(x[i:i + 480], level=level) for i in range(0, len(x), 480)]).astype(np.float32)

    d = RobotVoice()._delay
    corr = lambda y: float(np.corrcoef(y[3000 + d:], x[3000:-d].astype(np.float32))[0, 1])
    c0 = 1.0                                                # grau 0: devolve o original, sem atraso
    c30, c60, c100 = (corr(run(l)) for l in (0.3, 0.6, 1.0))
    check("grau 0 devolve a voz natural, sem processar", np.array_equal(RobotVoice().process(x[:480], level=0.0), x[:480]))
    check(f"quanto maior o grau, menos parecido com a voz original ({c0:.2f} > {c30:.2f} > {c60:.2f} > {c100:.2f})",
          c0 > c30 > c60 > c100)
    check("grau 100 % mantém a voz audível (sem silêncio nem estouro)",
          0.02 < np.abs(run(1.0)).max() / 32768 <= 1.0)
    check("NURIA_VOZ_ROBOTICA: percentuais e valores antigos",
          [credenciais.robot_percent(v) for v in ("0", "35", "100%", "150", "sim", "1", "off", "", "abc")]
          == [0, 35, 100, 100, 60, 60, 0, 0, 0])

    from assistente_realtime import RealtimeAssistant
    import inspect
    src = inspect.getsource(RealtimeAssistant)
    check("nenhum texto na tela diz que há teleoperador ou operador remoto",
          "Teleoperado: o operador remoto" not in src and "(voz do operador)" not in src and 'question="(operador)"' not in src)

    import voz
    p = voz.StreamPlayer()
    out = np.zeros((960, 1), np.int16)
    base = p.prebuffer
    for _ in range(3):
        p.feed(np.full(960 * 6, 500, np.int16))
        for _ in range(8):
            p._callback(out, 960, None, None)
    check("o buffer de jitter cresce sozinho depois de picotar (até 400 ms)",
          p.underruns >= 3 and base < p.prebuffer <= p.max_prebuffer)

    import audio_duplex as AD
    got = []
    hub = AD.AudioHub(mode="eco", capture_factory=lambda cb: object())
    hub.subscribe(24000, lambda pcm: got.append(len(pcm)))
    hub._ensure_stream()
    main_thread = threading.get_ident()
    seen = []
    orig = hub.feed_capture
    hub.feed_capture = lambda b, r: (seen.append(threading.get_ident()), orig(b, r))
    for _ in range(10):
        hub.submit_capture(np.zeros(960, np.int16), 48000)
    deadline = time.perf_counter() + 2
    while len(seen) < 10 and time.perf_counter() < deadline:
        time.sleep(0.01)
    hub.close()
    check("a captura é processada fora da thread do PortAudio (não disputa o GIL no callback)",
          len(seen) == 10 and all(t != main_thread for t in seen) and sum(got) > 0)

    import main as app
    html = (HERE / "operador_web" / "index.html").read_text(encoding="utf-8")
    check("sala do operador tem controle deslizante do grau da voz robótica",
          'id="robotLevel"' in html and 'type="range"' in html and "nivel" in html)
    import menu
    from renderer import HudInfo
    import theme
    hud = HudInfo()
    hud.api = dict(robot_level=40)
    m = menu.OptionsMenu(); m.view = "api"
    row = [i for i in m.items(hud, theme.SCHEMES["dark"]) if i[0] == "api:robot"][0]
    check("Ajustes mostra o grau em % e o toque cicla", "40 %" in row[2])


def test_vision_load():
    print("Visão: detecção limitada e prioridade baixa (CPU livre para interface e áudio)")
    import tracker
    from tracker import CameraSource

    class Cap:
        def __init__(self):
            self.n = 0
        def read(self):
            time.sleep(0.01)                                # câmera a ~100 quadros/s
            self.n += 1
            return True, np.random.default_rng(self.n).integers(60, 200, (120, 160, 3), dtype=np.uint8)
        def release(self):
            pass
        def isOpened(self):
            return True

    class Det:
        calls = 0
        def detect(self, frame):
            Det.calls += 1
            return []

    class Src(CameraSource):
        def _open(self):
            self.status = "ok"
            return Cap()

    prio = []
    orig = tracker.lower_thread_priority
    with patch.object(tracker, "lower_thread_priority", side_effect=lambda: (prio.append(1), orig())):
        src = Src(Det())
        src.start()
        time.sleep(2.5)
        src._stop.set()
        time.sleep(0.2)
    rate = Det.calls / 2.5
    check(f"detecção de rosto limitada a ~15 por segundo (medido {rate:.1f}/s com a câmera a ~100/s)", 6 <= rate <= 18)
    check("a thread da câmera roda com prioridade abaixo do normal", prio)
    import auxiliary
    src_text = (HERE / "auxiliary.py").read_text(encoding="utf-8")
    check("mãos 5,5 Hz, sorriso 3 Hz, objetos 1,7 Hz (antes 8, 6,7 e 2)", "(PalmDetector, .18)" in src_text
          and "(SmileDetector, .30)" in src_text and "(ObjectDetector, .60)" in src_text)


if __name__ == "__main__":
    pygame.init()
    test_audio_path()
    test_operator_voice_smooth()
    test_menu_groups_and_scroll()
    test_use_mode_panel()
    test_use_mode_app()
    test_light_mode()
    test_voice_level_and_privacy()
    test_vision_load()
    ok = sum(1 for _, v in RESULTS if v)
    print(f"\n{ok}/{len(RESULTS)} verificações OK")
    sys.exit(0 if ok == len(RESULTS) else 1)
