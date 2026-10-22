"""Coluna "Nuria" (pygame): estado, imagem capturada, pergunta, resposta e botão de falar.

Segue as HIG: estado com texto + forma (nunca só cor), alvos >= 44 pt, respiro de 16 pt,
feedback imediato ao pressionar, confirmação ao soltar sobre o mesmo alvo, Reduzir Movimento
respeitado (troca de imagem sem transição) e alto contraste com superfícies opacas.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pygame

import theme
from theme import mix

STATE_TEXT = {
    "idle": ("Pronta", "circle", "green"),
    "connecting": ("Conectando…", "ring", "yellow"),
    "listening": ("Ouvindo…", "ring", "red"),
    "capturing": ("Capturando imagem…", "ring", "yellow"),
    "transcribing": ("Transcrevendo…", "ring", "yellow"),
    "thinking": ("Pensando…", "ring", "indigo"),
    "speaking": ("Falando…", "circle", "blue"),
    "error": ("Algo deu errado", "diamond", "red"),
}


def sidebar_width(sw: int, s: float) -> int:
    """Largura reservada à coluna; o rosto centraliza no espaço restante (nunca é coberto)."""
    if sw < 960:
        return int(min(max(300 * s, 0.48 * sw), sw - 280))
    return int(max(340 * s, min(0.34 * sw, 460 * s)))


class AssistantPanel:
    def __init__(self):
        self.rect = pygame.Rect(0, 0, 0, 0)
        self.mic = pygame.Rect(0, 0, 0, 0)
        self.type_btn = pygame.Rect(0, 0, 0, 0)
        self.vis_btn = pygame.Rect(0, 0, 0, 0)
        self.typing = False
        self.draft = ""
        self._pressed: Optional[str] = None
        self._image_version = -1
        self._image_surf: Optional[pygame.Surface] = None
        self._fade = 1.0
        self._time = 0.0
        self.visible = True

    # ---- geometria -----------------------------------------------------------
    def reserve(self, screen: pygame.Surface, s: float) -> int:
        return sidebar_width(screen.get_width(), s) if self.visible else 0

    def layout(self, r) -> None:
        sw, sh = r.screen.get_size()
        s = r.s
        pad = int(theme.PADDING * s)
        w = sidebar_width(sw, s)
        top = int(76 * s)                         # abaixo do indicador de câmera
        self.rect = pygame.Rect(sw - w - pad // 2, top, w - pad // 2, sh - top - pad)
        inner = self.rect.inflate(-2 * pad, -2 * pad)
        btn_h = int(52 * s)
        self.type_btn = pygame.Rect(inner.right - int(52 * s), inner.bottom - btn_h, int(52 * s), btn_h)
        self.vis_btn = pygame.Rect(self.type_btn.x - int(60 * s), inner.bottom - btn_h, int(52 * s), btn_h)
        self.mic = pygame.Rect(inner.x, inner.bottom - btn_h, self.vis_btn.x - inner.x - int(8 * s), btn_h)

    # ---- eventos -------------------------------------------------------------
    def handle_event(self, ev, assistant) -> bool:
        if not self.visible:
            return False
        if self.typing:
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    self._stop_typing()
                elif ev.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    if assistant.ask_text(self.draft):
                        self._stop_typing()
                elif ev.key == pygame.K_BACKSPACE:
                    self.draft = self.draft[:-1]
                elif ev.key == pygame.K_v and (ev.mod & pygame.KMOD_CTRL):
                    try:
                        clip = pygame.scrap.get_text() if hasattr(pygame.scrap, "get_text") else ""
                        self.draft = (self.draft + (clip or ""))[:300]
                    except Exception:
                        pass
                return True
            if ev.type == pygame.TEXTINPUT:
                self.draft = (self.draft + ev.text)[:300]
                return True
            if ev.type in (pygame.KEYUP, pygame.TEXTEDITING):
                return True
        if ev.type == pygame.KEYDOWN and not self.typing:
            if ev.key == pygame.K_SPACE:
                assistant.press_voice()
                return True
            if ev.key == pygame.K_v:
                assistant.set_vision(not assistant.snapshot().vision)
                return True
            if ev.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self._start_typing()
                return True
            if ev.key == pygame.K_ESCAPE and assistant.state not in ("idle", "error"):
                assistant.cancel()
                return True
        if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
            target = self._target(ev.pos)
            self._pressed = target
            if target:
                return True
            if self.typing and not self.rect.collidepoint(ev.pos):
                self._stop_typing()
            return self.rect.collidepoint(ev.pos)
        if ev.type == pygame.MOUSEBUTTONUP and ev.button == 1:
            pressed, self._pressed = self._pressed, None
            target = self._target(ev.pos)
            if pressed and pressed == target:
                if target == "vision":
                    assistant.set_vision(not assistant.snapshot().vision)
                elif target == "mic":
                    if self.typing and self.draft.strip():
                        if assistant.ask_text(self.draft):
                            self._stop_typing()
                    else:
                        self._stop_typing()
                        assistant.press_voice()
                else:
                    self._stop_typing() if self.typing else self._start_typing()
                return True
            return self.rect.collidepoint(ev.pos)
        if ev.type in (pygame.MOUSEMOTION, pygame.MOUSEWHEEL) and self.rect.collidepoint(pygame.mouse.get_pos()):
            return ev.type == pygame.MOUSEWHEEL
        return False

    def _target(self, pos):
        for name, rect in (("mic", self.mic), ("vision", self.vis_btn), ("type", self.type_btn)):
            if rect.collidepoint(pos):
                return name
        return None

    def _start_typing(self):
        self.typing, self.draft = True, ""
        try:
            pygame.key.set_repeat(400, 35)
            pygame.key.start_text_input()
        except Exception:
            pass

    def _stop_typing(self):
        self.typing = False
        try:
            pygame.key.set_repeat(0)
            pygame.key.stop_text_input()
        except Exception:
            pass

    # ---- desenho ---------------------------------------------------------------
    def _surface_from(self, image_bgr) -> pygame.Surface:
        rgb = np.ascontiguousarray(image_bgr[:, :, ::-1])
        h, w = rgb.shape[:2]
        return pygame.image.frombuffer(rgb.tobytes(), (w, h), "RGB").convert()

    def _rounded(self, surf: pygame.Surface, radius: int) -> pygame.Surface:
        out = pygame.Surface(surf.get_size(), pygame.SRCALPHA)
        out.blit(surf, (0, 0))
        mask = pygame.Surface(surf.get_size(), pygame.SRCALPHA)
        pygame.draw.rect(mask, (255, 255, 255, 255), mask.get_rect(), border_radius=radius)
        out.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
        return out

    def _marker(self, screen, kind, center, size, color, s):
        x, y = center
        r = size // 2
        if kind == "circle":
            pygame.draw.circle(screen, color, center, r)
        elif kind == "ring":
            pygame.draw.circle(screen, color, center, r, max(2, int(1.8 * s)))
        else:
            pygame.draw.polygon(screen, color, [(x, y - r - 1), (x + r + 1, y), (x, y + r + 1), (x - r - 1, y)])

    def draw(self, r, assistant, dt: float, reduce_motion: bool, config_label: str = "") -> None:
        if not self.visible:
            return
        self._time += dt
        self.layout(r)
        sc, s, scr = r.scheme, r.s, r.screen
        pal = sc.palette
        pad = int(theme.PADDING * s)
        snap = assistant.snapshot()
        r._glass(self.rect, int(24 * s), blur=True)
        inner = self.rect.inflate(-2 * pad, -2 * pad)
        x, y, w = inner.x, inner.y, inner.w

        # cabeçalho: nome + estado (texto + forma)
        title = r._text("title", "Nuria", sc.text)
        scr.blit(title, (x, y))
        label, mk, colkey = STATE_TEXT.get(snap.state, STATE_TEXT["idle"])
        if snap.state == "idle" and snap.message_kind == "warning":
            label, mk, colkey = "Atenção", "diamond", "orange"
        col = pal[colkey]
        st = r._text("headline", label, sc.text)
        pill = pygame.Rect(0, 0, st.get_width() + int(40 * s), int(32 * s))
        pill.midright = (inner.right, y + title.get_height() // 2)
        pygame.draw.rect(scr, mix(sc.chip, col, .16 if sc.key != "contrast" else 0), pill, border_radius=pill.h // 2)
        if sc.key == "contrast":
            pygame.draw.rect(scr, sc.separator, pill, 1, border_radius=pill.h // 2)
        size = int(10 * s)
        if snap.state == "listening" and not reduce_motion:
            size = int(size * (1.0 + 0.6 * snap.level))
        self._marker(scr, mk, (pill.x + int(16 * s), pill.centery), size, col, s)
        scr.blit(st, (pill.x + int(28 * s), pill.centery - st.get_height() // 2))
        y += title.get_height() + int(4 * s)
        sub = config_label or assistant.backend_label()
        scr.blit(r._text("footnote", r._fit("footnote", sub, w), sc.text_dim), (x, y))
        y += int(26 * s)

        # imagem capturada
        scr.blit(r._text("section", "IMAGEM CAPTURADA" if snap.vision else "VISÃO PAUSADA", sc.text_dim), (x, y))
        y += int(22 * s)
        img_h = int(w * 9 / 16)
        avail = self.mic.top - y - int(190 * s)
        img_h = max(int(90 * s), min(img_h, avail))
        img_rect = pygame.Rect(x, y, w, img_h)
        radius = int(14 * s)
        if snap.image is not None and snap.image_version != self._image_version:
            self._image_version = snap.image_version
            self._image_surf = self._surface_from(snap.image)
            self._fade = 0.0 if not reduce_motion else 1.0
        pygame.draw.rect(scr, mix(sc.chip, sc.text, .07), img_rect, border_radius=radius)
        if self._image_surf is not None and snap.vision:
            iw, ih = self._image_surf.get_size()
            k = min(img_rect.w / iw, img_rect.h / ih)
            fit = pygame.transform.smoothscale(self._image_surf, (max(1, int(iw * k)), max(1, int(ih * k))))
            fit = self._rounded(fit, radius)
            self._fade = min(1.0, self._fade + dt / 0.25)
            fit.set_alpha(int(255 * self._fade))
            scr.blit(fit, fit.get_rect(center=img_rect.center))
        else:
            hint = r._wrap("footnote", "A foto aparece aqui quando você fizer uma pergunta." if snap.vision else
                           "Só conversa: nenhuma imagem é enviada. V ou o botão da câmera retomam.", w - 4 * pad)
            yy = img_rect.centery - len(hint) * int(9 * s)
            for line in hint:
                t = r._text("footnote", line, sc.text_dim)
                scr.blit(t, (img_rect.centerx - t.get_width() // 2, yy))
                yy += t.get_height()
        if snap.state == "capturing" and not reduce_motion:
            flash = pygame.Surface(img_rect.size, pygame.SRCALPHA)
            a = int(90 * (0.5 + 0.5 * math.sin(self._time * 9)))
            pygame.draw.rect(flash, (255, 255, 255, a), flash.get_rect(), border_radius=radius)
            scr.blit(flash, img_rect)
        y = img_rect.bottom + int(6 * s)
        if self._image_surf is not None and snap.vision:
            scr.blit(r._text("caption", r._fit("caption", "Enviada à IA só para esta pergunta · não é salva", w), sc.text_dim), (x, y))
        y += int(26 * s)

        # pergunta
        scr.blit(r._text("section", "PERGUNTA", sc.text_dim), (x, y))
        y += int(22 * s)
        if self.typing:
            field = pygame.Rect(x, y, w, int(44 * s))
            pygame.draw.rect(scr, mix(sc.chip, sc.text, .06), field, border_radius=int(10 * s))
            pygame.draw.rect(scr, pal["blue"], field, max(2, int(2 * s)), border_radius=int(10 * s))
            caret = "|" if int(self._time * 2) % 2 == 0 or reduce_motion else " "
            shown, limit = self.draft, w - 3 * pad
            while shown and r._font("body").size(shown)[0] > limit:
                shown = shown[1:]
            text = r._text("body", shown, sc.text) if shown else None
            tx = field.x + pad
            if text:
                scr.blit(text, (tx, field.centery - text.get_height() // 2))
                tx += text.get_width()
            else:
                ph = r._text("body", "Digite e tecle Enter", sc.text_dim)
                scr.blit(ph, (tx + int(6 * s), field.centery - ph.get_height() // 2))
            c = r._text("body", caret, sc.text)
            scr.blit(c, (tx, field.centery - c.get_height() // 2))
            y = field.bottom + int(14 * s)
        else:
            q = snap.question or ("…" if snap.state in ("listening", "transcribing", "capturing", "thinking", "speaking") else "—")
            lines = r._wrap("body", "“" + q + "”" if snap.question else q, w)[:3]
            for line in lines:
                t = r._text("body", line, sc.text)
                scr.blit(t, (x, y))
                y += t.get_height()
            y += int(14 * s)

        # resposta / mensagem
        is_error = snap.state == "error" or snap.message_kind == "warning"
        scr.blit(r._text("section", "RESPOSTA" if not is_error or snap.answer else "AVISO", sc.text_dim), (x, y))
        y += int(22 * s)
        bottom_limit = self.mic.top - int(40 * s)
        body = snap.answer
        if snap.state == "thinking":
            dots = "." * (1 + int(self._time * 3) % 3) if not reduce_motion else "…"
            body = "Interpretando a imagem" + dots
        elif snap.state == "error" or (not snap.answer and snap.message):
            body = ""
        box_lines = r._wrap("body", body, w) if body else []
        lh = r._font("body").get_linesize()
        if snap.message and (snap.state in ("error", "idle")):
            msg_lines = r._wrap("body", snap.message, w - int(24 * s))
        else:
            msg_lines = []
        room = max(1, (bottom_limit - y) // lh)
        if msg_lines and box_lines:
            room_answer = max(1, room - len(msg_lines) - 1)
        else:
            room_answer = room
        if len(box_lines) > room_answer:
            box_lines = box_lines[:room_answer]
            box_lines[-1] = r._fit("body", box_lines[-1] + " …", w)
        for line in box_lines:
            scr.blit(r._text("body", line, sc.text), (x, y))
            y += lh
        if msg_lines:
            y += int(6 * s) if box_lines else 0
            mcol = pal["red"] if snap.state == "error" else pal["orange"]
            self._marker(scr, "diamond", (x + int(7 * s), y + lh // 2), int(10 * s), mcol, s)
            for line in msg_lines[:max(1, (bottom_limit - y) // lh)]:
                scr.blit(r._text("body", line, sc.text), (x + int(24 * s), y))
                y += lh

        # tempos (evidência de latência)
        if snap.timings and snap.state in ("speaking", "idle"):
            names = dict(stt="transcrição", ia="IA", voz="voz", total_ate_falar="até falar",
                         primeiro_audio="1ª fala em", resposta_completa="resposta")
            parts = [f"{names[k]} {v:.1f} s".replace(".", ",") for k, v in snap.timings.items() if k in names]
            t = r._text("caption", r._fit("caption", " · ".join(parts), w), sc.text_dim)
            scr.blit(t, (x, self.mic.top - int(30 * s)))

        # botões
        self._draw_mic(r, snap, reduce_motion)
        self._draw_vision_button(r, snap.vision)
        self._draw_type_button(r)

    def _draw_mic(self, r, snap, reduce_motion):
        sc, s, scr, pal = r.scheme, r.s, r.screen, r.scheme.palette
        st = snap.state
        if self.typing and self.draft.strip():
            label, base, enabled = "Enviar pergunta", pal["blue"], True
        elif st == "listening":
            label, base, enabled = "Parar e enviar", pal["red"], True
        elif st == "speaking":
            label, base, enabled = "Interromper e falar", pal["blue"], True
        elif st == "connecting":
            label, base, enabled = "Conectando…", mix(sc.chip, sc.text, .25), False
        elif st in ("capturing", "transcribing", "thinking"):
            label, base, enabled = "Processando…", mix(sc.chip, sc.text, .25), False
        else:
            label, base, enabled = "Clique para falar", pal["blue"], True
        pressed = self._pressed == "mic"
        color = mix(base, (0, 0, 0), .15) if pressed else base
        if sc.key == "contrast":
            color = (0, 0, 0)
        rect = self.mic.inflate(-int(2 * s), -int(2 * s)) if pressed else self.mic
        if st == "listening" and not reduce_motion:
            halo = rect.inflate(int(10 * s * snap.level) + 2, int(10 * s * snap.level) + 2)
            pygame.draw.rect(scr, mix(sc.chip, base, .35), halo, border_radius=halo.h // 2)
        pygame.draw.rect(scr, color, rect, border_radius=rect.h // 2)
        if sc.key == "contrast":
            pygame.draw.rect(scr, (255, 255, 255), rect, max(2, int(2 * s)), border_radius=rect.h // 2)
        fg = (255, 255, 255) if enabled or sc.key == "contrast" else sc.text_dim
        text = r._text("headline", label, fg)
        icon_w = int(22 * s)
        total = icon_w + int(10 * s) + text.get_width()
        x0 = rect.centerx - total // 2
        sending = self.typing and self.draft.strip()
        self._mic_icon(scr, (x0 + icon_w // 2, rect.centery), s, fg, square=st == "listening" and not sending,
                       arrow=bool(sending))
        scr.blit(text, (x0 + icon_w + int(10 * s), rect.centery - text.get_height() // 2))

    def _mic_icon(self, scr, c, s, color, square=False, arrow=False):
        x, y = c
        k = lambda n: max(1, round(n * s))
        w = max(2, round(2 * s))
        if arrow:
            pygame.draw.line(scr, color, (x, y + k(9)), (x, y - k(8)), w + 1)
            pygame.draw.lines(scr, color, False, [(x - k(7), y - k(1)), (x, y - k(9)), (x + k(7), y - k(1))], w + 1)
            return
        if square:
            pygame.draw.rect(scr, color, (x - k(6), y - k(6), k(12), k(12)), border_radius=k(3))
            return
        pygame.draw.rect(scr, color, (x - k(4), y - k(10), k(8), k(14)), border_radius=k(4))
        pygame.draw.arc(scr, color, (x - k(8), y - k(6), k(16), k(14)), math.pi, 2 * math.pi, w)
        pygame.draw.line(scr, color, (x, y + k(8)), (x, y + k(11)), w)

    def _draw_vision_button(self, r, on: bool):
        sc, s, scr = r.scheme, r.s, r.screen
        rect = self.vis_btn
        pressed = self._pressed == "vision"
        fill = mix(sc.chip, sc.text, .16 if pressed else .08)
        if not on:
            fill = mix(sc.chip, sc.palette["orange"], .22 if sc.key != "contrast" else 0)
        pygame.draw.rect(scr, fill, rect, border_radius=rect.h // 2)
        if sc.key == "contrast" or not on:
            pygame.draw.rect(scr, sc.palette["orange"] if not on else sc.separator, rect,
                             max(1, int(2 * s)), border_radius=rect.h // 2)
        k = lambda n: max(1, round(n * s))
        w = max(2, round(1.6 * s))
        x, y = rect.center
        pygame.draw.rect(scr, sc.text, (x - k(11), y - k(7), k(22), k(15)), w, border_radius=k(4))
        pygame.draw.circle(scr, sc.text, (x, y + k(1)), k(4), w)
        if not on:                                     # risco diagonal = pausada (forma, não só cor)
            pygame.draw.line(scr, sc.chip, (x - k(13), y - k(11)), (x + k(13), y + k(11)), w + 3)
            pygame.draw.line(scr, sc.text, (x - k(13), y - k(11)), (x + k(13), y + k(11)), w)

    def _draw_type_button(self, r):
        sc, s, scr = r.scheme, r.s, r.screen
        rect = self.type_btn
        pressed = self._pressed == "type"
        fill = mix(sc.chip, sc.text, .16 if pressed else (.12 if self.typing else .08))
        pygame.draw.rect(scr, fill, rect, border_radius=rect.h // 2)
        if sc.key == "contrast" or self.typing:
            pygame.draw.rect(scr, sc.separator if not self.typing else sc.palette["blue"], rect,
                             max(1, int(2 * s)), border_radius=rect.h // 2)
        # teclado
        k = lambda n: max(1, round(n * s))
        x, y = rect.center
        body = pygame.Rect(x - k(11), y - k(7), k(22), k(14))
        pygame.draw.rect(scr, sc.text, body, max(2, round(1.6 * s)), border_radius=k(3))
        for row in range(2):
            for col in range(4):
                pygame.draw.rect(scr, sc.text, (body.x + k(3) + col * k(4.5), body.y + k(3) + row * k(4), k(2), k(2)))
