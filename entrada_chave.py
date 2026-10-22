"""Folha para colar/digitar a chave da OpenAI (aba Ajustes → API).

A chave nunca aparece inteira: só pontos e os 4 últimos caracteres. Ctrl+V cola, Enter salva, Esc cancela.
Enquanto a folha está aberta, nenhuma tecla vaza para os atalhos do app.
"""
from __future__ import annotations

from typing import Optional, Tuple

import pygame

import credenciais
import theme
from theme import mix


def clipboard_text() -> str:
    try:
        text = pygame.scrap.get_text()                  # pygame-ce >= 2.2
        if text:
            return text
    except Exception:
        pass
    try:
        import tkinter
        root = tkinter.Tk()
        root.withdraw()
        text = root.clipboard_get()
        root.destroy()
        return text
    except Exception:
        return ""


class KeyEntry:
    def __init__(self):
        self.active = False
        self.text = ""
        self.error = ""
        self.rect = pygame.Rect(0, 0, 0, 0)
        self.save_btn = pygame.Rect(0, 0, 0, 0)
        self.paste_btn = pygame.Rect(0, 0, 0, 0)
        self.cancel_btn = pygame.Rect(0, 0, 0, 0)
        self._pressed: Optional[str] = None

    def open(self) -> None:
        self.active, self.text, self.error = True, "", ""
        try:
            pygame.key.set_repeat(400, 35)
            pygame.key.start_text_input()
        except Exception:
            pass

    def close(self) -> None:
        self.active, self.text = False, ""
        try:
            pygame.key.set_repeat(0)
            pygame.key.stop_text_input()
        except Exception:
            pass

    def _paste(self) -> None:
        clip = "".join(clipboard_text().split())          # chaves não têm espaços nem quebras de linha
        if clip:
            self.text = (self.text + clip)[:300]
            self.error = ""
        else:
            self.error = "Área de transferência vazia. Copie a chave no site da OpenAI e tente de novo."

    def _submit(self) -> Optional[Tuple[str, str]]:
        key = self.text.strip()
        if not credenciais.looks_valid(key):
            self.error = "Isso não parece uma chave da OpenAI (começa com sk- e não tem espaços)."
            return None
        self.close()
        return ("save", key)

    def handle_event(self, ev) -> Optional[Tuple[str, str]]:
        """Consome todos os eventos enquanto ativa. Devolve ("save", chave) ou ("cancel", "")."""
        if ev.type == pygame.KEYDOWN:
            if ev.key == pygame.K_ESCAPE:
                self.close()
                return ("cancel", "")
            if ev.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                return self._submit()
            if ev.key == pygame.K_BACKSPACE:
                self.text = self.text[:-1]
            elif ev.key == pygame.K_v and (ev.mod & pygame.KMOD_CTRL):
                self._paste()
            return None
        if ev.type == pygame.TEXTINPUT:
            if not (pygame.key.get_mods() & pygame.KMOD_CTRL):
                self.text = (self.text + "".join(ev.text.split()))[:300]
                self.error = ""
            return None
        if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
            for name, rect in (("save", self.save_btn), ("paste", self.paste_btn), ("cancel", self.cancel_btn)):
                if rect.collidepoint(ev.pos):
                    self._pressed = name
            return None
        if ev.type == pygame.MOUSEBUTTONUP and ev.button == 1:
            pressed, self._pressed = self._pressed, None
            if pressed == "save" and self.save_btn.collidepoint(ev.pos):
                return self._submit()
            if pressed == "paste" and self.paste_btn.collidepoint(ev.pos):
                self._paste()
            if pressed == "cancel" and self.cancel_btn.collidepoint(ev.pos):
                self.close()
                return ("cancel", "")
        return None

    def masked(self) -> str:
        if not self.text:
            return ""
        tail = self.text[-4:] if len(self.text) > 8 else ""
        return "•" * min(24, max(0, len(self.text) - len(tail))) + tail

    def draw(self, r, source: str = "") -> None:
        if not self.active:
            return
        sc, s, scr = r.scheme, r.s, r.screen
        pad = int(theme.PADDING * s)
        sw, sh = scr.get_size()
        shade = pygame.Surface((sw, sh), pygame.SRCALPHA)
        shade.fill((0, 0, 0, 110 if sc.key != "light" else 60))
        scr.blit(shade, (0, 0))
        w = min(sw - 2 * pad, int(560 * s))
        h = int(290 * s)
        self.rect = pygame.Rect((sw - w) // 2, (sh - h) // 2, w, h)
        r._glass(self.rect, int(24 * s), blur=True)
        x, y = self.rect.x + 2 * pad, self.rect.y + pad
        scr.blit(r._text("title", "Chave da OpenAI", sc.text), (x, y))
        y += int(44 * s)
        for line in r._wrap("footnote", "Cole a chave (Ctrl+V) e tecle Enter. Ela fica só neste computador, no arquivo .env, "
                                        "que não vai para o Git. Na tela aparecem apenas os 4 últimos caracteres.", w - 4 * pad):
            scr.blit(r._text("footnote", line, sc.text_dim), (x, y))
            y += r._font("footnote").get_linesize()
        y += int(10 * s)
        field = pygame.Rect(x, y, w - 4 * pad, int(48 * s))
        pygame.draw.rect(scr, mix(sc.chip, sc.text, .06), field, border_radius=int(12 * s))
        pygame.draw.rect(scr, sc.palette["red"] if self.error else sc.palette["blue"], field, max(2, int(2 * s)),
                         border_radius=int(12 * s))
        shown = self.masked()
        t = r._text("body", shown if shown else "sk-…", sc.text if shown else sc.text_dim)
        scr.blit(t, (field.x + pad, field.centery - t.get_height() // 2))
        if shown:
            caret = r._text("body", "|", sc.text)
            scr.blit(caret, (field.x + pad + t.get_width() + 2, field.centery - caret.get_height() // 2))
        y = field.bottom + int(8 * s)
        note = self.error or ("Atenção: a variável OPENAI_API_KEY do Windows tem prioridade sobre o .env."
                              if source == "windows" else f"{len(self.text)} caracteres" if self.text else "")
        if note:
            for line in r._wrap("footnote", note, w - 4 * pad)[:2]:
                scr.blit(r._text("footnote", line, sc.palette["red"] if self.error else sc.text_dim), (x, y))
                y += r._font("footnote").get_linesize()
        bh = int(48 * s)
        by = self.rect.bottom - pad - bh
        bw = (w - 4 * pad - 2 * int(10 * s)) // 3
        self.cancel_btn = pygame.Rect(x, by, bw, bh)
        self.paste_btn = pygame.Rect(self.cancel_btn.right + int(10 * s), by, bw, bh)
        self.save_btn = pygame.Rect(self.paste_btn.right + int(10 * s), by, bw, bh)
        for rect, label, primary, name in ((self.cancel_btn, "Cancelar", False, "cancel"),
                                           (self.paste_btn, "Colar", False, "paste"),
                                           (self.save_btn, "Salvar", True, "save")):
            fill = sc.palette["blue"] if primary else mix(sc.chip, sc.text, .10)
            if self._pressed == name:
                fill = mix(fill, (0, 0, 0), .15)
            if sc.key == "contrast":
                fill = (0, 0, 0)
            pygame.draw.rect(scr, fill, rect, border_radius=rect.h // 2)
            if sc.key == "contrast":
                pygame.draw.rect(scr, (255, 255, 255), rect, max(2, int(2 * s)), border_radius=rect.h // 2)
            fg = (255, 255, 255) if primary or sc.key == "contrast" else sc.text
            lt = r._text("headline", label, fg)
            scr.blit(lt, lt.get_rect(center=rect.center))
