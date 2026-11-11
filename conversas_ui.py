"""Tela "Conversas": lista com busca à esquerda e a conversa em balões à direita (como WhatsApp/ChatGPT).

Tudo é texto salvo em logs/sessoes.jsonl (ver historico.py). Retomar leva as últimas trocas como contexto de uma sessão nova.
Teclado: digitar busca · ↑/↓ escolhe · Enter retoma · Tab alterna Conversas/Memória · Esc fecha (ou limpa a busca).
Aba Memória: instruções vigentes e preferências guardadas, com Revogar/Apagar. Lê o banco só ao abrir a aba e após cada ação.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import pygame

import historico
import theme
from theme import mix


class ChatHistory:
    def __init__(self):
        self.active = False
        self.query = ""
        self.sel = 0                             # índice na lista filtrada
        self.list_scroll = 0.0                   # px
        self.chat_scroll: Optional[float] = None  # px; None = colado no fim (mensagem mais recente)
        self.confirm_delete = False
        self.rect = pygame.Rect(0, 0, 0, 0)
        self.close_btn = pygame.Rect(0, 0, 0, 0)
        self.new_btn = pygame.Rect(0, 0, 0, 0)
        self.resume_btn = pygame.Rect(0, 0, 0, 0)
        self.delete_btn = pygame.Rect(0, 0, 0, 0)
        self.copy_btn = pygame.Rect(0, 0, 0, 0)
        self.list_rect = pygame.Rect(0, 0, 0, 0)
        self.chat_rect = pygame.Rect(0, 0, 0, 0)
        self.rows: List[Tuple[pygame.Rect, dict]] = []
        self._sessions: List[dict] = []
        self._pressed: Optional[str] = None
        self._max_list = 0.0
        self._max_chat = 0.0
        self._bubbles_key = None
        self._bubbles: List[tuple] = []
        self._bubbles_h = 0
        self.tab = "chat"                        # chat | memory
        self.memory = None                       # memoria.Memory (opcional)
        self.tab_btns: List[Tuple[pygame.Rect, str]] = []
        self.mem_rows: List[Tuple[pygame.Rect, str, int, str]] = []   # (botão, tipo, id, texto)
        self._mem_data: Optional[dict] = None
        self.mem_scroll = 0.0
        self._max_mem = 0.0
        self.mem_rect = pygame.Rect(0, 0, 0, 0)

    # ---- abrir/fechar ---------------------------------------------------------------
    def open(self, current_id: str = "", memory=None, tab: str = "chat") -> None:
        self.memory = memory if memory is not None else self.memory
        self.tab, self._mem_data, self.mem_scroll = tab, None, 0.0
        self.active, self.query, self.sel, self.list_scroll, self.chat_scroll = True, "", 0, 0.0, None
        self.confirm_delete = False
        self._refresh()
        for i, s in enumerate(self._sessions):          # começa na conversa atual, se já tiver registro
            if s["id"] == current_id:
                self.sel = i
        try:
            pygame.key.set_repeat(400, 35)
            pygame.key.start_text_input()
        except Exception:
            pass

    def close(self) -> None:
        self.active = False
        try:
            pygame.key.set_repeat(0)
            pygame.key.stop_text_input()
        except Exception:
            pass

    def _refresh(self) -> None:
        self._sessions = historico.list_sessions(self.query)
        self.sel = max(0, min(self.sel, len(self._sessions) - 1))

    @property
    def selected(self) -> Optional[dict]:
        return self._sessions[self.sel] if 0 <= self.sel < len(self._sessions) else None

    def _select(self, index: int) -> None:
        if index != self.sel:
            self.sel, self.chat_scroll, self.confirm_delete = index, None, False

    # ---- eventos -----------------------------------------------------------------------
    def handle_event(self, ev) -> Optional[Tuple[str, str]]:
        """Consome tudo enquanto aberta. Devolve ("resume"|"delete"|"new"|"close", id) ou None."""
        if ev.type == pygame.KEYDOWN and ev.key == pygame.K_TAB:
            self._set_tab("memory" if self.tab == "chat" else "chat")
            return None
        if self.tab == "memory" and ev.type in (pygame.KEYDOWN, pygame.TEXTINPUT) and not (
                ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE):
            return None
        if ev.type == pygame.KEYDOWN:
            if ev.key == pygame.K_ESCAPE:
                if self.query:
                    self.query = ""
                    self._refresh()
                    return None
                self.close()
                return ("close", "")
            if ev.key == pygame.K_UP:
                self._select(max(0, self.sel - 1))
            elif ev.key == pygame.K_DOWN:
                self._select(min(len(self._sessions) - 1, self.sel + 1))
            elif ev.key in (pygame.K_RETURN, pygame.K_KP_ENTER) and self.selected:
                return self._finish("resume")
            elif ev.key == pygame.K_BACKSPACE and self.query:
                self.query = self.query[:-1]
                self._refresh()
                self.chat_scroll = None
            elif ev.key == pygame.K_PAGEUP:
                self._scroll_chat(-self.chat_rect.h * 0.8)
            elif ev.key == pygame.K_PAGEDOWN:
                self._scroll_chat(self.chat_rect.h * 0.8)
            return None
        if ev.type == pygame.TEXTINPUT:
            if not (pygame.key.get_mods() & pygame.KMOD_CTRL):
                self.query = (self.query + ev.text)[:60]
                self._refresh()
                self.chat_scroll = None
            return None
        if ev.type == pygame.MOUSEWHEEL:
            pos = pygame.mouse.get_pos()
            amount = -ev.y * 48
            if self.tab == "memory":
                self.mem_scroll = max(0.0, min(self._max_mem, self.mem_scroll + amount))
            elif self.list_rect.collidepoint(pos):
                self.list_scroll = max(0.0, min(self._max_list, self.list_scroll + amount))
            else:
                self._scroll_chat(amount)
            return None
        if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
            self._pressed = self._hit(ev.pos)
            return None
        if ev.type == pygame.MOUSEBUTTONUP and ev.button == 1:
            pressed, self._pressed = self._pressed, None
            hit = self._hit(ev.pos)
            if not pressed or pressed != hit:
                if not self.rect.collidepoint(ev.pos):
                    self.close()
                    return ("close", "")
                return None
            if hit == "close":
                self.close()
                return ("close", "")
            if isinstance(hit, tuple) and hit[0] == "tab":
                self._set_tab(hit[1])
                return None
            if isinstance(hit, tuple) and hit[0] == "mem":
                return self._mem_action(hit[1], hit[2], hit[3])
            if hit == "new":
                return self._finish("new")
            if hit == "copy" and self.selected:
                return ("copy", self.selected["id"])
            if hit == "resume" and self.selected:
                return self._finish("resume")
            if hit == "delete" and self.selected:
                if not self.confirm_delete:
                    self.confirm_delete = True
                    return None
                sid = self.selected["id"]
                self.confirm_delete = False
                self._sessions = [s for s in self._sessions if s["id"] != sid]
                self.sel = max(0, min(self.sel, len(self._sessions) - 1))
                self.chat_scroll = None
                return ("delete", sid)
            if isinstance(hit, tuple) and hit[0] == "row":
                self._select(hit[1])
        return None

    def _finish(self, kind: str) -> Tuple[str, str]:
        sid = self.selected["id"] if self.selected else ""
        self.close()
        return (kind, sid)

    def _hit(self, pos):
        for rect, name in self.tab_btns:
            if rect.collidepoint(pos):
                return ("tab", name)
        if self.tab == "memory":
            if self.close_btn.collidepoint(pos):
                return "close"
            for rect, kind, ident, text in self.mem_rows:
                if rect.collidepoint(pos):
                    return ("mem", kind, ident, text)
            return None
        for name, rect in (("close", self.close_btn), ("new", self.new_btn),
                           ("resume", self.resume_btn), ("delete", self.delete_btn), ("copy", self.copy_btn)):
            if rect.w and rect.collidepoint(pos):
                return name
        if self.list_rect.collidepoint(pos):
            for rect, s in self.rows:
                if rect.collidepoint(pos):
                    return ("row", self._sessions.index(s))
        return None

    # ---- aba Memória ------------------------------------------------------------------------
    def _set_tab(self, tab: str) -> None:
        if tab != self.tab:
            self.tab, self._mem_data, self.mem_scroll, self._pressed = tab, None, 0.0, None

    def _mem_ready(self) -> bool:
        return self.memory is not None and getattr(self.memory, "active", False)

    def _load_mem(self) -> dict:
        if self._mem_data is None:
            self._mem_data = self.memory.overview() if self._mem_ready() else {"instructions": [], "memories": []}
        return self._mem_data

    def _mem_action(self, kind: str, ident: int, text: str):
        """Revogar/apagar: entra na fila de escrita da memória (não espera o disco); a lista some na hora."""
        if not self._mem_ready():
            return None
        data = self._load_mem()
        if kind == "revoke":
            self.memory.revoke_by_id(ident)
            data["instructions"] = [i for i in data["instructions"] if i["id"] != ident]
            return ("mem_note", f"a instrução \"{text}\" foi revogada; não a siga mais")
        if kind == "delete":
            self.memory.delete_memory(ident)
            data["memories"] = [m for m in data["memories"] if m["id"] != ident]
            return ("mem_note", "")
        return None

    def _draw_tabs(self, r, x: int, cy: int) -> None:
        sc, s, scr = r.scheme, r.s, r.screen
        self.tab_btns = []
        for name, label in (("chat", "Conversas"), ("memory", "Memória")):
            img = r._text("headline", label, (255, 255, 255) if self.tab == name else sc.text)
            rect = pygame.Rect(x, cy - int(18 * s), img.get_width() + int(28 * s), int(36 * s))
            fill = sc.palette["blue"] if self.tab == name else mix(sc.chip, sc.text, .08)
            pygame.draw.rect(scr, fill, rect, border_radius=rect.h // 2)
            scr.blit(img, img.get_rect(center=rect.center))
            self.tab_btns.append((rect, name))
            x = rect.right + int(8 * s)

    def _draw_memory(self, r, area: pygame.Rect) -> None:
        sc, s, scr = r.scheme, r.s, r.screen
        pad = int(theme.PADDING * s)
        self.mem_rect, self.mem_rows = area, []
        self.new_btn = self.resume_btn = self.delete_btn = self.copy_btn = pygame.Rect(0, 0, 0, 0)
        if not self._mem_ready():
            for i, line in enumerate(r._wrap("body", "Memória desligada. Ligue em Ajustes → Conversa e IA → Memória "
                                             "(ou o app foi aberto com --sem-registro/--sem-memoria).", area.w)):
                scr.blit(r._text("body", line, sc.text_dim), (area.x, area.y + i * r._font("body").get_linesize()))
            return
        data = self._load_mem()
        line_h = r._font("body").get_linesize()
        bw, bh = int(104 * s), int(36 * s)
        items = [("title", "INSTRUÇÕES VIGENTES", 0)]
        items += [("revoke", i["instruction"], i["id"], i["actor"]) for i in data["instructions"]] or [("empty", "Nenhuma. Diga ao robô “lembre-se de que…”.", 0)]
        items += [("title", "PREFERÊNCIAS E CONHECIMENTO", 0)]
        items += [("delete", m["content"], m["id"], m["validation_status"]) for m in data["memories"]] or [("empty", "Nada guardado ainda.", 0)]
        items += [("empty", "Para validar, editar ou buscar: abra /memoria no navegador (Ajustes → Conexões → Painel de memória).", 0)]
        clip = scr.get_clip()
        scr.set_clip(area)
        y = area.y - int(self.mem_scroll)
        for it in items:
            kind, text = it[0], it[1]
            if kind == "title":
                y += int(6 * s)
                img = r._text("caption", text, sc.text_dim)
                scr.blit(img, (area.x, y))
                y += img.get_height() + int(6 * s)
                continue
            if kind == "empty":
                for line in r._wrap("footnote", text, area.w):
                    scr.blit(r._text("footnote", line, sc.text_dim), (area.x, y))
                    y += r._font("footnote").get_linesize()
                y += int(6 * s)
                continue
            tag = {"operator": "instrução", "painel": "instrução", "user": "instrução", "validated": "validado",
                   "candidate": "não verificado"}.get(it[3], "")        # a origem da instrução não aparece na tela do robô
            lines = r._wrap("body", text, area.w - bw - 2 * pad)
            h = max(bh, len(lines) * line_h + r._font("footnote").get_linesize()) + int(14 * s)
            row = pygame.Rect(area.x, y, area.w, h)
            pygame.draw.rect(scr, mix(sc.chip, sc.text, .05), row, border_radius=int(14 * s))
            ty = row.y + int(7 * s)
            scr.blit(r._text("footnote", tag, sc.text_dim), (row.x + pad, ty))
            ty += r._font("footnote").get_linesize()
            for line in lines:
                scr.blit(r._text("body", line, sc.text), (row.x + pad, ty))
                ty += line_h
            btn = pygame.Rect(row.right - bw - int(8 * s), row.centery - bh // 2, bw, bh)
            name = ("mem", kind, it[2], text)
            self._button(r, btn, "Revogar" if kind == "revoke" else "Apagar", name, False, danger=self._pressed == name)
            self.mem_rows.append((btn, kind, it[2], text))
            y = row.bottom + int(8 * s)
        scr.set_clip(clip)
        self._max_mem = max(0.0, y + self.mem_scroll - area.y - area.h)

    def _scroll_chat(self, amount: float) -> None:
        cur = self._max_chat if self.chat_scroll is None else self.chat_scroll
        new = max(0.0, min(self._max_chat, cur + amount))
        self.chat_scroll = None if new >= self._max_chat - 1 else new

    # ---- desenho --------------------------------------------------------------------------
    def _messages(self, sid: str) -> List[Tuple[str, str]]:
        out: List[Tuple[str, str]] = []
        for q, a in historico.load(sid):
            if q:
                out.append(("user", q))
            if a:
                out.append(("bot", a))
        return out

    def draw(self, r, current_id: str = "") -> None:
        if not self.active:
            return
        sc, s, scr = r.scheme, r.s, r.screen
        pad = int(theme.PADDING * s)
        sw, sh = scr.get_size()
        shade = pygame.Surface((sw, sh), pygame.SRCALPHA)
        shade.fill((0, 0, 0, 130 if sc.key != "light" else 70))
        scr.blit(shade, (0, 0))
        w, h = min(sw - 2 * pad, int(1120 * s)), sh - 2 * pad
        self.rect = pygame.Rect((sw - w) // 2, pad, w, h)
        r._glass(self.rect, int(24 * s), blur=True)
        x0, y0 = self.rect.x + pad, self.rect.y + pad
        title = r._text("title", "Conversas", sc.text)          # só a altura: as abas fazem o papel do título
        cb = int(40 * s)
        self.close_btn = pygame.Rect(self.rect.right - pad - cb, y0 - int(4 * s), cb, cb)
        self._button(r, self.close_btn, "", "close", False)
        k, cx, cy = int(7 * s), self.close_btn.centerx, self.close_btn.centery
        for a_, b_ in (((-k, -k), (k, k)), ((-k, k), (k, -k))):
            pygame.draw.line(scr, sc.text, (cx + a_[0], cy + a_[1]), (cx + b_[0], cy + b_[1]), max(2, int(2 * s)))
        self._draw_tabs(r, x0, y0 + title.get_height() // 2)
        top = y0 + title.get_height() + int(10 * s)
        if self.tab == "memory":
            self._draw_memory(r, pygame.Rect(x0, top, w - 2 * pad, self.rect.bottom - pad - top))
            return
        narrow = w < int(760 * s)
        lw = int(w * (0.42 if narrow else 0.34))
        left = pygame.Rect(x0, top, lw - pad, self.rect.bottom - pad - top)
        right = pygame.Rect(left.right + pad, top, self.rect.right - pad - left.right - pad, left.h)
        pygame.draw.line(scr, sc.separator, (left.right + pad // 2, top), (left.right + pad // 2, left.bottom))

        # --- busca + nova conversa
        field = pygame.Rect(left.x, left.y, left.w, int(40 * s))
        pygame.draw.rect(scr, mix(sc.chip, sc.text, .07), field, border_radius=field.h // 2)
        pygame.draw.rect(scr, sc.palette["blue"], field, max(1, int(2 * s)), border_radius=field.h // 2)
        shown = self.query or "Buscar nas conversas"
        t = r._text("body", r._fit("body", shown, field.w - 2 * pad), sc.text if self.query else sc.text_dim)
        scr.blit(t, (field.x + pad, field.centery - t.get_height() // 2))
        if self.query:
            caret = r._text("body", "|", sc.text)
            scr.blit(caret, (field.x + pad + t.get_width() + 2, field.centery - caret.get_height() // 2))
        self.new_btn = pygame.Rect(left.x, field.bottom + int(8 * s), left.w, int(40 * s))
        self._button(r, self.new_btn, "+  Nova conversa", "new", True)

        # --- lista
        self.list_rect = pygame.Rect(left.x, self.new_btn.bottom + int(10 * s), left.w, left.bottom - self.new_btn.bottom - int(10 * s))
        row_h = int(62 * s)
        self._max_list = max(0.0, len(self._sessions) * row_h - self.list_rect.h)
        self.list_scroll = max(0.0, min(self._max_list, self.list_scroll))
        self.rows = []
        clip = scr.get_clip()
        scr.set_clip(self.list_rect)
        if not self._sessions:
            msg = "Nada encontrado." if self.query else "As conversas aparecem aqui depois da primeira resposta."
            for i, line in enumerate(r._wrap("footnote", msg, self.list_rect.w - pad)):
                scr.blit(r._text("footnote", line, sc.text_dim), (self.list_rect.x, self.list_rect.y + i * r._font("footnote").get_linesize()))
        for i, sess in enumerate(self._sessions):
            row = pygame.Rect(self.list_rect.x, self.list_rect.y + int(i * row_h - self.list_scroll), self.list_rect.w, row_h - int(4 * s))
            if row.bottom < self.list_rect.top or row.top > self.list_rect.bottom:
                continue
            self.rows.append((row, sess))
            if i == self.sel:
                pygame.draw.rect(scr, mix(sc.chip, sc.palette["blue"], .28), row, border_radius=int(14 * s))
            elif self._pressed == ("row", i):
                pygame.draw.rect(scr, mix(sc.chip, sc.text, .10), row, border_radius=int(14 * s))
            name = r._text("headline", r._fit("headline", sess["title"], row.w - 2 * pad), sc.text)
            scr.blit(name, (row.x + pad, row.y + int(8 * s)))
            when = sess["when"]
            stamp = (when[8:10] + "/" + when[5:7] + " " + when[11:16]) if len(when) >= 16 else when
            info = f"{stamp} · {sess['turns']} {'troca' if sess['turns'] == 1 else 'trocas'}" + ("  ·  atual" if sess["id"] == current_id else "")
            scr.blit(r._text("footnote", r._fit("footnote", info, row.w - 2 * pad), sc.text_dim), (row.x + pad, row.y + int(8 * s) + name.get_height()))
        scr.set_clip(clip)

        # --- conversa em balões
        sel = self.selected
        bh = int(40 * s)
        head_h = bh + int(10 * s)
        if sel:
            tt = r._text("headline", r._fit("headline", sel["title"], right.w - 3 * int(120 * s) - pad), sc.text)
            scr.blit(tt, (right.x, right.y + (bh - tt.get_height()) // 2))
            bw = int(112 * s)
            self.delete_btn = pygame.Rect(right.right - bw, right.y, bw, bh)
            self.resume_btn = pygame.Rect(self.delete_btn.x - bw - int(8 * s), right.y, bw, bh)
            self.copy_btn = pygame.Rect(self.resume_btn.x - bw - int(8 * s), right.y, bw, bh)
            self._button(r, self.copy_btn, "Copiar .md", "copy", False)
            self._button(r, self.resume_btn, "Retomar", "resume", True)
            self._button(r, self.delete_btn, "Apagar?" if self.confirm_delete else "Apagar", "delete", False,
                         danger=self.confirm_delete)
        else:
            self.resume_btn = self.delete_btn = self.copy_btn = pygame.Rect(0, 0, 0, 0)
        self.chat_rect = pygame.Rect(right.x, right.y + head_h, right.w, right.h - head_h)
        if not sel:
            msg = r._text("body", "Selecione uma conversa para ver as mensagens.", sc.text_dim)
            scr.blit(msg, msg.get_rect(center=self.chat_rect.center))
            return
        key = (sel["id"], sel["turns"], self.chat_rect.w, round(s, 3))
        if key != self._bubbles_key:
            self._layout_bubbles(r, self._messages(sel["id"]))
            self._bubbles_key = key
        self._max_chat = max(0.0, self._bubbles_h - self.chat_rect.h)
        if self.chat_scroll is not None:
            self.chat_scroll = max(0.0, min(self._max_chat, self.chat_scroll))
        off = self._max_chat if self.chat_scroll is None else self.chat_scroll
        clip = scr.get_clip()
        scr.set_clip(self.chat_rect)
        for who, lines, y, bw_, bh_ in self._bubbles:
            ry = self.chat_rect.y + int(y - off)
            if ry + bh_ < self.chat_rect.top or ry > self.chat_rect.bottom:
                continue
            user = who == "user"
            bx = self.chat_rect.right - bw_ - int(6 * s) if user else self.chat_rect.x
            rect = pygame.Rect(bx, ry, bw_, bh_)
            fill = sc.palette["blue"] if user else mix(sc.chip, sc.text, .10)
            pygame.draw.rect(scr, fill, rect, border_radius=int(18 * s))
            fg = (255, 255, 255) if user or sc.key == "contrast" else sc.text
            ly = rect.y + int(9 * s)
            for line in lines:
                scr.blit(r._text("body", line, fg), (rect.x + int(14 * s), ly))
                ly += r._font("body").get_linesize()
        scr.set_clip(clip)
        if self._max_chat > 0:                                   # trilho fino de rolagem
            tr = pygame.Rect(self.chat_rect.right - int(4 * s), self.chat_rect.y, int(3 * s), self.chat_rect.h)
            th = max(int(24 * s), int(tr.h * self.chat_rect.h / self._bubbles_h))
            ty = tr.y + int((tr.h - th) * (off / self._max_chat))
            pygame.draw.rect(scr, mix(sc.chip, sc.text, .35), (tr.x, ty, tr.w, th), border_radius=tr.w)

    def _layout_bubbles(self, r, msgs: List[Tuple[str, str]]) -> None:
        s = r.s
        max_w = int(self.chat_rect.w * 0.78)
        gap = int(10 * s)
        y, out = 0, []
        for who, text in msgs:
            lines = r._wrap("body", text, max_w - int(28 * s))
            if not lines:
                continue
            tw = max(r._font("body").size(line)[0] for line in lines)
            bw = min(max_w, tw + int(28 * s))
            bh = len(lines) * r._font("body").get_linesize() + int(18 * s)
            out.append((who, lines, y, bw, bh))
            y += bh + gap
        self._bubbles, self._bubbles_h = out, max(0, y - gap)

    def _button(self, r, rect, label, name, primary, danger=False) -> None:
        sc, s, scr = r.scheme, r.s, r.screen
        fill = sc.palette["red"] if danger else sc.palette["blue"] if primary else mix(sc.chip, sc.text, .10)
        if self._pressed == name:
            fill = mix(fill, (0, 0, 0), .15)
        if sc.key == "contrast":
            fill = (0, 0, 0)
        pygame.draw.rect(scr, fill, rect, border_radius=rect.h // 2)
        if sc.key == "contrast":
            pygame.draw.rect(scr, (255, 255, 255), rect, max(2, int(2 * s)), border_radius=rect.h // 2)
        fg = (255, 255, 255) if primary or danger or sc.key == "contrast" else sc.text
        lt = r._text("headline", label, fg)
        scr.blit(lt, lt.get_rect(center=rect.center))
