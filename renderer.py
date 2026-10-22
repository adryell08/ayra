"""Desenho com pygame: painel de LEDs + HUD no estilo das HIG.

Painel: a matriz (W x H células) é ampliada por um número inteiro de pixels por célula e
centralizada na janela (a proporção nunca é distorcida: a arte é *escalada*, não esticada).
Cada célula vira um LED redondo; um halo suave (bloom) imita a luz dos LEDs.

HUD (HIG): conteúdo importante no topo; chips arredondados com contraste >= 4,5:1;
indicador de câmera sempre visível; dicas de atalho que somem sozinhas.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from pathlib import Path
import os
from typing import Dict, List, Optional, Tuple

import numpy as np
import pygame

import theme
from expressions import LABELS
from menu import OptionsMenu
from robot import RobotFrame
from theme import Scheme, emotion_tint, mix

SHORTCUTS = [
    ("Espaço", "Nuria: perguntar por voz; de novo envia; durante a fala interrompe"),
    ("Enter", "Nuria: digitar a pergunta (Enter envia, Esc cancela)"),
    ("V", "Nuria: pausar/retomar a visão (só conversa, sem enviar imagem)"),
    ("Esc", "Durante uma pergunta: cancelar"),
    ("Esc / Q", "Fechar menu/ajuda; Q encerra o aplicativo"),
    ("F10", "Menu: setas ou Tab navegam; Enter confirma; Esc fecha"),
    ("F6", "Dock: setas ou Tab navegam; Enter ativa; Esc sai"),
    ("P", "Pausar / retomar a câmera"),
    ("1 – 8", "Expressões: neutro, feliz, surpreso, triste, bravo, arregalado, sonolento, dormindo"),
    ("0", "Voltar ao modo automático"),
    ("B  /  K", "Piscar  /  piscadela"),
    ("S", "Alternar quem “está falando” (gancho da IA)"),
    ("Tab", "HUD: completo, mínimo, oculto"),
    ("C", "Tema: escuro, claro, alto contraste"),
    ("M", "Reduzir movimento"),
    ("G", "Brilho dos LEDs"),
    ("F11", "Tela cheia"),
    ("D", "Diagnóstico: FPS, quadro até flip, IDs, scores e caixas numéricas"),
    ("Mouse", "Sem câmera: o mouse é um rosto; roda = distância; botão direito = sai/entra"),
    ("+  /  –", "Sem câmera: adicionar / remover uma pessoa"),
    ("T (segurar)", "Simulado: língua de fora; reação padrão bravo"),
    ("W / J", "Simulado: acenar / mão próxima (reflexo e recuo)"),
    ("O", "Simulado: cachorro (feliz)"),
    ("U (segurar)", "Simulado: sorriso (feliz)"),
]


@dataclass
class HudInfo:
    camera_status: str = "ok"          # ok | paused | sim | error | starting
    camera_note: str = ""
    fps: float = 0.0
    hud_mode: str = "minimal"          # full | minimal | off
    show_help: bool = False
    hint_age: float = 0.0
    reduce_motion: bool = False
    toast: str = ""
    toast_age: float = 99.0
    detector: str = ""
    fullscreen: bool = False
    simulated: bool = False
    help_scroll: int = 0
    debug: bool = False
    detect_fps: Optional[float] = None
    latency_ms: Optional[float] = None
    latency_p95_ms: Optional[float] = None
    detection_ms: Optional[float] = None
    sample_age_ms: Optional[float] = None
    latency_samples: int = 0
    camera_index: Optional[int] = None
    camera_name: str = ""
    camera_backend: str = ""
    capture_resolution: Optional[Tuple[int, int]] = None
    capture_fps: Optional[float] = None
    mean_brightness: Optional[float] = None
    camera_choices: List[Tuple[int, str]] = field(default_factory=list)
    perception_note: str = ""
    perception_modules: dict = field(default_factory=dict)
    sensitivity: float = 1.0
    visual_scores: dict = field(default_factory=dict)
    face_proximity: float = 0.
    build_label: str = ""
    debug_scroll: int = 0
    last_trigger: Optional[dict] = None
    trigger_age: Optional[float] = None
    features: dict = field(default_factory=dict)
    active_actions: tuple = ()
    arduino: dict = field(default_factory=dict)      # estado da aba Arduino (painel LED físico)
    api: dict = field(default_factory=dict)          # estado da aba API (chave mascarada, teste, modelo, voz)
    remote: dict = field(default_factory=dict)       # sala do operador (controle remoto, etapa 2)


class Renderer:
    def __init__(self, screen: pygame.Surface, grid: Tuple[int, int], scheme: Scheme, glow: bool = True):
        self.W, self.H = grid
        self.scheme = scheme
        self.glow_on = glow
        self._fonts: Dict[Tuple[str, int], pygame.font.Font] = {}
        self._txt: Dict[tuple, pygame.Surface] = {}
        self._prev: Optional[np.ndarray] = None
        self._tint: Tuple[float, float, float] = tuple(float(v) for v in scheme.led_on)
        self.menu = OptionsMenu()
        self._help_max_scroll = 0
        self._debug_max_scroll = 0
        self.help_close = pygame.Rect(0, 0, 0, 0)
        self.debug_close = pygame.Rect(0, 0, 0, 0)
        self.debug_rect = pygame.Rect(0, 0, 0, 0)
        self._close_pressed = None
        self._visual_time = 0.0
        self.reserve_right = 0          # coluna da Nuria à direita (px); o rosto centraliza no restante
        self.resize(screen)

    # ---- geometria -------------------------------------------------------
    def resize(self, screen: pygame.Surface) -> None:
        self.screen = screen
        sw, sh = screen.get_size()
        self.s = min(theme.ui_scale(sh), max(1.0, sw / 800.0))
        self.menu.layout(screen, self.s)
        m = max(int(0.015 * min(sw, sh)), 6)
        fw = max(self.W * 2 + 2 * m, sw - self.reserve_right)
        self.cell = max(2, min((fw - 2 * m) // self.W, (sh - 2 * m) // self.H))
        pw, ph = self.W * self.cell, self.H * self.cell
        self.panel = pygame.Rect((fw - pw) // 2, (sh - ph) // 2, pw, ph)
        self._fonts.clear()
        self._txt.clear()
        self._build_static()

    def set_scheme(self, scheme: Scheme) -> None:
        self.scheme = scheme
        self._txt.clear()
        self._build_static()

    def _build_static(self) -> None:
        cs, W, H = self.cell, self.W, self.H
        ss = 4
        yy, xx = np.mgrid[0:cs * ss, 0:cs * ss].astype(np.float32)
        r = cs * ss * 0.40
        dot = (np.hypot(xx - cs * ss / 2 + 0.5, yy - cs * ss / 2 + 0.5) < r).astype(np.float32)
        dot = dot.reshape(cs, ss, cs, ss).mean(axis=(1, 3))          # antialias por média
        tile = np.tile(dot, (H, W))                                   # (ph, pw)
        pw, ph = self.panel.size
        rgba = np.empty((ph, pw, 4), dtype=np.uint8)
        # Pontos nítidos: piso discreto conserva o corpo sem apagar os vãos.
        rgba[..., :3] = 255
        rgba[..., 3] = ((.10 + .90 * tile) * 255).astype(np.uint8)
        self.mask = pygame.image.frombuffer(rgba.tobytes(), (pw, ph), "RGBA").convert_alpha()
        panel = np.array(self.scheme.panel, dtype=np.float32)
        off = np.array(self.scheme.led_off, dtype=np.float32)
        vy, vx = np.mgrid[0:ph, 0:pw]
        vignette = np.exp(-(((vx / pw - .5) / .43) ** 4 + ((vy / ph - .48) / .38) ** 4))
        img = panel + (off - panel) * tile[..., None] * vignette[..., None] * (.18 if self.scheme.key == "light" else .45)
        self.static = pygame.image.frombuffer(img.astype(np.uint8).tobytes(), (pw, ph), "RGB").convert()
        # Fundo, borda e LEDs apagados são estáticos até resize/troca de tema.
        self.background = pygame.Surface(self.screen.get_size()).convert()
        self.background.fill(self.scheme.bg)
        self.background.blit(self.static, self.panel.topleft)
        self.big = pygame.Surface((pw, ph), pygame.SRCALPHA)
        self.small = pygame.Surface((self.W, self.H), pygame.SRCALPHA)
        self.glow_big = pygame.Surface((pw, ph))
        self.glow_small = pygame.Surface((self.W, self.H))
        self._prev = None

    # ---- texto -------------------------------------------------------------
    def _font(self, role: str) -> pygame.font.Font:
        px = max(9, int(round(theme.TEXT_ROLES[role][0] * self.s)))
        key = (role, px)
        if key not in self._fonts:
            bold = theme.TEXT_ROLES[role][1]
            # SysFont pode escolher HelveticaNeueLight/segoeuil por colisão de família.
            regular = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / ("segoeuib.ttf" if bold else "segoeui.ttf")
            self._fonts[key] = (pygame.font.Font(str(regular), px) if regular.exists()
                               else pygame.font.SysFont(theme.FONT_STACK, px, bold=bold))
        return self._fonts[key]

    def _text(self, role: str, text: str, color) -> pygame.Surface:
        key = (role, text, tuple(color))
        if key not in self._txt:
            if len(self._txt) > 400:
                self._txt.clear()
            self._txt[key] = self._font(role).render(text, True, color)
        return self._txt[key]

    def _fit(self, role: str, text: str, width: int) -> str:
        if self._font(role).size(text)[0] <= width:
            return text
        while text and self._font(role).size(text + "…")[0] > width:
            text = text[:-1]
        return text + "…" if text else ""

    def _wrap(self, role: str, text: str, width: int) -> List[str]:
        """Quebra inclusive nomes/erros sem espaços; conserva o conteúdo na rolagem."""
        lines, current = [], ""
        for word in text.split():
            if current and self._font(role).size(current + " " + word)[0] > width:
                lines.append(current)
                current = ""
            while word and self._font(role).size(word)[0] > width:
                n = 1
                while n < len(word) and self._font(role).size(word[:n + 1])[0] <= width:
                    n += 1
                if current:
                    lines.append(current)
                    current = ""
                lines.append(word[:n])
                word = word[n:]
            current = (current + " " + word).strip()
        return lines + ([current] if current else [])

    # ---- LEDs --------------------------------------------------------------
    def draw_panel(self, rf: RobotFrame, dt: float, reduce_motion: bool) -> None:
        sc = self.scheme
        self._visual_time += max(0, dt)
        self.screen.blit(self.background, (0, 0))

        # cor-alvo da expressão (suavizada) — reforço, nunca o único sinal
        target = mix(sc.led_on, emotion_tint(sc, rf.shown), 0.92)
        a = 1.0 if reduce_motion else min(1.0, dt * 6.0)
        self._tint = tuple(c + (t - c) * a for c, t in zip(self._tint, target))

        alpha = rf.alpha
        if not reduce_motion and self._prev is not None:      # leve persistência, como LED real
            alpha = np.maximum(alpha, self._prev * 0.25)
        self._prev = alpha
        glint = rf.glint
        ys, xs = np.nonzero(alpha > 0.004)
        if len(ys) == 0:
            return
        c0, c1 = int(xs.min()), int(xs.max()) + 1
        r0, r1 = int(ys.min()), int(ys.max()) + 1
        tint = np.array(self._tint, dtype=np.float32)
        hi = np.array(sc.highlight, dtype=np.float32)
        yy, xx = np.mgrid[0:self.H, 0:self.W]
        # Luz interna, contínua e igual nos temas: centro vivo, borda serena.
        from face import EYE_DX, EYE_CY, EYE_HW, EYE_HH, GAZE_X, GAZE_Y
        gx, gy = rf.gaze
        distance = np.minimum(*[
            ((xx + .5 - (self.W / 2 + side * EYE_DX * self.H + gx * GAZE_X * self.H)) / (EYE_HW * self.H)) ** 2
            + ((yy + .5 - (EYE_CY + gy * GAZE_Y) * self.H) / (EYE_HH * self.H)) ** 2
            for side in (-1, 1)])
        gradient = np.exp(-distance * 1.5)
        inner = .02 + .05 * gradient
        if not reduce_motion:
            inner *= 1 + .025 * math.sin(self._visual_time * 1.7)
        light = np.clip(inner + glint, 0, 1)[..., None]
        body = tint[None, None, :] * (.78 + .22 * gradient[..., None])
        if sc.key == "light":
            ring = np.maximum.reduce([np.roll(glint, n, axis=axis) for axis in (0, 1) for n in (-1, 1)])
            body *= (1 - .22 * np.clip(ring - glint, 0, 1))[..., None]
        col = body + (hi - body) * light

        # Halo luminoso no OLED; sombra azul muito leve no claro.
        if self.glow_on and sc.glow > 0:
            g0, g1 = max(c0 - 3, 0), min(c1 + 3, self.W)
            q0, q1 = max(r0 - 3, 0), min(r1 + 3, self.H)
            lum = alpha[q0:q1, g0:g1, None] * (np.array([75, 75, 75]) if sc.key == "light" else col[q0:q1, g0:g1])
            k = np.array([0, 1, 2, 1, 0], dtype=np.float32) / 4.0
            pad = np.pad(lum, ((2, 2), (2, 2), (0, 0)), mode="edge")
            tmp = sum(k[i] * pad[:, i:i + lum.shape[1]] for i in range(5))
            blur = sum(k[i] * tmp[i:i + lum.shape[0]] for i in range(5))
            # Não lavar os pontos por baixo: halo só fora da silhueta.
            blur *= (1 - alpha[q0:q1, g0:g1, None])
            blur = np.clip(blur * sc.glow * (.08 if sc.key == "light" else .50), 0, 255).astype(np.uint8)
            gw, gh = g1 - g0, q1 - q0
            gs = self.glow_small.subsurface((g0, q0, gw, gh))
            pygame.surfarray.blit_array(gs, np.ascontiguousarray(blur.transpose(1, 0, 2)))
            gb = self.glow_big.subsurface((g0 * self.cell, q0 * self.cell, gw * self.cell, gh * self.cell))
            pygame.transform.smoothscale(gs, gb.get_size(), gb)
            self.screen.blit(gb, (self.panel.x + g0 * self.cell, self.panel.y + q0 * self.cell),
                             special_flags=pygame.BLEND_RGB_SUB if sc.key == "light" else pygame.BLEND_RGB_ADD)

        # LEDs acesos
        cw, rh = c1 - c0, r1 - r0
        sub = self.small.subsurface((c0, r0, cw, rh))
        rgb = np.clip(col[r0:r1, c0:c1], 0, 255).astype(np.uint8)
        a8 = (np.clip(alpha[r0:r1, c0:c1], 0, 1) * 255).astype(np.uint8)
        px = pygame.surfarray.pixels3d(sub)
        px[:] = rgb.transpose(1, 0, 2)
        del px
        pa = pygame.surfarray.pixels_alpha(sub)
        pa[:] = a8.T
        del pa
        cs = self.cell
        bsub = self.big.subsurface((c0 * cs, r0 * cs, cw * cs, rh * cs))
        # AA já vem do campo analítico e do círculo supersampled; sem blur no upscale.
        pygame.transform.scale(sub, bsub.get_size(), bsub)
        bsub.blit(self.mask.subsurface((c0 * cs, r0 * cs, cw * cs, rh * cs)), (0, 0),
                  special_flags=pygame.BLEND_RGBA_MULT)
        self.screen.blit(bsub, (self.panel.x + c0 * cs, self.panel.y + r0 * cs))

    # ---- HUD ---------------------------------------------------------------
    def _glass(self, rect: pygame.Rect, radius: int, blur: bool = False) -> None:
        """Vidro somente sobre a arte do robô; alto contraste usa fundo opaco."""
        sc = self.scheme
        card = pygame.Surface(rect.size, pygame.SRCALPHA)
        if blur and sc.key != "contrast" and self.screen.get_rect().contains(rect):
            backdrop = self.screen.subsurface(rect)
            small = pygame.transform.smoothscale(backdrop, (max(1, rect.w // 12), max(1, rect.h // 12)))
            card.blit(pygame.transform.smoothscale(small, rect.size), (0, 0))
        tint = pygame.Surface(rect.size, pygame.SRCALPHA)
        # Tinta suficiente para contraste, preservando a luz desfocada ao fundo.
        alpha = 255 if sc.key == "contrast" else (233 if sc.key == "light" else 238)
        pygame.draw.rect(tint, (*sc.chip, alpha), tint.get_rect(),
                         border_radius=radius)
        card.blit(tint, (0, 0))
        mask = pygame.Surface(rect.size, pygame.SRCALPHA)
        pygame.draw.rect(mask, (255, 255, 255, 255), mask.get_rect(), border_radius=radius)
        card.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
        pygame.draw.rect(card, mix(sc.chip, sc.text, .15 if sc.key != "contrast" else 1), card.get_rect(), width=1, border_radius=radius)
        self.screen.blit(card, rect)

    def _icon(self, name, rect, color):
        """Ícones vetoriais consistentes, sem depender de glifos/emoji do sistema."""
        x, y = rect.center
        s, w = self.s, max(2, round(1.8 * self.s))
        k = lambda n: round(n * s)
        if name == "camera":
            body = pygame.Rect(x-k(12), y-k(8), k(24), k(18))
            pygame.draw.rect(self.screen, color, body, w, border_radius=k(5))
            pygame.draw.circle(self.screen, color, (x, y+k(1)), k(4), w)
            pygame.draw.line(self.screen, color, (x-k(5), y-k(11)), (x+k(5), y-k(11)), w)
        elif name == "theme":
            pygame.draw.circle(self.screen, color, (x, y), k(10), w)
            pygame.draw.arc(self.screen, color, (x-k(6), y-k(7), k(12), k(14)), -math.pi/2, math.pi/2, k(6))
        elif name == "motion":
            for i, length in enumerate((11, 7, 11)):
                pygame.draw.line(self.screen, color, (x-k(length), y+k((i-1)*6)), (x+k(length), y+k((i-1)*6)), w)
        else:
            for i, dx in enumerate((-5, 5, -2)):
                yy = y+k((i-1)*8)
                pygame.draw.line(self.screen, color, (x-k(12), yy), (x+k(12), yy), w)
                pygame.draw.circle(self.screen, self.scheme.chip, (x+k(dx), yy), k(4))
                pygame.draw.circle(self.screen, color, (x+k(dx), yy), k(4), w)

    def handle_event(self, event, hud: HudInfo):
        self.menu.block_dock = (hud.show_help or hud.debug) and not self.menu.open
        consumed, action = self.menu.handle_event(event, self.menu.items(hud, self.scheme))
        if consumed:
            return consumed, action
        # Fechar exige pressionar e soltar no mesmo alvo; nenhum clique vaza ao rosto simulado.
        if event.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP) and event.button == 1:
            target = ("help" if hud.show_help and self.help_close.collidepoint(event.pos)
                      else "debug" if hud.debug and not hud.show_help and self.debug_close.collidepoint(event.pos)
                      else None)
            if event.type == pygame.MOUSEBUTTONDOWN:
                self._close_pressed = target
            else:
                pressed, self._close_pressed = self._close_pressed, None
                if target and target == pressed:
                    if target == "help":
                        hud.show_help = False
                    else:
                        hud.debug = False
                    return True, None
            if target:
                return True, None
        if hud.show_help:
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    hud.show_help = False
                    return True, None
                if event.key in (pygame.K_UP, pygame.K_DOWN, pygame.K_PAGEUP, pygame.K_PAGEDOWN):
                    amount = int((120 if event.key in (pygame.K_PAGEUP, pygame.K_PAGEDOWN) else 30) * self.s)
                    if event.key in (pygame.K_UP, pygame.K_PAGEUP):
                        amount = -amount
                    hud.help_scroll = max(0, min(self._help_max_scroll, hud.help_scroll + amount))
                    return True, None
            if event.type == pygame.MOUSEWHEEL:
                hud.help_scroll = max(0, min(self._help_max_scroll, hud.help_scroll - int(event.y * 30 * self.s)))
                return True, None
            if event.type in (pygame.MOUSEMOTION, pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
                return True, None
        if hud.debug:
            amount = 0
            if event.type == pygame.MOUSEWHEEL:
                amount = -event.y * int(48 * self.s)
            elif event.type == pygame.KEYDOWN and event.key in (pygame.K_PAGEUP, pygame.K_PAGEDOWN):
                amount = int(180 * self.s) * (-1 if event.key == pygame.K_PAGEUP else 1)
            if amount:
                hud.debug_scroll = max(0, min(self._debug_max_scroll, hud.debug_scroll + amount))
                return True, None
            if event.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP, pygame.MOUSEMOTION):
                if self.debug_rect.collidepoint(event.pos):
                    return True, None
        return False, None

    def _chip(self, lines: List[str], pos: Tuple[int, int], anchor: str = "tl",
              marker: Optional[str] = None, marker_color=None, role: str = "body",
              max_width: Optional[int] = None) -> pygame.Rect:
        sc, s = self.scheme, self.s
        pad = int(theme.PADDING * s)
        if max_width is not None:
            text_width = max(1, max_width - 2 * pad - (int(18 * s) if marker else 0))
            lines = [self._fit(role, ln, text_width) for ln in lines]
        surfs = [self._text(role, ln, sc.text if i == 0 else sc.text_dim) for i, ln in enumerate(lines)]
        tw = max(t.get_width() for t in surfs)
        th = sum(t.get_height() for t in surfs)
        mk = int(10 * s) if marker else 0
        gap = int(8 * s) if marker else 0
        w = pad + mk + gap + tw + pad
        h = max(int(theme.CONTROL_MIN * s), th + pad)
        rect = pygame.Rect(0, 0, w, h)
        x, y = pos
        if anchor in ("tr", "br"):
            rect.right = x
        elif anchor in ("bc", "tc"):
            rect.centerx = x
        else:
            rect.left = x
        if anchor in ("bl", "br", "bc"):
            rect.bottom = y
        else:
            rect.top = y
        surf = pygame.Surface(rect.size, pygame.SRCALPHA)
        radius = h // 2 if len(lines) == 1 else int(20 * s)
        pygame.draw.rect(surf, (*sc.chip, sc.chip_alpha), surf.get_rect(), border_radius=radius)
        if sc.key == "contrast":
            pygame.draw.rect(surf, sc.separator, surf.get_rect(), width=max(1, int(s)), border_radius=radius)
        cy = h // 2
        x0 = pad
        if marker:
            col = marker_color or sc.text
            if marker == "circle":
                pygame.draw.circle(surf, col, (x0 + mk // 2, cy), mk // 2)
            elif marker == "square":
                pygame.draw.rect(surf, col, (x0, cy - mk // 2, mk, mk), border_radius=max(2, mk // 5))
            elif marker == "diamond":
                c = (x0 + mk // 2, cy)
                r = mk // 2 + 1
                pygame.draw.polygon(surf, col, [(c[0], c[1] - r), (c[0] + r, c[1]), (c[0], c[1] + r), (c[0] - r, c[1])])
            elif marker == "ring":
                pygame.draw.circle(surf, col, (x0 + mk // 2, cy), mk // 2, width=max(2, int(1.6 * s)))
            x0 += mk + gap
        ty = (h - th) // 2
        for t in surfs:
            surf.blit(t, (x0, ty))
            ty += t.get_height()
        self.screen.blit(surf, rect.topleft)
        return rect

    def draw_hud(self, rf: RobotFrame, hud: HudInfo) -> None:
        sc, s = self.scheme, self.s
        pad = int(theme.PADDING * s)
        p = self.panel
        left, top, right, bottom = pad, pad, self.screen.get_width() - pad, self.screen.get_height() - pad
        pal = sc.palette
        self.menu.layout(self.screen, s, len(self.menu.items(hud, sc)))

        # indicador de câmera: SEMPRE visível (transparência sobre o uso da câmera)
        st = hud.camera_status
        if st == "ok":
            txt, mk, col = "Câmera ativa · local", "circle", pal["green"]
        elif st == "paused":
            txt, mk, col = "Câmera pausada", "square", pal["orange"]
        elif st == "starting":
            txt, mk, col = "Iniciando câmera…", "ring", pal["yellow"]
        elif st == "waiting":
            txt, mk, col = "Aguardando imagem · local", "ring", pal["yellow"]
        elif st == "sim":
            txt, mk, col = "Modo simulado · sem câmera", "diamond", pal["indigo"]
        else:
            txt = "Câmera indisponível" if not hud.simulated else "Sem câmera · simulação"
            mk, col = "diamond", pal["red"]
        camera_lines = [txt]
        max_width = min(right - left, int(460 * s))
        if hud.camera_name and not hud.simulated:
            camera_lines.append(f"#{hud.camera_index} · {hud.camera_name}")
        if st in ("error", "waiting") and hud.camera_note:
            camera_lines.extend(self._wrap("body", hud.camera_note, max_width - 2 * pad - int(18 * s))[:3])
        r_cam = self._chip(camera_lines, (left, top), "tl", mk, col, max_width=max_width)
        if hud.hud_mode == "off":
            if hud.debug:
                self._debug(rf, hud, (left, r_cam.bottom + pad), (right, bottom))
            self._toast_and_help(rf, hud, pad)
            self.menu.draw(self, hud)
            return
        att = rf.attention

        # estado da atenção (canto inferior esquerdo; informativo, não é controle)
        if att.idle_level == 2:
            msg = "Dormindo · aproxime-se para acordar"
        elif att.idle_level == 1:
            msg = "Sonolento…"
        elif att.mode == "tracking":
            dist = "perto" if att.proximity > 0.40 else "médio" if att.proximity > 0.22 else "longe"
            msg = f"Acompanhando o rosto #{att.focus_id} · {dist}"
        elif att.mode == "glance":
            msg = f"Olhando de relance o rosto #{att.focus_id}"
        elif att.mode == "override":
            msg = "Olhar dirigido"
        else:
            msg = "Procurando alguém…"
        if att.speaker_id is not None:
            msg += f" · falando: #{att.speaker_id}"
        if not hud.debug and not hud.show_help and not self.menu.open:
            if hud.hud_mode == "full":
                msg += " · " + LABELS.get(rf.shown, rf.shown)
                if hud.reduce_motion:
                    msg += " · movimento reduzido"
                self._chip([msg], (left, bottom), "bl", max_width=right - left)
            # O dock já oferece acesso a Ajustes; sem uma segunda pílula no rodapé.
        if hud.debug:
            self._debug(rf, hud, (left, r_cam.bottom + pad), (right, bottom))
        self._toast_and_help(rf, hud, pad)
        self.menu.draw(self, hud)

    def _hint(self, a: float, pad: int) -> None:
        sc = self.scheme
        txt = "F10  Ajustes     ·     F1  Ajuda"
        t = self._text("footnote", txt, sc.text_dim)
        w, h = t.get_width() + 2 * pad, t.get_height() + pad
        surf = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.rect(surf, (*sc.chip, int(sc.chip_alpha * a)), surf.get_rect(), border_radius=h // 2)
        tt = t.copy()
        tt.set_alpha(int(255 * a))
        surf.blit(tt, (pad, (h - t.get_height()) // 2))
        self.screen.blit(surf, (self.screen.get_width() // 2 - w // 2, self.screen.get_height() - pad - h))

    def _debug(self, rf: RobotFrame, hud: HudInfo, pos, anchor) -> None:
        sc, s = self.scheme, self.s
        pad = int(theme.PADDING * s)
        value = lambda n: "—" if n is None else f"{n:.1f}"
        lines = [
            "Diagnóstico local · D para ocultar",
            (f"Último gatilho: {hud.last_trigger['kind']} · score/medida {hud.last_trigger['score']:.2f} · "
             f"t={hud.last_trigger['time']:.2f}s · há {hud.trigger_age or 0:.1f}s") if hud.last_trigger else "Último gatilho: nenhum",
            f"{hud.build_label} · sens {hud.sensitivity:.2f}",
            f"Língua {hud.visual_scores.get('tongue_out', 0):.2f} · sorriso {hud.visual_scores.get('smile', 0):.2f} · rosto h={hud.face_proximity:.2f}",
            f"Câmera #{hud.camera_index if hud.camera_index is not None else 'auto'} · {hud.camera_name[:35] or '—'}",
            f"Backend: {hud.camera_backend or '—'} · " + ("×".join(map(str, hud.capture_resolution)) if hud.capture_resolution else "—"),
            f"Captura: {value(hud.capture_fps)} FPS · brilho: {value(hud.mean_brightness)}/255",
            f"Render: {hud.fps:.1f} FPS",
            f"Detecção: {value(hud.detect_fps)} FPS · {'sem detector' if hud.simulated else hud.detector or '—'}",
            hud.perception_note or "Percepção auxiliar: aguardando",
            f"Quadro recebido até flip: {value(hud.latency_ms)} ms",
            f"p95: {value(hud.latency_p95_ms)} ms · {hud.latency_samples} amostras (máx. 120)",
            f"Detector: {value(hud.detection_ms)} ms · idade: {value(hud.sample_age_ms)} ms",
            "Simulação: sem medida da câmera" if hud.simulated else "Exclui sensor/buffer e varredura do monitor",
            f"Alvo #{rf.attention.target_id or '—'} · foco #{rf.attention.focus_id or '—'}",
        ]
        lines.extend(f"ID #{tr.id} · score {rf.attention.scores.get(tr.id, 0):.2f}"
                     for tr in rf.tracks if tr.confirmed and tr.visible)
        for key, data in hud.perception_modules.items():
            name = {"hands": "Mãos", "objects": "Objetos", "smile": "Sorriso", "tongue": "Língua"}.get(key, key)
            ms = value(data.get("inference_ms"))
            if not data.get("enabled", True):
                lines.append(f"{name} · desligado")
                continue
            lines.append(f"{name} · modelo {'sim' if data.get('loaded') else 'não'} · {ms} ms · {data.get('label', '—')} {data.get('score', 0):.2f}")
            if data.get("error"):
                lines.append("  " + data["error"])
        for key, label in (("reflex", "Reflexo"), ("auto_blink", "Piscadas automáticas")):
            if key in hud.features:
                lines.append(f"{label} · {'ligado' if hud.features[key] else 'desligado'}")
        role = "subheadline"
        line_h = self._font(role).get_height() + int(7 * s)
        width = min(int(480 * s), self.screen.get_width() - 2 * pad)
        header, footer = int(60 * s), int(36 * s)
        wrapped = [part for line in lines[1:] for part in self._wrap(role, line, width - 3 * pad)]
        content_h = len(wrapped) * line_h
        height = min(content_h + header + footer, max(1, anchor[1] - pos[1]))
        rect = self.debug_rect = pygame.Rect(pos, (width, height))
        self._glass(rect, int(24 * s))
        title = self._text("headline", "Diagnóstico local", sc.text)
        self.screen.blit(title, (rect.x + pad, rect.y + int(20 * s)))
        self.debug_close = pygame.Rect(rect.right - pad - int(44 * s), rect.y + int(8 * s),
                                       int(44 * s), int(44 * s))
        self._close_button(self.debug_close)
        viewport = pygame.Rect(rect.x + pad, rect.y + header, width - 2 * pad,
                               max(1, height - header - footer))
        self._debug_max_scroll = max(0, content_h - viewport.h)
        hud.debug_scroll = max(0, min(self._debug_max_scroll, hud.debug_scroll))
        old_clip = self.screen.get_clip()
        self.screen.set_clip(viewport)
        for i, line in enumerate(wrapped):
            self.screen.blit(self._text(role, line, sc.text_dim),
                             (viewport.x, viewport.y + i * line_h - hud.debug_scroll))
        self.screen.set_clip(old_clip)
        self._scrollbar(viewport, content_h, hud.debug_scroll)
        footer_text = "Roda / PgUp / PgDn · D oculta" if self._debug_max_scroll else "Somente números · D oculta"
        self.screen.blit(self._text("caption", footer_text, sc.text_dim),
                         (rect.x + pad, rect.bottom - footer + int(8 * s)))
        if self.screen.get_width() - rect.right > int(320 * s):
            self._minimap(rf, anchor, debug=True)

    def _close_button(self, rect: pygame.Rect) -> None:
        sc = self.scheme
        pygame.draw.circle(self.screen, mix(sc.chip, sc.text, 0.08), rect.center, int(14 * self.s))
        k = int(4 * self.s)
        x, y = rect.center
        pygame.draw.line(self.screen, sc.text, (x-k, y-k), (x+k, y+k), max(2, int(self.s)))
        pygame.draw.line(self.screen, sc.text, (x-k, y+k), (x+k, y-k), max(2, int(self.s)))

    def _scrollbar(self, viewport: pygame.Rect, content_h: int, scroll: int) -> None:
        if content_h <= viewport.h:
            return
        height = max(int(20 * self.s), int(viewport.h * viewport.h / content_h))
        y = viewport.y + int((viewport.h - height) * scroll / (content_h - viewport.h))
        pygame.draw.rect(self.screen, mix(self.scheme.chip, self.scheme.text, .10),
                         (viewport.right - int(3 * self.s), viewport.y, max(2, int(3 * self.s)), viewport.h),
                         border_radius=max(1, int(2 * self.s)))
        pygame.draw.rect(self.screen, self.scheme.text_dim,
                         (viewport.right - int(3 * self.s), y, max(2, int(3 * self.s)), height),
                         border_radius=max(1, int(2 * self.s)))

    def _minimap(self, rf: RobotFrame, anchor: Tuple[int, int], debug: bool = False) -> None:
        sc, s = self.scheme, self.s
        w = int(max(180 * s, min(260 * s, self.screen.get_width() * 0.23))) if debug else int(self.panel.w * 0.17)
        h = int(w * 0.75)
        pad = int(theme.PADDING * s)
        rect = pygame.Rect(0, 0, w, h)
        rect.bottomright = (anchor[0], anchor[1] - 0)
        card = pygame.Surface(rect.size, pygame.SRCALPHA)
        pygame.draw.rect(card, (*sc.chip, sc.chip_alpha), card.get_rect(), border_radius=int(14 * s))
        pygame.draw.rect(card, sc.separator, card.get_rect(), width=1, border_radius=int(14 * s))
        # eixos de referência
        pygame.draw.line(card, sc.separator, (w // 2, int(8 * s)), (w // 2, h - int(8 * s)))
        pygame.draw.line(card, sc.separator, (int(8 * s), h // 2), (w - int(8 * s), h // 2))
        pal = sc.palette
        acc = pal["cyan"] if sc.key != "light" else pal["blue"]
        att = rf.attention
        inner = pygame.Rect(int(10 * s), int(10 * s), w - int(20 * s), h - int(20 * s))
        for tr in rf.tracks:
            if not (tr.confirmed and tr.visible):
                continue
            if debug:
                box = pygame.Rect(inner.x + int(tr.x * inner.w), inner.y + int(tr.y * inner.h),
                                  max(1, int(tr.w * inner.w)), max(1, int(tr.h * inner.h)))
                pygame.draw.rect(card, sc.text, box.clip(inner), width=max(1, int(s)))
            cx = inner.x + int(tr.cx * inner.w)
            cy = inner.y + int(tr.cy * inner.h)
            r = max(int(5 * s), int(tr.h * inner.h * 0.5))
            if tr.id == att.target_id:
                pygame.draw.circle(card, acc, (cx, cy), r)
                pygame.draw.circle(card, sc.text, (cx, cy), r + int(3 * s), width=max(2, int(2 * s)))
            elif tr.id == att.glance_id:
                pygame.draw.circle(card, acc, (cx, cy), r, width=max(2, int(2 * s)))
                pygame.draw.circle(card, acc, (cx, cy), max(2, r // 3))
            else:
                pygame.draw.circle(card, sc.text_dim, (cx, cy), r, width=max(2, int(2 * s)))
            lab = self._text("caption", f"#{tr.id}", sc.text if tr.id == att.target_id else sc.text_dim)
            card.blit(lab, (cx - lab.get_width() // 2, cy - r - lab.get_height() - 1))
        # onde o robô olha: cruz
        gx = inner.x + int((rf.gaze[0] * 0.5 + 0.5) * inner.w)
        gy = inner.y + int((rf.gaze[1] * 0.5 + 0.5) * inner.h)
        k = int(5 * s)
        pygame.draw.line(card, pal["orange"] if sc.key != "light" else pal["orange"], (gx - k, gy), (gx + k, gy), max(2, int(2 * s)))
        pygame.draw.line(card, pal["orange"], (gx, gy - k), (gx, gy + k), max(2, int(2 * s)))
        cap = self._text("caption", "Coordenadas · sem imagem" if debug else "Visão (espelhada) · ✚ olhar", sc.text_dim)
        card.blit(cap, (pad // 2 + int(2 * s), h - cap.get_height() - int(3 * s)))
        self.screen.blit(card, rect.topleft)

    def _toast_and_help(self, rf: RobotFrame, hud: HudInfo, pad: int) -> None:
        sc, s = self.scheme, self.s
        if hud.toast and hud.toast_age < 2.2 and not hud.show_help and not self.menu.open:
            a = 1.0 if hud.toast_age < 1.6 else (2.2 - hud.toast_age) / 0.6
            lines = self._wrap("body", hud.toast, self.screen.get_width() - 5 * pad)
            ts = [self._text("body", line, sc.text) for line in lines[:3]]
            w = max(t.get_width() for t in ts) + 3 * pad
            h = sum(t.get_height() for t in ts) + pad
            surf = pygame.Surface((w, h), pygame.SRCALPHA)
            pygame.draw.rect(surf, (*sc.chip, int(sc.chip_alpha * a)), surf.get_rect(), border_radius=h // 2)
            y = pad // 2
            for t in ts:
                tt = t.copy()
                tt.set_alpha(int(255 * a))
                surf.blit(tt, ((w - t.get_width()) // 2, y))
                y += t.get_height()
            self.screen.blit(surf, (self.screen.get_width() // 2 - w // 2, self.screen.get_height() - 2 * pad - h))
        if hud.show_help:
            lines = SHORTCUTS
            title = self._text("title", "Ajuda", sc.text)
            kw = max(self._text("headline", k, sc.text).get_width() for k, _ in lines)
            lh = self._text("body", "Ag", sc.text).get_height() + int(6 * s)
            w = min(self.screen.get_width() - 2 * pad, int(720 * s))
            wrapped = []
            for key, description in lines:
                parts = self._wrap("body", description, w - kw - 5 * pad)
                wrapped.append((key, parts))
            row_gap = int(18 * s)
            content_h = sum(len(parts) * lh + row_gap for _, parts in wrapped)
            header_h = int(76 * s)
            footer_h = lh + 2 * pad
            h = min(header_h + content_h + footer_h, self.screen.get_height() - int(80 * s) - 2 * pad)
            viewport_h = max(1, h - header_h - footer_h - int(8 * s))
            self._help_max_scroll = max(0, content_h - viewport_h)
            hud.help_scroll = min(hud.help_scroll, self._help_max_scroll)
            destination = pygame.Rect(0, 0, w, h)
            destination.center = (self.screen.get_width() // 2,
                                  int(80 * s) + (self.screen.get_height() - int(80 * s)) // 2)
            self._glass(destination, int(28 * s), blur=True)
            card = pygame.Surface((w, h), pygame.SRCALPHA)
            card.blit(title, (2 * pad, pad))
            viewport = pygame.Rect(pad, header_h, w - 2 * pad, viewport_h)
            self.help_viewport = viewport.move(destination.topleft)
            self.help_footer = pygame.Rect(destination.x + pad, destination.bottom - footer_h,
                                           w - 2 * pad, footer_h)
            card.set_clip(viewport)
            y = header_h - hud.help_scroll
            for k, parts in wrapped:
                keycap = self._text("caption", k, sc.text)
                key_rect = pygame.Rect(2 * pad, y, kw, max(lh, keycap.get_height() + int(8 * s)))
                pygame.draw.rect(card, mix(sc.chip, sc.text, .08), key_rect, border_radius=int(8 * s))
                card.blit(keycap, keycap.get_rect(center=key_rect.center))
                for d in parts:
                    card.blit(self._text("body", d, sc.text_dim), (2 * pad + kw + pad, y))
                    y += lh
                y += row_gap
            card.set_clip(None)
            footer = self._text("caption", "Setas / roda para rolar · Esc fecha", sc.text_dim)
            pygame.draw.rect(card, (*sc.chip, 240), (pad, h - footer_h, w - 2 * pad, footer_h))
            card.blit(footer, (2 * pad, h - footer_h + pad // 2))
            self.screen.blit(card, destination)
            self.help_close = pygame.Rect(destination.right - pad - int(44 * s), destination.y + pad,
                                          int(44 * s), int(44 * s))
            self._close_button(self.help_close)
            self._scrollbar(pygame.Rect(destination.x + pad, destination.y + header_h,
                                        w - 2 * pad, viewport_h), content_h, hud.help_scroll)
