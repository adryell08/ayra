"""Esqueleto e gestos do corpo 3D da Ayra (sem OpenGL: só numpy, testável).

O modelo (models/nuria3d.npz, gerado por blender/nuria_3d.py) é feito de peças rígidas presas a pivôs.
Cada gesto descreve, para cada mão, onde fica o punho, para onde apontam os dedos e a palma e quanto cada
dedo dobra. Uma cinemática inversa de dois ossos (ombro → cotovelo → punho) transforma isso em matrizes.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

SIDES = {"LEFT": -1.0, "RIGHT": 1.0}
FINGERS = ("INDICADOR", "MEDIO", "ANELAR", "MINIMO")

# Nome técnico -> rótulo exibido (sala do operador, menu, chip). Ordem = ordem dos botões.
GESTURES: Dict[str, str] = {
    "tchau": "Tchau", "joia": "Joia", "dedo_do_meio": "Dedo do meio", "apontar": "Apontar",
    "paz": "Paz e amor", "ok": "OK", "aplaudir": "Aplaudir", "encolher_ombros": "Encolher ombros",
    "comemorar": "Comemorar", "continencia": "Continência", "pensar": "Pensar", "sim": "Sim (cabeça)",
    "nao": "Não (cabeça)",
}


def _n(v) -> np.ndarray:
    v = np.asarray(v, dtype=np.float64)
    return v / max(float(np.linalg.norm(v)), 1e-9)


def rot(axis, angle: float) -> np.ndarray:
    x, y, z = _n(axis)
    c, s = math.cos(angle), math.sin(angle)
    C = 1 - c
    return np.array([[c + x * x * C, x * y * C - z * s, x * z * C + y * s],
                     [y * x * C + z * s, c + y * y * C, y * z * C - x * s],
                     [z * x * C - y * s, z * y * C + x * s, c + z * z * C]])


def _m4(R=None, t=(0, 0, 0)) -> np.ndarray:
    M = np.eye(4)
    if R is not None:
        M[:3, :3] = R
    M[:3, 3] = t
    return M


def about(pivot, R) -> np.ndarray:
    """Rotação R em torno de um ponto."""
    p = np.asarray(pivot, dtype=np.float64)
    return _m4(R, p - R @ p)


@dataclass
class Hand:
    wrist: Tuple[float, float, float]                     # alvo do punho (mundo)
    fingers: Tuple[float, float, float]                   # direção dos dedos
    palm: Tuple[float, float, float]                      # normal da palma
    curl: Tuple[float, float, float, float] = (.25, .25, .3, .35)   # indicador, médio, anelar, mínimo (0..1)
    thumb: float = .25                                    # 0 = aberto, 1 = dobrado sobre a palma
    elbow: Tuple[float, float, float] = (0, .6, -.8)      # para onde o cotovelo aponta (x é espelhado)
    shrug: float = 0.0


@dataclass
class Pose:
    left: Hand
    right: Hand
    head: Tuple[float, float, float] = (0.0, 0.0, 0.0)    # giro, inclinação, tombar (rad)


def _mirror(h: Hand) -> Hand:
    """Versão da mão direita para a esquerda (espelha X)."""
    fx = lambda v: (-v[0], v[1], v[2])
    return replace(h, wrist=fx(h.wrist), fingers=fx(h.fingers), palm=fx(h.palm), elbow=fx(h.elbow))


def _lerp(a, b, w):
    return tuple(x + (y - x) * w for x, y in zip(a, b))


def blend(a: Pose, b: Pose, w: float) -> Pose:
    if w <= 0:
        return a
    if w >= 1:
        return b

    def hb(p: Hand, q: Hand) -> Hand:
        return Hand(_lerp(p.wrist, q.wrist, w), tuple(_n(_lerp(_n(p.fingers), _n(q.fingers), w))),
                    tuple(_n(_lerp(_n(p.palm), _n(q.palm), w))), _lerp(p.curl, q.curl, w),
                    p.thumb + (q.thumb - p.thumb) * w, tuple(_n(_lerp(_n(p.elbow), _n(q.elbow), w))),
                    p.shrug + (q.shrug - p.shrug) * w)
    return Pose(hb(a.left, b.left), hb(a.right, b.right), _lerp(a.head, b.head, w))


# ---------------------------------------------------------------------------
# Esqueleto
# ---------------------------------------------------------------------------
class Rig:
    def __init__(self, names, parents, pivots):
        self.names = [str(n) for n in names]
        self.index = {n: i for i, n in enumerate(self.names)}
        self.parent = [int(p) for p in parents]
        self.pivot = np.asarray(pivots, dtype=np.float64)
        self.arm = {}
        for label, s in SIDES.items():
            S, E, W = (self.pivot[self.index[f"{j}_{label}"]] for j in ("SHOULDER", "ELBOW", "WRIST"))
            down0 = _n(E - S)
            fwd0 = np.array([0.0, -1.0, 0.0])
            lat0 = _n(np.cross(fwd0, down0))
            fdown0 = _n(W - E)
            flat0 = _n(lat0 - lat0.dot(fdown0) * fdown0)
            ffwd0 = np.cross(fdown0, flat0)
            hand0 = self._hand_frame((0, 0, -1), (-s, 0, 0), s)
            t1 = self.pivot[self.index[f"THUMB_{label}_1"]]
            tdir = _n((-s * .22, -.55, -.80))
            self.arm[label] = dict(
                S=S, E=E, W=W, L1=float(np.linalg.norm(E - S)), L2=float(np.linalg.norm(W - E)),
                up0=np.column_stack([lat0, fwd0, down0]), fore0=np.column_stack([flat0, ffwd0, fdown0]),
                hand0=hand0, thumb_axis=_n(np.cross(tdir, _n((-s, .55, -.15)))), side=s, t1=t1)

    @classmethod
    def load(cls, data) -> "Rig":
        return cls(data["bone_names"], data["bone_parent"], data["bone_pivot"])

    @staticmethod
    def _hand_frame(fingers, palm, s) -> np.ndarray:
        d = _n(fingers)
        n = _n(np.asarray(palm, dtype=np.float64) - np.dot(palm, d) * d)
        t = -s * np.cross(d, n)
        return np.column_stack([d, n, t])

    def _ik(self, a, hand: Hand):
        S = a["S"] + np.array([0, 0, hand.shrug])
        L1, L2 = a["L1"], a["L2"]
        to = np.asarray(hand.wrist, dtype=np.float64) - S
        dist = float(np.clip(np.linalg.norm(to), abs(L1 - L2) + 1e-3, L1 + L2 - 1e-3))
        u = _n(to)
        W = S + u * dist
        pole = np.asarray(hand.elbow, dtype=np.float64)
        v = pole - pole.dot(u) * u
        if np.linalg.norm(v) < 1e-6:
            v = np.array([0, 1.0, 0]) - u[1] * u
        v = _n(v)
        cos_a = (L1 * L1 + dist * dist - L2 * L2) / (2 * L1 * dist)
        al = math.acos(float(np.clip(cos_a, -1, 1)))
        E = S + L1 * (math.cos(al) * u + math.sin(al) * v)
        down = _n(E - S)
        f = _n(W - E)
        fwd = f - f.dot(down) * down
        fwd = _n(fwd) if np.linalg.norm(fwd) > 1e-4 else _n(-(v - v.dot(down) * down))
        lat = np.cross(fwd, down)
        R_up = np.column_stack([lat, fwd, down]) @ a["up0"].T
        R_fore = np.column_stack([lat, np.cross(f, lat), f]) @ a["fore0"].T
        return S, R_up, R_fore

    def matrices(self, pose: Pose, bob: float = 0.0) -> np.ndarray:
        M = np.repeat(np.eye(4)[None], len(self.names), axis=0)
        if bob:
            for i in range(len(M)):
                M[i][2, 3] = bob
        yaw, pitch, roll = pose.head
        Rh = rot((0, 0, 1), yaw) @ rot((1, 0, 0), pitch) @ rot((0, 1, 0), roll)
        h = self.index["HEAD_PIVOT"]
        M[h] = M[h] @ about(self.pivot[h], Rh)
        for label, hand in (("LEFT", pose.left), ("RIGHT", pose.right)):
            a = self.arm[label]
            s = a["side"]
            S, R_up, R_fore = self._ik(a, hand)
            base = M[self.index["TORSO"]]
            up = base @ _m4(R_up, S - R_up @ a["S"])
            E = up[:3, :3] @ a["E"] + up[:3, 3]
            fore = _m4(R_fore, E - R_fore @ a["E"])
            W = fore[:3, :3] @ a["W"] + fore[:3, 3]
            R_hand = self._hand_frame(hand.fingers, hand.palm, s) @ a["hand0"].T
            wrist = _m4(R_hand, W - R_hand @ a["W"])
            M[self.index[f"SHOULDER_{label}"]] = up
            M[self.index[f"ELBOW_{label}"]] = fore
            M[self.index[f"WRIST_{label}"]] = wrist
            for k, fname in enumerate(FINGERS):
                c = float(hand.curl[k])
                i1, i2 = self.index[f"FINGER_{label}_{fname}_1"], self.index[f"FINGER_{label}_{fname}_2"]
                M[i1] = wrist @ about(self.pivot[i1], rot((0, 1, 0), s * c * 1.45))
                M[i2] = M[i1] @ about(self.pivot[i2], rot((0, 1, 0), s * c * 1.55))
            t = float(hand.thumb)
            i1, i2 = self.index[f"THUMB_{label}_1"], self.index[f"THUMB_{label}_2"]
            M[i1] = wrist @ about(self.pivot[i1], rot(a["thumb_axis"], t * 1.05))
            M[i2] = M[i1] @ about(self.pivot[i2], rot(a["thumb_axis"], t * .9))
        return M.astype(np.float32)


# ---------------------------------------------------------------------------
# Poses e gestos (coordenadas do modelo: frente = -Y, altura = Z; ombro direito em x=+1, z=1.3)
# ---------------------------------------------------------------------------
REST_R = Hand(wrist=(.62, -.62, .66), fingers=(-.35, -.85, -.30), palm=(-.25, .1, -1), curl=(.3, .35, .4, .45),
              thumb=.2, elbow=(.55, .55, -.6))
REST = Pose(_mirror(REST_R), REST_R)

FIST = (1.0, 1.0, 1.0, 1.0)
OPEN = (0.0, 0.0, 0.0, 0.0)


def _wave(t):
    return math.sin(2 * math.pi * 1.7 * t)


@dataclass
class Gesture:
    name: str
    duration: float
    pose: Callable[[float], Pose]          # t em segundos desde o início
    blend_in: float = .55
    blend_out: float = .6


def _g_tchau(t):
    w = _wave(t)
    r = Hand(wrist=(1.22 + .05 * w, -.38, 2.15), fingers=(.12 + .45 * w, -.05, 1), palm=(0, -1, 0), curl=OPEN,
             thumb=0, elbow=(.75, .25, -.6))
    return Pose(REST.left, r, (.12, -.04, -.06))


def _g_joia(t):
    r = Hand(wrist=(.58, -.92, 1.42), fingers=(-.47, -.88, 0), palm=(-.88, .47, 0), curl=FIST, thumb=-.25,
             elbow=(.45, .35, -.82))
    return Pose(REST.left, r, (.10, .03, 0))


def _g_dedo(t):
    pump = .03 * math.sin(2 * math.pi * 1.2 * t)
    r = Hand(wrist=(.55, -.80, 1.72 + pump), fingers=(0, -.1, 1), palm=(0, 1, 0), curl=(1, 0, 1, 1), thumb=1,
             elbow=(.6, .2, -.8))
    return Pose(REST.left, r, (.06, -.02, .04))


def _g_apontar(t):
    r = Hand(wrist=(1.52, -.62, 1.62), fingers=(.82, -.42, .38), palm=(.1, -.3, -1), curl=(0, 1, 1, 1), thumb=.7,
             elbow=(.5, .5, -.7))
    return Pose(REST.left, r, (.08, .04, 0))


def _g_paz(t):
    r = Hand(wrist=(.92, -.62, 2.08), fingers=(.15, 0, 1), palm=(0, -1, 0), curl=(0, 0, 1, 1), thumb=1,
             elbow=(.7, .2, -.7))
    return Pose(REST.left, r, (.10, 0, -.12))


def _g_ok(t):
    r = Hand(wrist=(.85, -.70, 1.92), fingers=(.05, 0, 1), palm=(0, -1, 0), curl=(.62, .05, 0, 0), thumb=.85,
             elbow=(.7, .2, -.7))
    return Pose(REST.left, r, (.08, 0, -.05))


def _g_aplaudir(t):
    gap = .5 + .5 * math.cos(2 * math.pi * 2.6 * t)
    r = Hand(wrist=(.16 + .14 * gap, -.92, 1.30), fingers=(0, -.75, .65), palm=(-1, 0, 0), curl=(.1, .1, .1, .1),
             thumb=.1, elbow=(.6, .35, -.7))
    return Pose(_mirror(r), r, (0, -.05, 0))


def _g_ombros(t):
    r = Hand(wrist=(1.42, -.62, .98), fingers=(.6, -.7, .25), palm=(0, 0, 1), curl=(.1, .1, .15, .2), thumb=0,
             elbow=(.2, .6, -.75), shrug=.07)
    return Pose(_mirror(r), r, (0, -.06, .14))


def _g_comemorar(t):
    b = .05 * math.sin(2 * math.pi * 2.2 * t)
    r = Hand(wrist=(1.22, -.18, 2.45 + b), fingers=(.2, -.1, 1), palm=(0, -1, 0), curl=(.9, .9, .9, .9),
             thumb=.8, elbow=(.85, .2, -.5))
    l = _mirror(replace(r, wrist=(1.22, -.18, 2.45 - b)))
    return Pose(l, r, (0, -.14, .05 * math.sin(2 * math.pi * 1.1 * t)))


def _g_continencia(t):
    r = Hand(wrist=(.98, -.42, 2.42), fingers=(-.55, -.25, .8), palm=(.15, -.55, -.8), curl=OPEN, thumb=.3,
             elbow=(.9, .1, -.4))
    return Pose(REST.left, r, (.0, -.03, 0))


def _g_pensar(t):
    r = Hand(wrist=(.32, -.82, 1.72), fingers=(-.15, .15, 1), palm=(-.2, -1, 0), curl=(.45, .8, .85, .9),
             thumb=.5, elbow=(.3, -.2, -.95))
    tilt = .03 * math.sin(2 * math.pi * .4 * t)
    return Pose(REST.left, r, (.18, -.12 + tilt, .10))


NOD_F, NOD_N = 1.6, 3            # "sim": 3 acenos para baixo e de volta (nunca acima do neutro: não descola do pescoço)
SHAKE_F, SHAKE_N = 1.5, 3        # "não": 3 idas e voltas, entrando e saindo suaves


HEAD_ONLY = ("sim", "nao")


def nod(t: float) -> float:
    """0 (neutro) .. 1 (cabeça baixa); começa e termina exatamente em 0."""
    if t <= 0 or t >= NOD_N / NOD_F:
        return 0.0
    return .5 - .5 * math.cos(2 * math.pi * NOD_F * t)


def shake(t: float) -> float:
    """-1 .. 1 (esquerda/direita), com envelope; começa e termina exatamente em 0."""
    end = SHAKE_N / SHAKE_F
    if t <= 0 or t >= end:
        return 0.0
    env = min(1.0, t / .25, (end - t) / .25)
    return math.sin(2 * math.pi * SHAKE_F * t) * env * env * (3 - 2 * env)


def head_gaze(name: str, t: float):
    """Mesmo "sim"/"não" para o rosto 2D (e o painel do Arduino): vira olhar para baixo/lados."""
    if name == "sim":
        return 0.0, .75 * nod(t)
    if name == "nao":
        return .8 * shake(t), 0.0
    return None


def _g_sim(t):
    return Pose(REST.left, REST.right, (0, .16 * nod(t), 0))


def _g_nao(t):
    return Pose(REST.left, REST.right, (.30 * shake(t), 0, 0))


LIBRARY: Dict[str, Gesture] = {g.name: g for g in [
    Gesture("tchau", 2.8, _g_tchau), Gesture("joia", 2.0, _g_joia), Gesture("dedo_do_meio", 2.2, _g_dedo),
    Gesture("apontar", 2.0, _g_apontar), Gesture("paz", 2.0, _g_paz), Gesture("ok", 2.0, _g_ok),
    Gesture("aplaudir", 2.4, _g_aplaudir), Gesture("encolher_ombros", 1.8, _g_ombros),
    Gesture("comemorar", 2.8, _g_comemorar), Gesture("continencia", 2.0, _g_continencia),
    Gesture("pensar", 2.6, _g_pensar), Gesture("sim", NOD_N / NOD_F + .1, _g_sim, .05, .05),
    Gesture("nao", SHAKE_N / SHAKE_F + .1, _g_nao, .05, .05),
]}
assert set(LIBRARY) == set(GESTURES)


def _smooth(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


class Animator:
    """Mistura repouso, olhar, fala e gestos. Um gesto novo substitui o atual (com transição)."""

    def __init__(self):
        self.current: Optional[str] = None
        self.started = 0.0
        self._from: Optional[Pose] = None      # pose no instante em que o gesto atual começou
        self.last: Pose = REST

    def play(self, name: str, now: float) -> bool:
        if name not in LIBRARY:
            return False
        self._from = self.last
        self.current = name
        self.started = now
        return True

    @property
    def busy(self) -> bool:
        return self.current is not None

    def pose(self, now: float, gaze=(0.0, 0.0), talk: float = 0.0, reduce_motion: bool = False) -> Pose:
        breath = 0.0 if reduce_motion else math.sin(now * 1.3)
        sway = 0.0 if reduce_motion else math.sin(now * 2.7)
        base = replace(REST, head=(float(gaze[0]) * .42, float(gaze[1]) * .22 + .02 * breath * .3,
                                   .03 * talk * sway))
        if talk > 0 and not reduce_motion:                # mãos acompanham a fala, de leve
            k = .05 * talk
            r = replace(REST.right, wrist=_lerp(REST.right.wrist, (.62, -.70, .74 + k * math.sin(now * 5.1)), 1))
            l = replace(REST.left, wrist=_lerp(REST.left.wrist, (-.62, -.68, .70 + k * math.sin(now * 4.3 + 1)), 1))
            base = replace(base, left=l, right=r)
        pose = base
        if self.current is not None:
            g = LIBRARY[self.current]
            t = now - self.started
            if t >= g.duration:
                self.current = None
            elif self.current in HEAD_ONLY:
                # sim/não: o movimento SOMA ao olhar atual (sem puxar a cabeça para a frente nem dar tranco);
                # com "reduzir movimento" continua visível, só menor (o gesto é a mensagem)
                k = .5 if reduce_motion else 1.0
                gh = g.pose(t).head
                pose = replace(base, head=(base.head[0] + k * gh[0], base.head[1] + k * gh[1], base.head[2] + k * gh[2]))
            else:
                target = g.pose(0.0 if reduce_motion else t)
                # cabeça: o gesto soma ao olhar
                target = replace(target, head=tuple(a + b for a, b in zip(target.head, (base.head[0] * .5, base.head[1] * .5, 0))))
                w_in = _smooth(t / g.blend_in)
                w_out = _smooth((g.duration - t) / g.blend_out)
                start = self._from if (self._from is not None and t < g.blend_in) else base
                pose = blend(start if w_in < 1 else base, target, min(w_in, w_out))
        self.last = pose
        return pose
