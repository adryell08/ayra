#!/usr/bin/env python3
"""Testes automáticos do Robô-Olhos — rodam SEM câmera e SEM janela.

    python selftest.py            # roda tudo e gera imagens em ./previews
    python selftest.py --no-png   # só os testes

Valida: contraste (HIG), IDs persistentes, lógica de atenção com vários rostos,
dinâmica do olhar, expressões, piscadas, detector YuNet (se houver) e desempenho.
"""
from __future__ import annotations

import argparse
import math
import os
import random
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np

import theme
from attention import AttentionConfig
from expressions import ARCH, EXPRESSIONS, KEY_ORDER, Blinker, ExpressionEngine
from face import render_field
from gaze import GazeController
from robot import Robot
from tracker import Detection, FaceTracker, SimulatedSource

ROOT = Path(__file__).resolve().parent
RESULTS = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok)))
    print(f"  [{'OK' if ok else 'FALHA'}] {name}" + (f"  — {detail}" if detail else ""))


def det(cx, cy, size) -> Detection:
    h = size
    w = h * 0.78
    return Detection(cx - w / 2, cy - h / 2, w, h, cx, cy - h * 0.12, 0.95)


def simulate(robot: Robot, seconds: float, faces_fn, t0: float = 0.0, hz: int = 60, collect=None):
    """Avança o robô; `faces_fn(t)` -> lista de (cx, cy, tamanho). Detecções a 30 Hz."""
    dt = 1.0 / hz
    n = int(round(seconds * hz))
    t = t0
    last = None
    for i in range(n):
        t += dt
        dets = None
        if i % (hz // 30) == 0:
            dets = [det(*f) for f in faces_fn(t)]
        last = robot.step(dets, t, dt)
        if collect is not None:
            collect(t, last)
    return t, last


def kinds(frame):
    return [e.kind for e in frame.attention.events]


# --------------------------------------------------------------------------
def test_theme():
    print("Tema / acessibilidade (HIG)")
    for sc in theme.SCHEMES.values():
        opaque_chip = theme.mix(sc.panel, sc.chip, sc.chip_alpha / 255.0)
        check(f"{sc.label}: texto primário >= 4,5:1", theme.contrast(sc.text, opaque_chip) >= 4.5,
              f"{theme.contrast(sc.text, opaque_chip):.1f}:1")
        check(f"{sc.label}: texto secundário >= 4,5:1", theme.contrast(sc.text_dim, opaque_chip) >= 4.5,
              f"{theme.contrast(sc.text_dim, opaque_chip):.1f}:1")
        check(f"{sc.label}: olhos x painel >= 3:1 (gráfico)", theme.contrast(sc.led_on, sc.panel) >= 3.0,
              f"{theme.contrast(sc.led_on, sc.panel):.1f}:1")
        for emo in ("happy", "surprised", "sad", "angry"):
            c = theme.contrast(theme.mix(sc.led_on, theme.emotion_tint(sc, emo), 0.92), sc.panel)
            check(f"{sc.label}: olhos '{emo}' x painel >= 3:1", c >= 3.0, f"{c:.1f}:1")
    mn = min(v[0] for v in theme.TEXT_ROLES.values())
    check("Texto nunca abaixo de 10 pt", mn >= 10)


def test_tracker():
    print("Rastreador (IDs persistentes)")
    tr = FaceTracker()
    ids_a, ids_b = set(), set()
    for i in range(240):                                   # duas pessoas se cruzando, 30 Hz
        t = i / 30
        a = det(0.2 + 0.6 * t / 8, 0.45, 0.30)
        b = det(0.8 - 0.6 * t / 8, 0.50, 0.22)
        tr.update([a, b], t)
        for k in tr.tracks:
            if not k.confirmed:
                continue
            (ids_a if abs(k.cy - 0.45) < 0.03 and k.h > 0.26 else ids_b).add(k.id)
    check("Dois rostos que se cruzam mantêm 2 IDs fixos", len(ids_a) == 1 and len(ids_b) == 1 and ids_a != ids_b,
          f"A={ids_a} B={ids_b}")

    tr = FaceTracker()
    for i in range(30):
        tr.update([det(0.5, 0.45, 0.3)], i / 30)
    tid = tr.tracks[0].id
    for i in range(30, 45):                                # 0,5 s de oclusão
        tr.update([], i / 30)
    for i in range(45, 60):
        tr.update([det(0.52, 0.45, 0.3)], i / 30)
    check("Oclusão curta (0,5 s) mantém o mesmo ID", len(tr.tracks) == 1 and tr.tracks[0].id == tid)

    tr = FaceTracker()
    tr.update([det(0.3, 0.5, 0.3)], 0.0)
    for i in range(1, 20):
        tr.update([], i / 30)
    check("Falso positivo de 1 quadro nunca vira rosto", not any(k.confirmed for k in tr.tracks))

    tr = FaceTracker()
    ev = []
    for i in range(20):
        ev += tr.update([det(0.5, 0.5, 0.3)], i / 30)
    for i in range(20, 90):
        ev += tr.update([], i / 30)
    check("Rosto que some gera evento 'lost'", any(e.kind == "lost" for e in ev))


def test_attention():
    print("Atenção com vários rostos")
    cfg = AttentionConfig()
    r = Robot(seed=7, cfg=cfg)
    events = []

    def col(t, f):
        events.extend((round(t, 2), e.kind, e.track_id) for e in f.attention.events)

    # 1) sala vazia -> ocioso; depois chega A
    t, f = simulate(r, 3.5, lambda t: [], collect=col)
    check("Sem ninguém: modo ocioso (olhar passeando)", f.attention.mode == "idle" and any(e[1] == "idle" for e in events))
    t, f = simulate(r, 1.5, lambda t: [(0.75, 0.45, 0.30)], t0=t, collect=col)
    a_id = f.attention.target_id
    check("Primeira pessoa vira o alvo e é cumprimentada", a_id is not None and any(e[1] == "greet" for e in events))
    check("Olhar vai para o lado certo (rosto à direita => gx > 0)", f.gaze[0] > 0.3, f"gx={f.gaze[0]:.2f}")
    t1 = t
    walk = lambda t: [(max(0.15, 0.75 - 0.6 * (t - t1) / 1.0), 0.40, 0.30)]   # anda 1 s para a esquerda
    t, f = simulate(r, 2.5, walk, t0=t)
    check("Rosto andando para a esquerda => olhar à esquerda", f.gaze[0] < -0.4, f"gx={f.gaze[0]:.2f}")
    check("Pessoa que anda mantém o mesmo ID", f.attention.target_id == a_id, f"{f.attention.target_id} vs {a_id}")

    # 2) entra B (mais longe): reação, olhada de relance e SEM roubar o alvo
    events.clear()
    both = lambda t: [(0.15, 0.40, 0.30), (0.75, 0.50, 0.20)]
    t, f = simulate(r, 2.5, both, t0=t, collect=col)
    kinds_seen = {e[1] for e in events}
    check("Rosto novo gera reação ('new_face') e olhada de relance", {"new_face", "glance"} <= kinds_seen, str(sorted(kinds_seen)))
    check("Rosto novo mais distante NÃO rouba o alvo", f.attention.target_id == a_id)

    # 3) 60 s com dois rostos parados: olhadas periódicas, nenhuma troca
    events.clear()
    t, f = simulate(r, 60, both, t0=t, collect=col)
    glances = sum(1 for e in events if e[1] == "glance")
    switches = sum(1 for e in events if e[1] == "switch")
    check("Em 60 s com 2 pessoas: várias olhadas de relance (4–15)", 4 <= glances <= 15, f"{glances}")
    check("...e nenhuma troca de alvo (histerese)", switches == 0, f"{switches}")

    # 4) B chega muito perto (rosto grande) e fica: o alvo deve trocar
    events.clear()
    closer = lambda t: [(0.15, 0.40, 0.26), (0.75, 0.45, 0.46)]
    t, f = simulate(r, 6, closer, t0=t, collect=col)
    sw = [e for e in events if e[1] == "switch"]
    check("Quem fica claramente mais perto conquista o alvo", f.attention.target_id != a_id and len(sw) >= 1)

    # 5) alvo some: troca para o outro rapidamente
    events.clear()
    only_a = lambda t: [(0.15, 0.40, 0.26)]
    t1 = t
    t, f = simulate(r, 2.0, only_a, t0=t, collect=col)
    sw = [e for e in events if e[1] == "switch"]
    check("Alvo some => troca para quem sobrou em < 2 s", sw and sw[0][0] - t1 < 2.0 and f.attention.target_id is not None,
          f"{(sw[0][0] - t1) if sw else 0:.2f} s")

    # 6) todos saem: ocioso -> sonolento -> dormindo -> acorda
    events.clear()
    t, f = simulate(r, 4.0, lambda t: [], t0=t, collect=col)
    check("Todos saem => ocioso em ~2–4 s", f.attention.mode == "idle")
    t, f = simulate(r, 26.0, lambda t: [], t0=t, collect=col)
    check("Depois de ~25 s fica sonolento", f.attention.idle_level >= 1 and f.shown == "sleepy", f.shown)
    t, f = simulate(r, 30.0, lambda t: [], t0=t, collect=col)
    check("Depois de ~50 s dorme", f.attention.idle_level == 2 and f.shown == "sleeping", f.shown)
    events.clear()
    t, f = simulate(r, 1.5, lambda t: [(0.5, 0.45, 0.3)], t0=t, collect=col)
    check("Alguém chega: acorda (wake) e arregala", any(e[1] == "wake" for e in events))

    # 7) gancho de IA: quem fala vira o alvo
    r2 = Robot(seed=3)
    two = lambda t: [(0.25, 0.45, 0.30), (0.75, 0.45, 0.26)]
    t, f = simulate(r2, 4, two)
    other = [k.id for k in r2.tracker.tracks if k.id != f.attention.target_id][0]
    r2.attention.set_speaker(other)
    t, f = simulate(r2, 2.0, two, t0=t)
    check("Gancho da IA: set_speaker() leva o olhar a quem fala", f.attention.target_id == other)


def test_gaze():
    print("Dinâmica do olhar")
    g = GazeController(rng=random.Random(1))
    g.set_target(0.8, 0.0)
    xs = []
    for i in range(60):
        xs.append(g.update(1 / 60)[0])
    settle = next((i for i, v in enumerate(xs) if abs(v - 0.8) < 0.04), 99) / 60
    check("Sacada para um novo alvo assenta em < 0,35 s", settle < 0.35, f"{settle:.2f}s")
    check("Sem estourar o alvo (overshoot < 0,05)", max(xs) < 0.85, f"{max(xs):.3f}")
    g = GazeController(reduce_motion=True, rng=random.Random(1))
    g.set_target(0.8, 0.0)
    xs = [g.update(1 / 60)[0] for _ in range(120)]
    check("Reduzir movimento: sem overshoot e sem microssacadas", max(xs) <= 0.801 and abs(xs[-1] - 0.8) < 0.02)

    # o desenho acompanha: o centro de massa dos LEDs acesos desloca para o lado do olhar
    from expressions import EXPRESSIONS
    cm = []
    for gx in (-1.0, 0.0, 1.0):
        a, _ = render_field(80, 45, EXPRESSIONS["neutral"], (1, 1), (gx, 0), 1.0)
        xs_ = (a.sum(axis=0) * np.arange(80)).sum() / a.sum()
        cm.append(xs_)
    check("Desenho: olhar -1 / 0 / +1 desloca os olhos esq. / centro / dir.", cm[0] < cm[1] - 4 and cm[2] > cm[1] + 4,
          f"{cm[0]:.1f} {cm[1]:.1f} {cm[2]:.1f}")


def test_expressions():
    print("Expressões e piscadas")
    base, _ = render_field(80, 45, EXPRESSIONS["neutral"], (1, 1), (0, 0))
    check("Neutro é simétrico", np.abs(base - base[:, ::-1]).max() < 1e-4)
    for name in KEY_ORDER:
        a, g = render_field(80, 45, EXPRESSIONS[name], (1, 1), (0, 0))
        ok = a.min() >= 0 and a.max() <= 1.0001 and g.min() >= 0 and g.max() <= 1.0001
        check(f"'{name}': valores de brilho em [0,1]", ok)
        if name != "neutral":
            check(f"'{name}': visualmente distinto do neutro", np.abs(a - base).sum() > 15, f"{np.abs(a - base).sum():.0f}")
    closed, _ = render_field(80, 45, EXPRESSIONS["neutral"], (0, 0), (0, 0))
    check("Piscar fecha os olhos (brilho total cai > 85%)", closed.sum() < 0.15 * base.sum())
    left, _ = render_field(80, 45, EXPRESSIONS["neutral"], (0, 1), (0, 0))
    check("Piscadela: só um olho fecha", abs(left[:, :40].sum() - 0.1 * base[:, :40].sum()) < 0.25 * base[:, :40].sum()
          and left[:, 40:].sum() > 0.95 * base[:, 40:].sum())

    b = Blinker(random.Random(5))
    n, prev, intervals, t, last_t = 0, 1.0, [], 0.0, None
    for i in range(60 * 120):
        v, _ = b.update(1 / 60)
        t += 1 / 60
        if prev > 0.5 >= v:
            n += 1
            if last_t is not None:
                intervals.append(t - last_t)
            last_t = t
        prev = v
    check("Ritmo de piscadas natural (15–45 em 2 min, bem abaixo de 3 Hz)", 15 <= n <= 45, f"{n}")
    check("Intervalos irregulares (desvio-padrão > 0,6 s)", np.std(intervals) > 0.6, f"{np.std(intervals):.2f}s")

    e = ExpressionEngine(rng=random.Random(2))
    e.set_mood("happy")
    for _ in range(90):
        e.update(1 / 60)
    check("Transição animada chega à pose-alvo", abs(e.pose[ARCH] - EXPRESSIONS["happy"][ARCH]) < 0.03)
    e.set_mood("auto")
    e.pulse("surprised", 0.5)
    for _ in range(20):
        e.update(1 / 60)
    s1 = e.shown_name()
    for _ in range(40):
        e.update(1 / 60)
    check("Pulso: reage e volta ao neutro", s1 == "surprised" and e.shown_name() == "neutral")


def test_reflex_blink(make_png=False):
    print("Piscada-reflexo / aproximação simulada")
    durations = []
    for reduced in (False, True):
        b = Blinker(random.Random(2), reduce_motion=reduced)
        accepted = b.trigger(reflex=True)
        samples = [b.update(0.001, enabled=False) for _ in range(100)]
        duration = next(i + 1 for i in range(50, 100) if samples[i] == (1.0, 1.0))
        durations.append(duration)
        check(f"Reflexo {'reduzido' if reduced else 'normal'}: bilateral, fecha e reabre em 90 ms",
              accepted and duration == 90 and min(v[0] for v in samples) == 0.0
              and all(l == r and 0 <= l <= 1 for l, r in samples))
        check(f"Cooldown de 2,5 s {'reduzido' if reduced else 'normal'}: bloqueia repetição",
              not b.trigger(reflex=True))
        for _ in range(239):
            b.update(0.01, enabled=False)
        before = b.trigger(reflex=True)
        b.update(0.01, enabled=False)
        check(f"Cooldown {'reduzido' if reduced else 'normal'}: libera em 2,5 s",
              not before and b.trigger(reflex=True))
    b = Blinker(random.Random(2))
    b.trigger()
    normal = [b.update(0.001, enabled=False) for _ in range(250)]
    normal_ms = next(i + 1 for i in range(100, 250) if normal[i] == (1.0, 1.0))
    check("Reflexo é mais rápido que a piscada natural", max(durations) < normal_ms,
          f"reflexo {max(durations)} ms; natural {normal_ms} ms")
    timing = []
    for hz in (30, 60, 120):
        b = Blinker(random.Random(3))
        b.trigger(reflex=True)
        values = []
        while b.reflex_active:
            values.append(b.update(1 / hz, enabled=False)[0])
        timing.append(len(values) / hz)
    check("Reflexo termina até 100 ms a 30/60/120 Hz", all(0.08 <= t <= 0.100001 for t in timing),
          "/".join(f"{t * 1000:.0f} ms" for t in timing))

    b = Blinker(random.Random(2))
    b.trigger()
    closing = b.update(0.025, enabled=False)[0]
    promoted = b.trigger(reflex=True)
    check("Reflexo acelera fechamento em curso sem reabrir", promoted and b.update(0.001)[0] <= closing)
    opening = Blinker(random.Random(2))
    opening.trigger()
    value = opening.update(0.12, enabled=False)[0]
    check("Reflexo durante abertura acelera sem fechar outra vez",
          opening.trigger(reflex=True) and opening.update(0.001)[0] >= value)
    for _ in range(150):
        b.update(0.001, enabled=False)
    check("Não emenda piscada normal/dupla logo após reflexo", not b.trigger())
    # Pedidos misturados em alta frequência devem continuar abaixo de 3 Hz.
    b = Blinker(random.Random(8))
    starts = []
    for i in range(3000):
        idle = b._phase is None
        if b.trigger(reflex=i % 2 == 0) and idle:
            starts.append(i / 1000)
        b.update(0.001, enabled=False)
    check("Pedidos misturados: intervalo entre novos ciclos >=350 ms",
          all(y - x >= 0.35 - 1e-9 for x, y in zip(starts, starts[1:])))

    def approach_run(reduced=False):
        r, source = Robot(seed=18, reduce_motion=reduced), SimulatedSource(seed=9)
        t, active, starts, trace = 0.0, False, [], []

        def drive(seconds, zoom=0.0):
            nonlocal t, active
            for _ in range(round(seconds * 60)):
                t += 1 / 60
                source.zoom(zoom)  # mesmo comando da roda do mouse em --sim
                frame = r.step(source.poll(1 / 60), t, 1 / 60)
                current = r.expr.blinker.reflex_active
                if current and not active:
                    starts.append(t)
                active = current
                trace.append((r.expr.blink, frame.attention.target_id, current))

        drive(0.8)
        stationary = len(starts)
        source.zoom(0.15)  # .32 -> .47: aproxima rápido sem chegar a h>.52
        drive(0.25)
        first = len(starts)
        source.zoom(-0.15)
        drive(0.6)
        retreat = len(starts)
        source.zoom(0.15)
        drive(0.25)
        blocked = len(starts)
        source.zoom(-0.15)
        drive(2.0)
        source.zoom(0.15)
        drive(0.25)
        rearmed = len(starts)
        drive(3.0)
        still = len(starts)
        # Aproximação lenta até perto demais; dispara pela distância, não velocidade.
        drive(3.0, zoom=0.0005)
        near = len(starts)
        drive(3.0)
        held_near = len(starts)
        drive(1.5, zoom=-0.0005)  # cruza .52, mas fica acima de .48 (histerese)
        drive(1.5, zoom=0.0005)
        jitter = len(starts)
        return r, starts, trace, (stationary, first, retreat, blocked, rearmed, still, near, held_near, jitter)

    r, starts, trace, counts = approach_run()
    check("SimulatedSource: roda rápida dispara mesmo abaixo de h=0,52", counts[:2] == (0, 1))
    check("Afastamento não dispara e repetição durante cooldown é bloqueada", counts[2:4] == (1, 1))
    check("Nova aproximação após cooldown dispara; rosto parado não repete", counts[4:6] == (2, 2))
    check("Aproximação lenta: h>0,52 dispara uma vez; perto/parado não repete", counts[6:8] == (3, 3))
    check("Histerese evita repetição ao oscilar em torno de h=0,52", counts[8] == 3)
    check("Reflexos de aproximação separados por >=2,5 s", len(starts) == 3
          and all(y - x >= 2.5 - 1e-9 for x, y in zip(starts, starts[1:])))
    check("SimulatedSource com seed e tempo injetado é determinístico", trace == approach_run()[2])
    reduced, _, _, reduced_counts = approach_run(reduced=True)
    check("Reduzir Movimento mantém os gatilhos do reflexo", reduced_counts == counts)
    e = ExpressionEngine(reduce_motion=True, rng=random.Random(2))
    pose = e.pose.copy()
    api = e.blink_now(reflex=True)
    for _ in range(6):
        e.update(1 / 60)
    check("API Robot.expr: reflexo simples sem mola, recuo ou mudança de pose",
          api and np.array_equal(e.pose, pose) and np.count_nonzero(e.vel) == 0 and e.blink == (1.0, 1.0))
    check("API Robot.blink(reflex=True) expõe reflexo e bloqueio", reduced.blink(reflex=True)
          and not reduced.blink(reflex=True))

    # Não compara pessoas diferentes nem alturas separadas por falta de quadros.
    r = Robot(seed=18)
    now, _ = simulate(r, 0.8, lambda t: [(0.5, 0.45, 0.32)])
    for i in range(30):
        now += 1 / 60
        r.step(None, now, 1 / 60)
    r.step([det(0.5, 0.45, 0.47)], now + 1 / 60, 1 / 60)
    check("Sem quadro novo/oclusão não gera velocidade artificial", not r.expr.blinker.reflex_active)
    r = Robot(seed=18)
    now, _ = simulate(r, 0.8, lambda t: [(0.5, 0.45, 0.20)])
    r.step([det(0.5, 0.45, 0.45)], now + 1 / 30, 1 / 30)
    check("Rosto com outro ID não herda velocidade do anterior", not r.expr.blinker.reflex_active)
    r = Robot(seed=18)
    first_near = []
    simulate(r, 0.3, lambda t: [(0.5, 0.45, 0.60)], collect=lambda t, f: first_near.append(r.expr.blinker.reflex_active))
    check("Rosto que já entra muito perto também dispara", any(first_near))

    if make_png:
        import pygame
        from attention import AttentionState
        from renderer import Renderer
        from robot import RobotFrame
        pygame.init()
        pygame.display.set_mode((1, 1))
        sheet = pygame.Surface((1600, 240))
        font = pygame.font.SysFont(theme.FONT_STACK, 20)
        blink = Blinker(random.Random(2))
        blink.trigger(reflex=True)
        last, value = 0, (1.0, 1.0)
        for i, ms in enumerate((0, 25, 40, 65, 90)):
            if ms:
                value = blink.update((ms - last) / 1000, enabled=False)
            last = ms
            sub = pygame.Surface((320, 240))
            a, g = render_field(80, 45, EXPRESSIONS["neutral"], value, (0, 0))
            fr = RobotFrame(a, g, "neutral", "auto", (0, 0), AttentionState(), [])
            Renderer(sub, (80, 45), theme.DARK_SCHEME).draw_panel(fr, 1 / 60, True)
            sub.blit(font.render(f"{ms} ms", True, theme.DARK_SCHEME.text), (12, 8))
            sheet.blit(sub, (i * 320, 0))
        (ROOT / "previews").mkdir(exist_ok=True)
        pygame.image.save(sheet, str(ROOT / "previews" / "piscada_reflexo.png"))


def test_detector():
    print("Detector real (YuNet) com imagem de teste")
    try:
        import cv2
        from skimage import data
        from tracker import YuNetDetector, DEFAULT_MODEL
    except Exception as ex:
        print("  (pulado:", ex, ")")
        return
    if not DEFAULT_MODEL.exists():
        print("  (pulado: modelo ausente)")
        return
    img = cv2.cvtColor(data.astronaut(), cv2.COLOR_RGB2BGR)
    d = YuNetDetector(DEFAULT_MODEL, mirror=True)
    out = d.detect(img)
    check("Detecta 1 rosto na imagem de teste", len(out) == 1, f"{len(out)}")
    if out:
        raw = YuNetDetector(DEFAULT_MODEL, mirror=False).detect(img)[0]
        check("Espelhamento: x_vista = 1 - x_bruto", abs(out[0].ex - (1 - raw.ex)) < 1e-6)
        check("Ponto dos olhos fica dentro da caixa do rosto", out[0].x <= out[0].ex <= out[0].x + out[0].w
              and out[0].y <= out[0].ey <= out[0].y + out[0].h)
    two = np.concatenate([img, cv2.flip(img, 1)], axis=1)
    out2 = d.detect(two)
    check("Detecta 2 rostos lado a lado", len(out2) == 2, f"{len(out2)}")
    t0 = time.perf_counter()
    for _ in range(20):
        d.detect(two)
    ms = (time.perf_counter() - t0) / 20 * 1000
    check("Detecção rápida (< 25 ms/quadro nesta máquina)", ms < 25, f"{ms:.1f} ms")


def test_perf_and_png(make_png: bool):
    import pygame
    pygame.init()
    from attention import AttentionState
    from renderer import HudInfo, Renderer

    print("Desempenho e imagens")
    size = (1920, 1080)
    screen = pygame.display.set_mode(size)
    rend = Renderer(screen, (80, 45), theme.DARK_SCHEME, glow=True)
    r = Robot(seed=11)
    hud = HudInfo(camera_status="ok", fps=60.0)
    faces = lambda t: [(0.5 + 0.3 * math.sin(t * 0.7), 0.45, 0.30), (0.2, 0.5, 0.22)]
    t, f = simulate(r, 3.0, faces)
    t0 = time.perf_counter()
    N = 120
    for i in range(N):
        t += 1 / 60
        f = r.step([det(*x) for x in faces(t)] if i % 2 == 0 else None, t, 1 / 60)
        rend.draw_panel(f, 1 / 60, False)
        rend.draw_hud(f, hud)
    ms = (time.perf_counter() - t0) / N * 1000
    check("Pipeline + desenho em 1920x1080 cabe em 60 fps (< 16,6 ms)", ms < 16.6, f"{ms:.1f} ms/quadro")

    if not make_png:
        return
    out = ROOT / "previews"
    out.mkdir(exist_ok=True)

    def shot(name, scheme, mood="auto", size=(1280, 720), gaze_pose=None, hud_kw=None, faces=None, secs=2.5,
             sim_t=None, help_=False):
        s = pygame.display.set_mode(size)
        rn = Renderer(s, (80, 45), scheme, glow=True)
        rb = Robot(seed=5)
        rb.set_mood(mood)
        fa = faces or (lambda t: [(0.30, 0.42, 0.30)])
        t, fr = simulate(rb, secs, fa)
        h = HudInfo(camera_status="ok", fps=60.0, **(hud_kw or {}))
        h.show_help = help_
        h.hint_age = 0.0
        rn.draw_panel(fr, 1 / 60, True)
        rn.draw_hud(fr, h)
        pygame.image.save(s, str(out / name))
        return rb, fr

    # expressões
    cols, rows, tw, th = 4, 2, 640, 360
    sheet = pygame.Surface((cols * tw, rows * th))
    for i, name in enumerate(KEY_ORDER):
        sub = pygame.Surface((tw, th))
        rn = Renderer(sub, (80, 45), theme.DARK_SCHEME)
        z = [{"age": 1.0, "life": 3.4}, {"age": 2.2, "life": 3.4}] if name == "sleeping" else None
        a, g = render_field(80, 45, EXPRESSIONS[name], (1, 1), (0, 0), 1.0, z)
        from robot import RobotFrame
        rf = RobotFrame(a, g, name, "auto", (0, 0), AttentionState(), [])
        rn.draw_panel(rf, 1 / 60, True)
        sheet.blit(sub, ((i % cols) * tw, (i // cols) * th))
    pygame.image.save(sheet, str(out / "expressoes.png"))

    # olhar em 9 direções
    sheet = pygame.Surface((3 * 480, 3 * 270))
    for j, gy in enumerate((-1, 0, 1)):
        for i, gx in enumerate((-1, 0, 1)):
            sub = pygame.Surface((480, 270))
            rn = Renderer(sub, (80, 45), theme.DARK_SCHEME)
            a, g = render_field(80, 45, EXPRESSIONS["neutral"], (1, 1), (gx, gy), 1.0)
            rf = RobotFrame(a, g, "neutral", "auto", (gx, gy), AttentionState(), [])
            rn.draw_panel(rf, 1 / 60, True)
            sheet.blit(sub, (i * 480, j * 270))
    pygame.image.save(sheet, str(out / "olhar_9_direcoes.png"))

    two = lambda t: [(0.30, 0.42, 0.30), (0.72, 0.48, 0.20)]
    shot("hud_escuro_2_rostos.png", theme.DARK_SCHEME, faces=two, secs=4.0)
    shot("hud_claro.png", theme.LIGHT_SCHEME, faces=two, secs=4.0)
    shot("hud_alto_contraste.png", theme.CONTRAST_SCHEME, faces=two, secs=4.0)
    shot("ajuda.png", theme.DARK_SCHEME, faces=two, secs=3.0, help_=True)
    shot("ajuda_compacta.png", theme.LIGHT_SCHEME, size=(640, 480), help_=True)
    shot("feliz.png", theme.DARK_SCHEME, mood="happy", secs=1.5)
    # Os estados abaixo são dados sintéticos: não abrem nem fotografam a câmera.
    for filename, status, note in (("erro_camera.png", "error", "Câmera em uso por outro app. Feche o OBS e tente novamente."),
                                   ("aguardando_imagem.png", "waiting", "Sem quadros novos. Verifique a câmera; tentando novamente.")):
        surface = pygame.display.set_mode((640, 480))
        rn = Renderer(surface, (80, 45), theme.DARK_SCHEME)
        rn.draw_panel(f, 1 / 60, True)
        rn.draw_hud(f, HudInfo(camera_status=status, camera_name="Integrated Webcam", camera_index=1, camera_note=note))
        pygame.image.save(surface, str(out / filename))
    for sc in theme.SCHEMES.values():
        size = (1280, 720)
        surface = pygame.display.set_mode(size)
        rn = Renderer(surface, (80, 45), sc)
        rn.menu.open = True
        rn.draw_panel(f, 1 / 60, False)
        rn.draw_hud(f, HudInfo(camera_status="sim", simulated=True, fps=60))
        pygame.image.save(surface, str(out / f"menu_{sc.key}.png"))
    rn.menu.open = True
    rn.menu.view = "features"
    rn.draw_panel(f, 1 / 60, True)
    rn.draw_hud(f, HudInfo(camera_status="sim", simulated=True, features=dict(hands=True, tongue=False,
                        smile=True, objects=False, reflex=True, auto_blink=False)))
    pygame.image.save(surface, str(out / "reacoes.png"))
    rn.menu.open = True
    rn.menu.view = "cameras"
    rn.draw_panel(f, 1 / 60, True)
    rn.draw_hud(f, HudInfo(camera_status="sim", simulated=True,
                          camera_choices=[(0, "Animaze"), (1, "Integrated Webcam"), (2, "Iriun"), (3, "OBS Virtual Camera")]))
    pygame.image.save(surface, str(out / "selecao_camera.png"))
    for name, size, status in (("debug.png", (1280, 720), "sim"),
                               ("camera_pausada_hud_oculto.png", (640, 480), "paused")):
        surface = pygame.display.set_mode(size)
        rn = Renderer(surface, (80, 45), theme.DARK_SCHEME)
        rn.draw_panel(f, 1 / 60, False)
        rn.draw_hud(f, HudInfo(camera_status=status, hud_mode="off", debug=name == "debug.png",
                              simulated=True, fps=60, last_trigger=dict(kind="tongue_out", score=.95, time=2.4), trigger_age=.2))
        pygame.image.save(surface, str(out / name))
    print("  imagens em", out)


def test_camera_bootstrap():
    import tracker
    from unittest.mock import patch
    print("Inicialização do detector e backends da câmera")
    if tracker.cv2 is not None and tracker.DEFAULT_MODEL.exists():
        detector, warn = tracker.make_detector()
        result = detector.detect(np.zeros((480, 640, 3), dtype=np.uint8))
        check("YuNet carrega ONNX no caminho real e processa quadro sintético", detector.name == "YuNet"
              and warn is None and isinstance(result, list))
        detector.close()
    captures = []
    from camera_devices import CameraDevice
    class Capture:
        def __init__(self, index, backend):
            self.backend, self.released = backend, False
            self.reads = 0
            captures.append(self)
        def isOpened(self):
            return True
        def set(self, *args):
            return True
        def read(self):
            self.reads += 1
            return ((False, None) if self.backend == tracker.cv2.CAP_DSHOW else
                    (True, np.full((2, 2, 3), (60 + self.reads, 100, 150), dtype=np.uint8)))
        def release(self):
            self.released = True
    source = tracker.CameraSource(None, 0)
    with patch.object(tracker.os, "name", "nt"), patch.object(tracker.cv2, "VideoCapture", side_effect=Capture), \
            patch.object(tracker, "enumerate_cameras", return_value=[CameraDevice(0, "Câmera 0")]):
        chosen = source._open()
    check("DSHOW sem quadro libera dispositivo e tenta MSMF", len(captures) == 2
          and captures[0].released and source.backend == "MSMF" and chosen is captures[1])
    chosen.release()
    check("Sonda não retém quadros na fonte", not hasattr(source, "first_frame") and not hasattr(source, "frame"))
    with patch.object(tracker.os, "name", "nt"), \
            patch.object(tracker.cv2, "VideoCapture", side_effect=RuntimeError("ocupada")):
        chosen = source._open()
    check("Erro identifica índice/backends e orienta o usuário", chosen is None and "Câmera 0" in source.error
          and "DSHOW" in source.error and "MSMF" in source.error and "ocupada" in source.error)


def test_camera_health():
    import tracker
    from unittest.mock import patch
    from camera_devices import CameraDevice, preferred_cameras
    from camera_health import CaptureHealth
    print("Seleção física e imagem ao vivo")
    devices = [CameraDevice(0, "Animaze Virtual Camera"), CameraDevice(1, "Integrated Webcam"),
               CameraDevice(2, "IR Camera"), CameraDevice(3, "OBS Virtual Camera"),
               CameraDevice(4, "Iriun Webcam"), CameraDevice(5, "USB Camera")]
    check("Preferência ignora virtual/IR/Animaze/Iriun/OBS", [d.index for d in preferred_cameras(devices)] == [1, 5])
    health = CaptureHealth()
    health.observe(np.zeros((16, 16, 3), dtype=np.uint8), 1)
    check("Quadro preto nunca indica câmera ativa", health.snapshot(1)["state"] == "waiting")
    health.observe(np.full((16, 16, 3), 100, dtype=np.uint8), 1.1)
    check("Quadro monocromático aguarda imagem RGB", health.snapshot(1.1)["state"] == "waiting")
    color = np.full((16, 16, 3), (30, 90, 160), dtype=np.uint8)
    health.observe(color, 1.2)
    check("Quadro colorido que varia confirma captura", health.snapshot(1.3)["state"] == "ok")
    health.observe(color, 2.8)
    check("Quadro congelado deixa de indicar ativo", health.snapshot(2.8)["state"] == "waiting")
    check("Sem quadro novo por 1 s zera FPS e aguarda imagem", health.snapshot(4)["state"] == "waiting"
          and health.snapshot(4)["capture_fps"] == 0)
    health.observe(color + 1, 4.1)
    check("Imagem nova recupera captura sem guardar pixels", health.snapshot(4.1)["state"] == "ok"
          and not any(isinstance(value, np.ndarray) for value in vars(health).values()))
    opened = []
    class Capture:
        def __init__(self, index, backend):
            self.index, self.reads = index, 0
            opened.append(index)
        def isOpened(self): return True
        def set(self, *args): return True
        def release(self): pass
        def read(self):
            self.reads += 1
            return True, np.full((16, 16, 3), (30 + (self.reads if self.index == 5 else 0), 90, 160), dtype=np.uint8)
    source = tracker.CameraSource(None)
    with patch.object(tracker.os, "name", "nt"), patch.object(tracker, "enumerate_cameras", return_value=devices), \
            patch.object(tracker.cv2, "VideoCapture", side_effect=Capture):
        cap = source._open()
    check("Auto rejeita física congelada e escolhe primeira RGB válida", cap is not None and source.index == 5
          and set(opened) == {1, 5})
    cap.release()
    source.set_camera(1)
    check("Troca de câmera agenda reabertura e limpa captura anterior", source.requested_index == 1
          and source._switch.is_set() and source.capture_info(5)["resolution"] is None)


def test_diagnostics():
    import pygame
    from unittest.mock import patch
    from diagnostics import DetectionBatch, FrameMetrics, TimedDetector
    from renderer import HudInfo, Renderer
    print("Diagnóstico e privacidade")

    class Detector:
        name = "teste"
        def detect(self, frame):
            return [det(0.5, 0.45, 0.3)]

    ticks = iter((10.0, 10.012))
    batch = TimedDetector(Detector(), clock=lambda: next(ticks)).detect(object())
    check("Tempos acompanham a lista do mesmo quadro, sem imagem", isinstance(batch, list)
          and isinstance(batch[0], Detection) and batch.received_at == 10.0 and batch.detected_at == 10.012
          and batch.observations is None and batch.perception_status == ""
          and not any(isinstance(v, np.ndarray) for v in vars(batch).values()))
    metrics = FrameMetrics()
    metrics.accept(batch)
    metrics.presented(10.030)
    check("Quadro recebido até flip inclui detector e espera/desenho", abs(metrics.latency_ms - 30) < 1e-6
          and abs(metrics.detection_ms - 12) < 1e-6)
    metrics.presented(10.060)
    check("Sem quadro novo não duplica amostra de latência", len(metrics.samples) == 1)
    for i in range(130):
        metrics.accept(DetectionBatch([], float(i), float(i)))
        metrics.presented(i + 0.02)
    check("Janela p95 é limitada a 120 amostras", len(metrics.samples) == 120 and abs(metrics.p95_ms - 20) < 1e-6)
    metrics.reset()
    check("Pausa/erro limpa tempos e histórico", metrics.latency_ms is None and metrics.p95_ms is None
          and metrics.pending is None and not metrics.samples)
    pygame.init()
    surface = pygame.display.set_mode((640, 480))
    rf = Robot(seed=4).step([], 0, 1 / 60)
    indicators = []
    for scheme in theme.SCHEMES.values():
        rn = Renderer(surface, (80, 45), scheme)
        for status in ("ok", "paused", "sim", "starting", "error", "waiting"):
            rn.draw_panel(rf, 1 / 60, False)
            with patch.object(rn, "_chip", wraps=rn._chip) as chips:
                rn.draw_hud(rf, HudInfo(camera_status=status, hud_mode="off"))
            call = chips.call_args_list[0]
            indicators.append((status, call.args[0][0], call.args[3]))
    check("HUD oculto mantém texto e forma nos 3 temas/6 estados", len(indicators) == 18
          and all(text and marker == {"ok": "circle", "paused": "square", "sim": "diamond",
                                      "starting": "ring", "error": "diamond", "waiting": "ring"}[status]
                  for status, text, marker in indicators))
    with patch.object(rn, "_debug", wraps=rn._debug) as overlay:
        rn.draw_hud(rf, HudInfo(debug=False))
        hidden = overlay.call_count == 0
        rn.draw_hud(rf, HudInfo(debug=True, simulated=True, hud_mode="off"))
    check("Overlay só aparece habilitado e funciona com HUD oculto", hidden and overlay.call_count == 1)


def test_camera_metrics_smoke():
    import pygame
    from dataclasses import replace
    from unittest.mock import patch
    import main as app
    print("Laço com câmera falsa: métricas e pausa")
    sources, snapshots = [], []

    class Detector:
        name = "teste"
        def detect(self, frame):
            return [det(0.5, 0.45, 0.3)]

    class FakeSource:
        def __init__(self, detector, index):
            self.detector, self.index = detector, index
            self.status, self.paused, self.error = "ok", False, ""
            self.detect_fps, self.stopped = 28.0, False
            sources.append(self)
        def start(self):
            pass
        def poll(self):
            return None if self.paused else self.detector.detect(None)
        def set_paused(self, value):
            self.paused = value
            self.status = "paused" if value else "ok"
        def stop(self):
            self.stopped = True
        def set_camera(self, index):
            self.index = index
        def capture_info(self):
            return dict(state=self.status, note=self.error, index=1, name="RGB de teste",
                        backend="DSHOW", capture_fps=28, resolution=(640, 480), brightness=123,
                        choices=[(1, "RGB de teste")])

    events = [[], [], [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_p)],
              [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_p)],
              [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_d)],
              [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F10),
               pygame.event.Event(pygame.KEYDOWN, key=pygame.K_DOWN),
               pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN),
               pygame.event.Event(pygame.KEYDOWN, key=pygame.K_DOWN),
               pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN)],
              [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_q)]]
    original_draw = app.Renderer.draw_hud
    def observe(renderer, frame, hud):
        snapshots.append(replace(hud))
        original_draw(renderer, frame, hud)
    with patch.object(app, "make_detector", return_value=(Detector(), "")), \
            patch.object(app, "CameraSource", FakeSource), \
            patch.object(app.pygame.event, "get", side_effect=events), \
            patch.object(app.Renderer, "draw_hud", autospec=True, side_effect=observe):
        result = app.main(["--debug", "--run-seconds", "2"])
    check("FPS/latência do quadro chegam ao overlay", result == 0 and any(
          h.detect_fps == 28 and h.latency_ms is not None and h.latency_ms >= h.detection_ms for h in snapshots))
    paused = [h for h in snapshots if h.camera_status == "paused"]
    check("Pausa limpa métricas; retomar produz novas amostras", paused and all(
          h.latency_ms is None and h.detect_fps is None and h.latency_samples == 0 for h in paused)
          and snapshots[-1].latency_samples > 0)
    check("Debug alterna e fonte falsa é liberada na saída", snapshots[0].debug and not snapshots[-1].debug
          and sources[0].stopped)
    check("HUD recebe nome/índice/backend/resolução/FPS/brilho", snapshots[-1].camera_name == "RGB de teste"
          and snapshots[-1].camera_index == 1 and snapshots[-1].camera_backend == "DSHOW"
          and snapshots[-1].capture_resolution == (640, 480) and snapshots[-1].capture_fps == 28
          and snapshots[-1].mean_brightness == 123)
    check("Escolha de câmera no menu chega à fonte sem reiniciar o app", sources[0].index == 1)


def test_menu():
    import pygame
    from renderer import HudInfo, Renderer
    from menu import OptionsMenu
    pygame.init()
    print("Menu: mouse, teclado e layout")
    hud = HudInfo(simulated=True)
    bounds_ok, targets_ok, contrast_ok = True, True, True
    for size in ((640, 480), (800, 600), (1280, 720), (1920, 1080)):
        for sc in theme.SCHEMES.values():
            surface = pygame.display.set_mode(size)
            rn = Renderer(surface, (80, 45), sc)
            rn.menu.open = True
            rn.menu.draw(rn, hud)
            bounds_ok &= surface.get_rect().contains(rn.menu.card) and surface.get_rect().contains(rn.menu.button)
            targets_ok &= all(rect.h >= theme.CONTROL_MIN * rn.s for rect in rn.menu.rows)
            backgrounds = [theme.mix(back, sc.chip, 245 / 255) for back in ((0, 0, 0), (255, 255, 255))]
            backgrounds.append(theme.mix(sc.chip, sc.text, 0.08))
            contrast_ok &= all(theme.contrast(text, back) >= 4.5 for text in (sc.text, sc.text_dim) for back in backgrounds)
    check("Menu cabe de 640x480 a 1920x1080 nos três temas", bounds_ok)
    check("Alvos do menu >= 44 pt", targets_ok)
    check("Texto do menu >= 4,5:1 mesmo sobre extremos do vidro", contrast_ok)
    menu = OptionsMenu()
    menu.layout(surface, rn.s)
    items = menu.items(hud, theme.DARK_SCHEME)
    menu.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F10), items)
    menu.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_END), items)
    consumed, action = menu.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN), items)
    check("Teclado abre menu, move foco e ativa Sair", consumed and action == "quit" and not menu.open)
    pos = menu.button.center
    menu.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=pos), items)
    menu.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=pos), items)
    pos = menu.rows[0].center
    menu.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=pos), items)
    consumed, action = menu.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=pos), items)
    check("Mouse abre menu e ativa pausa", consumed and action == "pause" and not menu.open)
    menu.open = True
    consumed, action = menu.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(0, 0)), items)
    check("Clique fora fecha menu sem vazar à simulação", consumed and action is None and not menu.open)
    consumed, action = menu.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=menu.button.center), items)
    check("Soltar mouse sem pressionar não ativa controle", action is None and not menu.open)
    hud.camera_choices = [(0, "Virtual"), (1, "Integrated Webcam")]
    menu.open = True
    menu.activate("cameras")
    camera_items = menu.items(hud, theme.DARK_SCHEME)
    menu.focus = 2
    consumed, action = menu.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN), camera_items)
    check("Submenu permite escolher câmera pelo nome/índice", consumed and action == "camera:1" and not menu.open)
    rn.draw_hud(Robot().step([], 0, 1 / 60), HudInfo(show_help=True))
    # A ajuda precisa ser fechada pelo Escape antes de sair do aplicativo.
    help_hud = HudInfo(show_help=True)
    consumed, _ = rn.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE), help_hud)
    check("Escape fecha primeiro o menu sobre a ajuda", consumed and not rn.menu.open and help_hud.show_help)
    consumed, _ = rn.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE), help_hud)
    check("Escape fecha ajuda sem encerrar o aplicativo", consumed and not help_hud.show_help)


def test_window_smoke():
    import pygame
    from unittest.mock import patch
    import main as app

    print("Janela e laço do aplicativo (sem câmera)")
    check("Janela é o padrão; tela cheia exige flag", not app.parse_args([]).fullscreen
          and app.parse_args(["--fullscreen"]).fullscreen)
    check("Flag --windowed mantém compatibilidade", not app.parse_args(["--windowed"]).fullscreen)
    events = [
        [pygame.event.Event(pygame.VIDEORESIZE, w=900, h=600)],
        [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F11)],
        [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F11)],
        [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F10),
         pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN)],
        [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_d)],
        [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F10),
         pygame.event.Event(pygame.KEYDOWN, key=pygame.K_END),
         pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN)],
    ]
    sim = app.SimulatedSource(seed=4)
    with patch.object(app.pygame.event, "get", side_effect=events), \
            patch.object(app.pygame.display, "set_mode", wraps=pygame.display.set_mode) as opened, \
            patch.object(app, "CameraSource", side_effect=AssertionError("Webcam proibida no selftest")), \
            patch.object(app, "SimulatedSource", return_value=sim), \
            patch.object(app.Renderer, "draw_hud", autospec=True) as draw_hud:
        result = app.main(["--sim", "--run-seconds", "2"])
    modes = [(call.args[0], call.args[1]) for call in opened.call_args_list]
    check("Laço simulado encerra sem webcam", result == 0)
    check("Ação Pausar do menu chega à fonte simulada", sim.paused)
    check("Tecla D liga diagnóstico sem inventar métricas da simulação", draw_hud.call_args.args[2].debug
          and draw_hud.call_args.args[2].detect_fps is None and draw_hud.call_args.args[2].latency_ms is None)
    check("F11 volta ao tamanho redimensionado da janela", modes[0] == ((1280, 720), pygame.RESIZABLE)
          and modes[-2][1] == pygame.FULLSCREEN and modes[-1] == ((900, 600), pygame.RESIZABLE))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-png", action="store_true")
    args = ap.parse_args()
    test_theme()
    test_tracker()
    test_attention()
    test_gaze()
    test_expressions()
    test_reflex_blink(not args.no_png)
    from test_perception import test_tongue, test_hands, test_objects, test_sensitivity, test_smile
    test_tongue(check)
    test_hands(check)
    test_objects(check)
    test_sensitivity(check)
    test_smile(check)
    test_detector()
    test_camera_bootstrap()
    test_camera_health()
    test_perf_and_png(not args.no_png)
    test_diagnostics()
    test_menu()
    from test_interface import test_interface
    test_interface(check)
    from test_stability import test_stability
    test_stability(check)
    from test_preferences import test_preferences
    test_preferences(check)
    from test_actions import test_actions
    test_actions(check)
    test_window_smoke()
    test_camera_metrics_smoke()
    bad = [n for n, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(bad)}/{len(RESULTS)} verificações OK")
    if bad:
        print("Falhas:", *bad, sep="\n  - ")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
