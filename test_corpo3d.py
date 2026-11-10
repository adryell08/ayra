"""Testes do corpo 3D (modelo do Blender, esqueleto, gestos, desenho OpenGL e integração com IA/operador).

Uso: python test_corpo3d.py        (o desenho usa a placa de vídeo; sem OpenGL, essa parte é pulada e avisada)
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import numpy as np
import pygame

import gestos
from gestos import GESTURES, LIBRARY, REST, Animator, Rig

RESULTS = []


def check(name, cond):
    RESULTS.append((name, bool(cond)))
    print(f"  [{'OK' if cond else 'FALHOU'}] {name}")


def model():
    return np.load(HERE / "models" / "nuria3d.npz")


def world(M, i, p):
    return M[i][:3, :3] @ np.asarray(p) + M[i][:3, 3]


def test_model():
    print("Modelo 3D (gerado pelo script do Blender)")
    d = model()
    names = [str(n) for n in d["bone_names"]]
    need = {"HEAD_PIVOT", "TORSO"} | {f"{j}_{s}" for j in ("SHOULDER", "ELBOW", "WRIST") for s in ("LEFT", "RIGHT")} \
        | {f"FINGER_{s}_{f}_{k}" for s in ("LEFT", "RIGHT") for f in gestos.FINGERS for k in (1, 2)} \
        | {f"THUMB_{s}_{k}" for s in ("LEFT", "RIGHT") for k in (1, 2)}
    check("pivôs: cabeça, ombros, cotovelos, punhos, 4 dedos + polegar por mão", need <= set(names))
    mats = [str(n) for n in d["mat_names"]]
    check("visor próprio para o rosto atual (sem olhos/boca de pontos no modelo)", "10 | Visor do rosto" in mats)
    led = mats.index("06 | LED azul")
    head = names.index("HEAD_PIVOT")
    on_head = d["bone"] == head
    check("nenhum LED na frente do visor (olhos do Blender removidos)",
          not np.any((d["mat"] == led) & on_head & (d["pos"][:, 1] < -.3) & (np.abs(d["pos"][:, 0]) < .5)))
    tris = len(d["idx"]) // 3
    check(f"malha leve o bastante para tempo real ({tris // 1000} mil triângulos)", 50_000 < tris < 600_000)
    check("índices válidos", int(d["idx"].max()) < len(d["pos"]))
    check("cada peça presa a um pivô existente", int(d["bone"].max()) < len(names))
    script = (HERE / "blender" / "nuria_3d.py").read_text(encoding="utf-8")
    check("script do Blender versionado, sem a matriz de pontos dos olhos", "def dots(" not in script and "def exportar" in script)


def test_rig():
    print("Esqueleto e cinemática inversa")
    rig = Rig.load(model())
    M = rig.matrices(REST)
    check("pose de repouso: matrizes finitas", np.isfinite(M).all() and M.shape == (len(rig.names), 4, 4))
    ok_len, ok_reach, worst = True, True, 0.0
    for name, g in LIBRARY.items():
        for t in (0.0, g.duration * .3, g.duration * .6):
            pose = g.pose(t)
            M = rig.matrices(pose)
            for label, hand in (("LEFT", pose.left), ("RIGHT", pose.right)):
                a = rig.arm[label]
                S = world(M, rig.index[f"SHOULDER_{label}"], a["S"])
                E = world(M, rig.index[f"ELBOW_{label}"], a["E"])
                W = world(M, rig.index[f"WRIST_{label}"], a["W"])
                ok_len &= abs(np.linalg.norm(E - S) - a["L1"]) < 1e-4 and abs(np.linalg.norm(W - E) - a["L2"]) < 1e-4
                err = float(np.linalg.norm(W - np.asarray(hand.wrist)))
                worst = max(worst, err)
                ok_reach &= err < .02
                fe = world(M, rig.index[f"ELBOW_{label}"], a["W"])
                ok_len &= np.allclose(fe, W, atol=1e-5)
    check("ossos rígidos: braço e antebraço mantêm o comprimento em todos os gestos", ok_len)
    check(f"punho chega ao alvo em todos os gestos (erro máx. {worst * 100:.1f} cm do modelo)", ok_reach)

    def tip(M, label, finger):
        i = rig.index[f"FINGER_{label}_{finger}_2"]
        return world(M, i, rig.pivot[i] + np.array([0, 0, -.09]))

    pose = LIBRARY["joia"].pose(1.0)
    M = rig.matrices(pose)
    t2 = rig.index["THUMB_RIGHT_2"]
    thumb = world(M, t2, rig.pivot[t2] + .06 * gestos._n((-.22, -.55, -.80)))
    W = np.asarray(pose.right.wrist)
    check("joia: polegar para cima e acima dos dedos fechados",
          thumb[2] > W[2] + .05 and all(thumb[2] > tip(M, "RIGHT", f)[2] for f in gestos.FINGERS))
    M = rig.matrices(LIBRARY["dedo_do_meio"].pose(1.0))
    tips = {f: tip(M, "RIGHT", f)[2] for f in gestos.FINGERS}
    check("dedo do meio: só o médio fica levantado", max(tips, key=tips.get) == "MEDIO"
          and tips["MEDIO"] - max(v for k, v in tips.items() if k != "MEDIO") > .08)
    pose = LIBRARY["apontar"].pose(1.0)
    M = rig.matrices(pose)
    d = gestos._n(pose.right.fingers)
    reach = {f: float((tip(M, "RIGHT", f) - np.asarray(pose.right.wrist)) @ d) for f in gestos.FINGERS}
    check("apontar: indicador é o que vai mais longe", max(reach, key=reach.get) == "INDICADOR")
    pose = LIBRARY["tchau"].pose(.3)
    M = rig.matrices(pose)
    check("tchau: mão acima do ombro, dedos para cima",
          pose.right.wrist[2] > 1.9 and tip(M, "RIGHT", "MEDIO")[2] > pose.right.wrist[2])
    pose = LIBRARY["sim"].pose(.12)
    check("sim/não: só a cabeça mexe (braços em repouso)", pose.right == REST.right and abs(pose.head[1]) > .05)


def test_animator():
    print("Animação e transições")
    anim = Animator()
    check("gesto desconhecido é ignorado", not anim.play("voar", 0.0) and not anim.busy)
    rig = Rig.load(model())
    check("gesto conhecido começa", anim.play("tchau", 10.0) and anim.busy)
    prev, jump = None, 0.0
    for k in range(0, int(3.0 * 60)):
        now = 10.0 + k / 60
        M = rig.matrices(anim.pose(now))
        W = world(M, rig.index["WRIST_RIGHT"], rig.arm["RIGHT"]["W"])
        if prev is not None:
            jump = max(jump, float(np.linalg.norm(W - prev)))
        prev = W
    check(f"movimento contínuo, sem saltos (máx. {jump:.3f} por quadro)", jump < .08)
    check("ao terminar, volta ao repouso", not anim.busy and np.allclose(anim.last.right.wrist, REST.right.wrist))
    anim.play("joia", 20.0)
    anim.pose(20.8)
    anim.play("tchau", 20.8)
    a = anim.pose(20.8).right.wrist
    b = anim.pose(20.8 + 1 / 60).right.wrist
    check("trocar de gesto no meio emenda sem salto", np.linalg.norm(np.subtract(a, b)) < .05)
    p = anim.pose(30.0, gaze=(1.0, 0.0))
    check("cabeça acompanha o olhar (rosto rastreado)", p.head[0] > .3)
    p2 = Animator().pose(5.0, talk=1.0, reduce_motion=True)
    check("movimento reduzido: sem balanço de fala", p2.head[2] == 0)
    for name, axis in (("sim", 1), ("nao", 0)):
        g = LIBRARY[name]
        an = Animator()
        an.play(name, 0.0)
        idle = Animator()
        vals = [an.pose(k / 60).head[axis] - idle.pose(k / 60).head[axis] for k in range(int((g.duration + .2) * 60))]
        jump = max(abs(x - y) for x, y in zip(vals, vals[1:]))
        check(f"{GESTURES[name]}: começa e termina no neutro, sem tranco (máx. {jump:.3f} rad/quadro)",
              abs(vals[0]) < 1e-3 and abs(vals[-1]) < 1e-3 and jump < .06 and max(abs(v) for v in vals) > .12)
    for rm in (False, True):
        an, idle = Animator(), Animator()
        an.play("nao", 0.0)
        yaws = [an.pose(k / 60, gaze=(.7, .2), reduce_motion=rm).head[0]
                - idle.pose(k / 60, gaze=(.7, .2), reduce_motion=rm).head[0] for k in range(130)]
        jump = max(abs(x - y) for x, y in zip(yaws, yaws[1:]))
        check(f"não com rosto rastreado{' e movimento reduzido' if rm else ''}: gira de verdade, sem puxar a cabeça "
              f"para a frente nem tique (amplitude {max(map(abs, yaws)):.2f} rad, salto {jump:.3f})",
              max(map(abs, yaws)) > (.12 if rm else .25) and abs(yaws[0]) < 1e-3 and abs(yaws[-1]) < 1e-3 and jump < .06)
    an = Animator()
    an.play("sim", 0.0)
    pitches = [an.pose(k / 60).head[1] for k in range(120)]
    check("sim: cabeça só desce e volta (não sobe além do neutro, não descola do pescoço)", min(pitches) >= -1e-6)
    check("sim/não também no rosto 2D e no painel LED (o olhar faz o movimento)",
          gestos.head_gaze("sim", .3)[1] > .5 and abs(gestos.head_gaze("nao", .15)[0]) > .4
          and gestos.head_gaze("sim", 9) == (0.0, 0.0) and "head_gaze(body[\"head2d\"]" in (HERE / "main.py").read_text(encoding="utf-8"))


def test_channels():
    print("Canais independentes: cabeça, braço esquerdo e braço direito")
    wr = lambda h: np.array(h.wrist)
    check("canais: um braço só nos de um braço, os dois nos de dois, nenhum nos de cabeça",
          gestos.gesture_arms("tchau") == ("R",) and gestos.gesture_arms("aplaudir") == ("L", "R")
          and gestos.gesture_arms("nao") == () and all(isinstance(gestos.gesture_arms(n), tuple) for n in LIBRARY))
    # simultaneidade: tchau (braço direito) + joia (esquerdo) + não (cabeça)
    an, solo_r, solo_l, solo_h = Animator(), Animator(), Animator(), Animator()
    an.play("tchau", 0.0)
    an.play("joia", 0.0, side="left")
    an.play("nao", 0.0)
    solo_r.play("tchau", 0.0)
    solo_l.play("joia", 0.0, side="left")
    solo_h.play("nao", 0.0)
    check("simultâneos: três gestos ativos ao mesmo tempo", sorted(an.active()) == ["joia", "nao", "tchau"] and an.busy)
    worst_r = worst_l = 0.0
    yaw_range = [0.0, 0.0]
    for k in range(int(2.0 * 60)):
        now = k / 60
        p, pr, pl, ph = an.pose(now), solo_r.pose(now), solo_l.pose(now), solo_h.pose(now)
        worst_r = max(worst_r, float(np.abs(wr(p.right) - wr(pr.right)).max()))
        worst_l = max(worst_l, float(np.abs(wr(p.left) - wr(pl.left)).max()))
        yaw_range = [min(yaw_range[0], p.head[0]), max(yaw_range[1], p.head[0])]
    check(f"simultâneos: cada braço faz exatamente o que faria sozinho (desvio {max(worst_r, worst_l):.1e})",
          worst_r < 1e-9 and worst_l < 1e-9)
    check("simultâneos: a cabeça balança (não) com os dois braços levantados", yaw_range[1] - yaw_range[0] > .2)
    p = an.pose(1.0)
    check("braço esquerdo levantado é do lado esquerdo (espelhado) e o direito do direito",
          p.left.wrist[0] < 0 < p.right.wrist[0] and p.left.wrist[2] > REST.left.wrist[2] + .3)
    # conflito: mesmo braço substitui; outro canal segue
    an = Animator()
    an.play("tchau", 0.0)
    an.play("paz", 0.1)
    check("conflito: mesmo braço — o gesto novo substitui o antigo", an.active() == ["paz"])
    an.play("nao", 0.1)
    an.play("joia", 0.2, side="left")
    check("coexistência: cabeça + braço direito + braço esquerdo", sorted(an.active()) == ["joia", "nao", "paz"])
    an.play("nao", 0.3)
    check("conflito: cabeça substitui só o gesto de cabeça", sorted(an.active()) == ["joia", "nao", "paz"])
    # dois braços reservam ambos
    an = Animator()
    an.play("tchau", 0.0)
    an.play("joia", 0.0, side="left")
    an.play("aplaudir", 0.5)
    check("dois braços: reserva ambos e encerra os de um braço", an.active() == ["aplaudir"])
    an.play("nao", 0.5)
    check("dois braços convivem com gesto de cabeça", sorted(an.active()) == ["aplaudir", "nao"])
    an.play("tchau", 1.0)
    check("um braço interrompe gesto de dois braços (substitui-o inteiro)", sorted(an.active()) == ["nao", "tchau"])
    # término suave: o braço solto volta ao repouso sem salto e o gesto de um braço termina no repouso
    an = Animator()
    an.play("aplaudir", 0.0)
    prev, jump = None, 0.0
    for k in range(int(1.0 * 60)):
        now = k / 60
        if abs(now - 0.8) < 1e-9:
            an.play("tchau", now)                       # interrompe os dois braços: o esquerdo deve ser solto suavemente
        p = an.pose(now)
        cur = wr(p.left)
        if prev is not None:
            jump = max(jump, float(np.linalg.norm(cur - prev)))
        prev = cur
    check(f"término suave: braço liberado não dá tranco (máx. {jump:.3f} por quadro)", jump < .08)
    for k in range(int(1.0 * 60), int(2.0 * 60)):
        an.pose(k / 60)
    check("braço liberado chega ao repouso", np.allclose(wr(an.pose(2.0).left), wr(REST.left), atol=1e-6))
    for name in LIBRARY:
        for side in ("right", "left"):
            an = Animator()
            an.play(name, 0.0, side=side)
            end = LIBRARY[name].duration + .05
            a = an.pose(0.0)
            for k in range(int(end * 60) + 1):
                a = an.pose(k / 60)
            ok = (not an.busy and np.allclose(wr(a.left), wr(REST.left), atol=1e-6)
                  and np.allclose(wr(a.right), wr(REST.right), atol=1e-6))
            if not ok:
                break
        if not ok:
            break
    check("término: todo gesto (direita/esquerda) termina em repouso e libera os canais", ok)
    # cabeça de um gesto de braço some quando um gesto de cabeça assume (sem tranco)
    an = Animator()
    an.play("pensar", 0.0)                              # inclina a cabeça
    heads, prev, jump = [], None, 0.0
    for k in range(int(1.6 * 60)):
        now = k / 60
        if abs(now - 1.0) < 1e-9:
            an.play("sim", now)
        h = np.array(an.pose(now).head)
        if prev is not None:
            jump = max(jump, float(np.abs(h - prev).max()))
        prev = h
    check(f"cabeça: floreio do braço cede a um gesto de cabeça sem tranco (máx. {jump:.3f} rad/quadro)", jump < .06)
    # compatibilidade
    an = Animator()
    an = Animator()
    an.play("tchau", 0.0, side="xyz")
    an.play("aplaudir", 5.0, side="left")
    an.play("nao", 5.0, side="left")
    check("animador: lado inválido = direito; dois braços e cabeça ignoram o lado",
          not any(x.left_side for x in an.acts) and sorted(an.active()) == ["aplaudir", "nao"])
    check("compat: play(nome, agora) e atributos antigos", an.play("tchau", 2.0) and an.current == "tchau"
          and an.started == 2.0 and an.busy and not Animator().play("voar", 0) and Animator().current is None)
    one, lifted, still = Animator(), 0.0, True
    one.play("ok", 0.0)
    for k in range(int(2.2 * 60)):
        po = one.pose(k / 60)
        lifted = max(lifted, float(po.right.wrist[2]) - float(REST.right.wrist[2]))
        still &= bool(np.allclose(wr(po.left), wr(REST.left)))
    check("compat: um gesto sozinho levanta só o braço direito; o esquerdo fica em repouso", lifted > .5 and still)
    multi = Animator()
    multi.play("tchau", 0.0)
    multi.play("joia", 0.0, side="left")
    multi.play("nao", 0.0)
    single = Animator()
    single.play("tchau", 0.0)
    n = 2000
    t0 = time.perf_counter()
    for k in range(n):
        single.pose(0.5 + (k % 100) / 100)
    t_single = (time.perf_counter() - t0) / n * 1e6
    t0 = time.perf_counter()
    for k in range(n):
        multi.pose(0.5 + (k % 100) / 100)
    t_multi = (time.perf_counter() - t0) / n * 1e6
    check(f"custo de pose: {t_single:.0f} µs (1 gesto) e {t_multi:.0f} µs (3 gestos) — ordem de décimos de ms",
          t_single < 400 and t_multi < 800)


def test_render():
    print("Desenho 3D (OpenGL)")
    import corpo3d
    ok, why = corpo3d.available()
    check("disponibilidade detectada", ok or bool(why))
    try:
        body = corpo3d.Body3D()
    except Exception as error:
        print(f"  [PULADO] sem OpenGL 3.3 neste computador: {error}")
        return
    pygame.init()
    pygame.display.set_mode((320, 240))
    red, black = pygame.Surface((320, 180)), pygame.Surface((320, 180))
    red.fill((255, 40, 40))
    t = time.perf_counter()
    a = body.render((480, 320), black, 1.0, bg=(10, 12, 16))
    ms = (time.perf_counter() - t) * 1000
    b = body.render((480, 320), red, 1.0, bg=(10, 12, 16))
    A, B = pygame.surfarray.array3d(a).astype(int), pygame.surfarray.array3d(b).astype(int)
    check("imagem desenhada (robô sobre o fundo)", A.std() > 10 and a.get_size() == (480, 320))
    diff = np.abs(A - B).sum(axis=2) > 30
    ys, xs = np.nonzero(diff.T)
    check("o rosto aparece no visor (e só no visor)", diff.sum() > 800 and ys.mean() < 320 * .45
          and abs(xs.mean() - 240) < 40)
    check(f"fundo igual ao do tema ({tuple(A[2, 2])})", tuple(A[2, 2]) == (10, 12, 16))
    body.set_adaptive(True); body.q, body.scale = 2, 0.7; body.set_adaptive(False)
    check("qualidade automática desligada volta ao máximo e não desce mais",
          body.q == 0 and body.scale == 1.0 and not body.adaptive)
    body.render_ms = 99.0; body._renders = 19; body._adapt(99.0)
    check("desligada, mesmo pesado, a qualidade fica no máximo", body.q == 0)
    body.set_adaptive(True); body.render_ms = 99.0; body._renders = 19; body._adapt(99.0)
    check("ligada, pesado: desce um degrau", body.q == 1)
    body.set_adaptive(False)
    body.play("comemorar", 1.0)
    c = body.render((480, 320), black, 2.0, bg=(10, 12, 16))
    C = pygame.surfarray.array3d(c).astype(int)
    check("gesto muda a imagem (braços para cima)", (np.abs(C - A).sum(axis=2) > 30).sum() > 2000)
    s = body.render((200, 300), black, 1.0, bg=(10, 12, 16))
    check("janela estreita/alta também enquadra", s.get_size() == (200, 300))
    print(f"    (primeiro quadro {ms:.0f} ms neste computador)")
    previews = HERE / "previews"
    previews.mkdir(exist_ok=True)
    face = _face()
    W, H, cols = 420, 300, 5
    names = ["repouso"] + list(GESTURES)
    sheet = pygame.Surface((W * cols, H * ((len(names) + cols - 1) // cols)))
    sheet.fill((10, 12, 16))
    font = pygame.font.SysFont(None, 24)
    for k, name in enumerate(names):
        body.anim = gestos.Animator()
        now = 50.0
        if name != "repouso":
            body.play(name, now)
            now += LIBRARY[name].duration * .55
        img = body.render((W, H), face, now, bg=(10, 12, 16))
        sheet.blit(img, ((k % cols) * W, (k // cols) * H))
        sheet.blit(font.render(GESTURES.get(name, "Repouso"), True, (230, 230, 240)), ((k % cols) * W + 10, (k // cols) * H + 8))
    pygame.image.save(sheet, str(previews / "nuria_3d_gestos.png"))
    big = body.render((1280, 720), face, 50.0, bg=(10, 12, 16))
    pygame.image.save(big, str(previews / "nuria_3d_1280x720.png"))
    check("prévias salvas (previews/nuria_3d_*.png)", (previews / "nuria_3d_gestos.png").exists())
    body.release()


def _face():
    import theme
    from renderer import Renderer
    from robot import Robot
    rb = Robot((80, 45))
    fr = None
    for i in range(60):
        fr = rb.step(None, i / 60, 1 / 60)
    r = Renderer(pygame.Surface((640, 360)), (80, 45), theme.SCHEMES["dark"])
    r.draw_panel(fr, 1 / 60, True)
    return r.screen.subsurface(r.panel).copy()


def test_ai_and_operator():
    print("IA e sala do operador")
    import assistente_realtime as ar
    import operador
    import test_realtime as T
    enum = ar.TOOLS[0]["parameters"]["properties"]["gesto"]["enum"]
    check("ferramenta expressar oferece todos os gestos à IA (inclusive dedo do meio)",
          set(enum) == set(GESTURES) | {"nenhum"} and "dedo_do_meio" in enum)
    check("instruções avisam a IA de que ela tem braços", "braços" in ar.INSTRUCTIONS)
    h = T.Harness()
    a = h.a
    v0 = a.snapshot().gesture_version
    a.ask_text("dá tchau pra turma")
    check("IA (simulada) pede o gesto tchau pela função expressar",
          h.wait(lambda: a.snapshot().gesture_version > v0 and a.snapshot().gesture == "tchau", 5))
    h.idle()
    v1 = a.snapshot().gesture_version
    check("operador/menu: pedido direto de gesto", a.request_gesture("joia") and a.snapshot().gesture_version == v1 + 1)
    check("o mesmo gesto pode repetir", a.request_gesture("joia") and a.snapshot().gesture_version == v1 + 2)
    check("gesto inválido é recusado", not a.request_gesture("x; drop") and a.snapshot().gesture == "joia")
    check("lado: padrão continua direito (comando antigo sem lado)",
          a.request_gesture("tchau") and a.snapshot().gesture_side == "")
    check("lado: esquerdo e variações aceitos em gesto de um braço",
          all(a.request_gesture("tchau", s) and a.snapshot().gesture_side == "left"
              for s in ("esquerdo", "esquerda", "left", "Esquerdo", " L ")))
    check("lado: direito explícito vira o padrão",
          a.request_gesture("joia", "direito") and a.snapshot().gesture_side == "")
    check("lado inválido não recusa o gesto: usa o direito",
          all(a.request_gesture("tchau", s) and a.snapshot().gesture_side == "" for s in ("xyz", "", None, 7, "esq; drop")))
    check("lado é ignorado em gestos de dois braços e de cabeça",
          all(a.request_gesture(n, "esquerdo") and a.snapshot().gesture_side == "" for n in ("aplaudir", "comemorar", "sim", "nao")))
    check("lado: gesto inválido continua recusado mesmo com lado", not a.request_gesture("voar", "esquerdo"))
    v2 = a.snapshot().gesture_version
    a.ask_text("dá tchau com a mão esquerda")
    check("IA (simulada) pede o tchau com o braço esquerdo pelo campo lado",
          h.wait(lambda: a.snapshot().gesture_version > v2 and a.snapshot().gesture == "tchau"
                 and a.snapshot().gesture_side == "left", 5))
    h.idle()
    props = ar.TOOLS[0]["parameters"]["properties"]
    check("ferramenta: lado é opcional (não entra em required) e enum curto",
          props["lado"]["enum"] == ["direito", "esquerdo"] and ar.TOOLS[0]["parameters"]["required"] == ["emocao"])
    main_src = (HERE / "main.py").read_text(encoding="utf-8")
    check("despacho: operador e corpo 3D repassam o lado",
          'request_gesture(str(data.get("nome", "")), data.get("lado"), source="operator")' in main_src
          and "body_gesture(cmd.name, now, cmd.side)" in main_src and 'body["obj"].play(name, now, side or "right")' in main_src)
    h.close() if hasattr(h, "close") else None
    check("servidor aceita os comandos gesture e body", {"gesture", "body"} <= set(operador.COMMANDS))
    html = (HERE / "operador_web" / "index.html").read_text(encoding="utf-8")
    buttons = re.findall(r'data-g="([a-z_]+)"', html)
    check("página do operador: um botão por gesto, na mesma ordem", buttons == list(GESTURES))
    check("página do operador: chave Corpo 3D e estado do gesto", 'id="body3d"' in html and 'id="gest"' in html)
    check("página do operador: escolha de braço enviada com o gesto, sem criar botões novos de gesto",
          'id="gside"' in html and 'lado: $("gside").value' in html)


def test_menu():
    print("Ajustes → Corpo 3D")
    import menu
    import theme
    from renderer import HudInfo
    m = menu.OptionsMenu()
    hud = HudInfo()
    roots = [a for a, *_ in m.items(hud, theme.SCHEMES["dark"])]
    check("item Corpo 3D na tela de Ajustes", "body" in roots)
    m.activate("body")
    items = m.items(hud, theme.SCHEMES["dark"])
    check("liga/desliga + um item por gesto", items[0][0] == "body:enabled"
          and [a.split(":")[2] for a, *_ in items[1:] if a.startswith("body:gesture:")] == list(GESTURES))
    check("item 'Qualidade automática do 3D' logo após o liga/desliga (liga/desliga próprio)",
          items[1][0] == "body:quality" and items[1][3] in ("on", "off"))
    hud.body = dict(enabled=True, auto=False)
    q = [i for i in m.items(hud, theme.SCHEMES["dark"]) if i[0] == "body:quality"][0]
    check("desligada: mostra 'sempre no máximo'", q[3] == "off" and "máximo" in q[2])
    hud.body = dict(enabled=False, error="instale o pacote moderngl")
    check("mostra o motivo quando o 3D não está disponível", "moderngl" in m.items(hud, theme.SCHEMES["dark"])[0][2])
    check("tocar num gesto mantém a aba aberta", m.activate("body:gesture:joia") == (True, "body:gesture:joia") and m.view == "body")


def test_app():
    print("App com o corpo 3D")
    import corpo3d
    import main as app
    cfg = HERE / "corpo3d.local.json"
    old = cfg.read_text(encoding="utf-8") if cfg.exists() else None
    try:
        cfg.write_text(json.dumps({"ligado": True}), encoding="utf-8")
        code = app.main(["--sim", "--ia", "simulada", "--sem-registro", "--run-seconds", "2.5"])
        check("abre com o corpo 3D ligado e fecha normalmente", code == 0)
        real = corpo3d.available
        corpo3d.available = lambda: (False, "instale o pacote moderngl")
        try:
            code = app.main(["--sim", "--ia", "simulada", "--sem-registro", "--run-seconds", "1.5"])
        finally:
            corpo3d.available = real
        check("sem OpenGL/moderngl: continua no rosto 2D, sem travar", code == 0
              and json.loads(cfg.read_text(encoding="utf-8")).get("ligado") is True)
    finally:
        if old is None:
            cfg.unlink(missing_ok=True)
        else:
            cfg.write_text(old, encoding="utf-8")


if __name__ == "__main__":
    test_model()
    test_rig()
    test_animator()
    test_channels()
    test_render()
    test_ai_and_operator()
    test_menu()
    test_app()
    ok = sum(1 for _, v in RESULTS if v)
    print(f"\n{ok}/{len(RESULTS)} verificações OK")
    sys.exit(0 if ok == len(RESULTS) else 1)
