"""Geração do rosto como um campo de LEDs (matriz de células).

Tudo é calculado em coordenadas de *célula* e com antialiasing analítico: cada LED
recebe um brilho entre 0 e 1. Assim um movimento de 0,3 célula acende o LED vizinho
pela metade, e o olhar desliza suavemente mesmo numa grade baixa — exatamente o que
um painel real com PWM faria. A mesma saída (matriz de brilhos) alimenta uma tela
agora e um painel de LEDs físico no futuro.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

import numpy as np

from expressions import ARCH, CHEEK, DY, GLINT, LID, OPEN, RADIUS, TILT, WIDTH

# Layout relativo à altura H da grade (independe da resolução da matriz)
EYE_DX = 0.35        # olhos mais próximos, sem se tocar no olhar extremo
EYE_CY = 0.48        # altura do centro dos olhos
EYE_HW = 0.160       # olhos menores: mais espaço para o olhar "andar"
EYE_HH = 0.195       # idem na vertical
GAZE_X = 0.11        # deslocamento máximo horizontal do olhar (maior = sensação de movimento)
GAZE_Y = 0.06        # deslocamento máximo vertical do olhar

_Z3 = np.array([[1, 1, 1], [0, 1, 0], [1, 1, 1]], dtype=np.float32)
_Z5 = np.array([[1, 1, 1, 1, 1], [0, 0, 0, 1, 0], [0, 0, 1, 0, 0], [0, 1, 0, 0, 0], [1, 1, 1, 1, 1]],
               dtype=np.float32)

_grid_cache: Dict[Tuple[int, int], Tuple[np.ndarray, np.ndarray]] = {}


def _grid(W: int, H: int):
    key = (W, H)
    if key not in _grid_cache:
        _grid_cache[key] = np.meshgrid(np.arange(W, dtype=np.float32) + 0.5,
                                       np.arange(H, dtype=np.float32) + 0.5)
    return _grid_cache[key]


def _sdf_box(px, py, hw, hh, r):
    qx = np.abs(px) - (hw - r)
    qy = np.abs(py) - (hh - r)
    outside = np.hypot(np.maximum(qx, 0.0), np.maximum(qy, 0.0))
    inside = np.minimum(np.maximum(qx, qy), 0.0)
    return outside + inside - r


def _eye(X, Y, cx, cy, hw, hh, p, blink, side, lid_extra):
    """Cobertura (0..1) e brilho do olhar de um olho. side: -1 esquerdo, +1 direito."""
    open_ = max(p[OPEN] * (0.04 + 0.96 * blink), 0.03)
    w = hw * p[WIDTH]
    h = max(hh * open_, 0.55)                       # nunca menor que ~meia célula
    r = min(w, h) * min(p[RADIUS] * 2.0, 1.0)
    lx = X - cx
    ly = Y - (cy + p[DY] * hh)
    d = _sdf_box(lx, ly, w, h, r)

    # pálpebra superior: reta (sonolento) + inclinada (bravo / triste)
    lid = float(np.clip(p[LID] + lid_extra, 0.0, 0.9))
    t = float(p[TILT])
    u = (-side * lx) / w                            # +1 no canto interno, -1 no externo
    slope = t * 0.95 * h / w
    y_line = -h + 2.0 * h * lid + t * 0.95 * h * u + h * 0.40 * abs(t)
    cut = (y_line - ly) / np.sqrt(1.0 + slope * slope)
    d = np.maximum(d, cut)

    # pálpebra inferior subindo em arco (olho que sorri)
    k = float(np.clip(p[CHEEK], 0.0, 0.92))
    if k > 0.01:
        R = w * 1.15
        ccy = h + R - 2.0 * h * k
        d = np.maximum(d, R - np.hypot(lx, ly - ccy))

    cov = np.clip(0.5 - d, 0.0, 1.0)

    # olho feliz: arco ∩ de espessura uniforme; a troca é uma dissolução entre as duas formas
    m = float(np.clip(p[ARCH], 0.0, 1.0))
    if m > 0.01:
        wx = 0.90 * w
        sag = max(0.60 * h, 0.35)
        y_a = -0.30 * h
        Rm = (wx * wx + sag * sag) / (2.0 * sag)
        t2 = max(0.20 * h, 0.5)
        ring = np.abs(np.hypot(lx, ly - (y_a + Rm)) - Rm) - t2
        d_arch = np.maximum(ring, ly - (y_a + sag))
        cov = (1.0 - m) * cov + m * np.clip(0.5 - d_arch, 0.0, 1.0)
    return cov, open_, w, h, d


def _iris(X, Y, cx, cy, w, h, side, gx, gy, pupil, conv, scale, open_=1.0):
    """Íris + pupila sem esclera (estilo robô): a íris desliza dentro do olho; o formato do olho/pálpebras a recorta.

    Brilho (LED) = corpo do olho apagado (0,20) + íris com gradiente radial (centro 0,74 -> borda 1,0) com a pupila escura no centro.
    """
    ri = 0.88 * w * scale * (0.78 + 0.22 * min(float(open_), 1.4))
    ox = float(np.clip(gx * 0.42 * w - side * conv * 0.30 * w, -0.55 * w, 0.55 * w))
    oy = float(np.clip(gy * 0.38 * h, -0.45 * h, 0.45 * h))
    r = np.hypot(X - (cx + ox), Y - (cy + oy))
    iris = np.clip(ri - r + 0.5, 0.0, 1.0)
    shade = 0.74 + 0.26 * np.clip(r / max(ri, 1e-6), 0.0, 1.0) ** 1.4
    pup = np.clip(pupil * ri - r + 0.5, 0.0, 1.0)
    return ((1.0 - iris) * 0.20 + iris * shade * (1.0 - 0.88 * pup)).astype(np.float32)


def _mouth(X, Y, W, H, pose, mood, gx, gy, talk=0.0, shake=0.0):
    """Boca de LEDs: linha/arco conforme o humor; círculo quando surpreso."""
    cx = W / 2.0 + gx * 0.09 * H + shake * H
    cy = 0.775 * H + gy * 0.04 * H
    half, thick = 0.13 * H, max(0.9, 0.028 * H)
    smile = float(np.clip(0.95 * pose[ARCH] + 0.7 * pose[CHEEK], 0.0, 1.0))
    if mood in ("sad", "angry"):
        smile = -0.45 if mood == "sad" else -0.25
    if mood == "sleeping":
        half *= 0.45
    lx = X - cx
    if talk > 0.04:                                         # fala: boca aberta, abre e fecha
        a, b = half * (0.80 + 0.25 * talk), max(0.8, 0.09 * H * talk)
        d = (np.hypot(lx / a, (Y - cy) / b) - 1.0) * min(a, b)
        return np.clip(0.5 - d, 0.0, 1.0).astype(np.float32)
    if mood in ("surprised", "wide"):
        r = 0.045 * H
        return np.clip(0.5 - (np.abs(np.hypot(lx, Y - cy) - r) - thick / 2), 0.0, 1.0).astype(np.float32)
    inside = np.clip(half - np.abs(lx) + 0.5, 0.0, 1.0)
    sag = smile * 0.07 * H                                  # >0 sorriso (centro mais baixo), <0 triste
    ycurve = cy + sag * (0.5 - np.clip((lx / half) ** 2, 0.0, 1.0))
    stroke = np.clip(0.5 - (np.abs(Y - ycurve) - thick / 2), 0.0, 1.0)
    return (stroke * inside).astype(np.float32)


def render_field(W: int, H: int, pose: np.ndarray, blink: Tuple[float, float],
                 gaze: Tuple[float, float], motion_scale: float = 1.0,
                 zzz: List[dict] | None = None, style: dict | None = None,
                 mood: str = "neutral", dyn: dict | None = None) -> Tuple[np.ndarray, np.ndarray]:
    """Devolve (alpha, glint), ambos float32 (H, W) em [0,1]."""
    X, Y = _grid(W, H)
    gx = float(np.clip(gaze[0], -1.0, 1.0)) * motion_scale
    gy = float(np.clip(gaze[1], -1.0, 1.0)) * motion_scale
    alpha = np.zeros((H, W), dtype=np.float32)
    glint = np.zeros((H, W), dtype=np.float32)

    style = style or {}
    dyn = dyn or {}
    talk, tremble, tears = float(np.clip(dyn.get("talk", 0.0), 0.0, 1.0)), float(dyn.get("tremble", 0.0)), dyn.get("tears") or ()
    clock = float(dyn.get("t", 0.0))
    pupil_in = float(np.clip(dyn.get("pupil", 0.42), 0.15, 0.85))
    conv = float(np.clip(dyn.get("near", 0.0), 0.0, 1.0))
    plead = float(np.clip(dyn.get("plead", 0.0), 0.0, 1.0))
    vx, vy, pulse = float(dyn.get("vx", 0.0)), float(dyn.get("vy", 0.0)), float(np.clip(dyn.get("pulse", 0.0), 0.0, 1.0))
    if plead > 0.02:                                       # Súplica: olhos grandes, redondos, com contorno fino
        style = dict(style, round_eyes=True, outline=plead > 0.3)
        pose = pose.copy()
        pose[TILT] += -0.30 * plead
    zoom, eye_cy = (0.86, 0.375) if style.get("mouth") else (1.0, EYE_CY)   # com boca: olhos menores e mais altos
    hw, hh = EYE_HW * H * zoom * (1 + 0.12 * plead), EYE_HH * H * zoom * (1 + 0.12 * plead)
    if style.get("round_eyes"):
        hw = hh = (EYE_HW * EYE_HH) ** 0.5 * H * 1.05 * zoom * (1 + 0.12 * plead)      # círculo
        pose = pose.copy()
        pose[RADIUS] = 1.0
    geom = []
    lid_extra = 0.22 * max(gy, 0.0)                 # olhar para baixo abaixa a pálpebra
    for side, b in ((-1, blink[0]), (1, blink[1])):
        persp = 1.0 + 0.10 * gx * side              # olho mais próximo do alvo fica um pouco maior
        cx = W / 2.0 + side * EYE_DX * H * zoom + gx * GAZE_X * H + tremble * H
        cy = eye_cy * H + gy * GAZE_Y * H
        cov, open_, w, h, d = _eye(X, Y, cx, cy, hw * persp, hh, pose, b, side, lid_extra)
        arch_w = float(np.clip(pose[ARCH], 0.0, 1.0))
        pupil = pupil_in + (0.78 - pupil_in) * plead
        eye_field = _iris(X, Y, cx, cy + pose[DY] * hh, w, h, side, gx, gy, pupil, conv, 1.0 + 0.12 * plead, open_)
        alpha = np.maximum(alpha, (cov * (arch_w + (1.0 - arch_w) * eye_field)).astype(np.float32))
        geom.append((side, cx, cy, w, h))
        if style.get("outline") and open_ > 0.25:
            gap, thick = 0.026 * H, 0.012 * H
            ring = np.clip(0.5 - (np.abs(d - (gap + thick / 2)) - thick / 2), 0.0, 1.0)
            ring *= (1.0 - float(np.clip(pose[ARCH], 0.0, 1.0))) * float(np.clip((open_ - 0.25) / 0.35, 0.0, 1.0))
            alpha = np.maximum(alpha, (ring * 0.50).astype(np.float32))

        # catchlight: camada própria. Paralaxe oposta ao olhar, inércia (atraso) com a velocidade,
        # alongamento na direção do movimento e brilho extra quando o foco muda de pessoa.
        speed = min(float(np.hypot(vx, vy)), 3.0)
        gs = max(1.5, 0.033 * H) * (1.0 + 0.55 * pulse + 0.10 * speed + 0.7 * plead)
        gcx = cx - 0.30 * w - gx * 0.10 * w - vx * 0.14 * w + 0.07 * w * np.sin(1.3 * clock)
        gcy = cy + pose[DY] * hh - 0.40 * h - gy * 0.08 * h - vy * 0.12 * h + 0.05 * h * np.sin(1.7 * clock)
        rr = gs * (2.0 + 0.6 * speed) + 3.0                 # janela local: o reflexo é pequeno
        ya, yb = max(0, int(gcy - rr)), min(H, int(gcy + rr) + 1)
        xa, xb = max(0, int(gcx - rr)), min(W, int(gcx + rr) + 1)
        if pulse > 0.08 or plead > 0.3:                      # o brilho secundário cai dentro do olho
            ya, yb = max(0, int(cy - h - 2)), min(H, int(cy + h + 3))
            xa, xb = max(0, int(cx - w - 2)), min(W, int(cx + w + 3))
        g = np.zeros((H, W), dtype=np.float32)
        if yb > ya and xb > xa:
            Xs, Ys = X[ya:yb, xa:xb], Y[ya:yb, xa:xb]
            dx, dy = Xs - gcx, Ys - gcy
            if speed > 0.15:
                norm = max(float(np.hypot(vx, vy)), 1e-6)
                ux, uy = vx / norm, vy / norm
                along, across = dx * ux + dy * uy, -dx * uy + dy * ux
                stretch = 1.0 + 0.45 * speed
                dist = np.hypot(along / stretch, across * np.sqrt(stretch))
            else:
                dist = np.hypot(dx, dy)
            local = np.clip(.5 - (dist - gs), 0, 1)
            if pulse > 0.08 or plead > 0.3:                  # segundo brilho pequeno (troca de foco / súplica)
                sx, sy = cx + 0.30 * w, cy + 0.32 * h
                local = np.maximum(local, np.clip(.5 - (np.hypot(Xs - sx, Ys - sy) - gs * 0.55), 0, 1) * min(1.0, max(pulse, 0.8 * plead) * 1.6))
            g[ya:yb, xa:xb] = local
        fade = float(np.clip((open_ - 0.35) / 0.40, 0.0, 1.0)) * float(pose[GLINT])
        glint = np.maximum(glint, (g * cov * fade).astype(np.float32))

    for side_t, age, life in tears:                         # lágrimas escorrendo do canto externo
        for side, ecx, ecy, ew, eh in geom:
            if side != side_t:
                continue
            tx = ecx + side * 0.46 * ew
            ty = ecy + 0.78 * eh + 0.30 * H * age * (0.6 + 0.4 * age / max(life, 0.1))
            rx = max(1.0, 0.019 * H)
            fade = max(0.0, 1.0 - age / life) ** 0.6
            d = (np.hypot((X - tx) / rx, (Y - ty) / (rx * 1.9)) - 1.0) * rx
            alpha = np.maximum(alpha, (np.clip(0.5 - d, 0.0, 1.0) * 0.85 * fade).astype(np.float32))
    if style.get("mouth"):
        alpha = np.maximum(alpha, _mouth(X, Y, W, H, pose, mood, gx, gy, talk, tremble))

    if zzz:
        for z in zzz:
            age, life = z["age"], z["life"]
            glyph = _Z3 if age < 1.4 else _Z5
            x0 = int(round(W / 2 + (EYE_DX + EYE_HW + 0.04) * H + 2.2 * age))
            y0 = int(round(EYE_CY * H - 0.20 * H - 2.6 * age))
            a = float(np.sin(np.pi * min(age / life, 1.0))) ** 0.7
            gh, gw = glyph.shape
            ys, xs = max(y0, 0), max(x0, 0)
            ye, xe = min(y0 + gh, H), min(x0 + gw, W)
            if ye > ys and xe > xs:
                sub = glyph[ys - y0:ye - y0, xs - x0:xe - x0] * a
                alpha[ys:ye, xs:xe] = np.maximum(alpha[ys:ye, xs:xe], sub)
    return alpha, glint
