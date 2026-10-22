"""Menu de opções com mouse, foco de teclado e superfícies discretas de vidro."""
from __future__ import annotations

import pygame
from math import ceil

import theme


class OptionsMenu:
    def __init__(self):
        self.open = False
        self.focus = 0
        self.pressed = None
        self.view = "root"
        self.offset = 0
        self.button = pygame.Rect(0, 0, 0, 0)
        self.card = pygame.Rect(0, 0, 0, 0)
        self.rows = []
        self.dock = []
        self.dock_visible = True
        self.last_input = pygame.time.get_ticks()
        self.close = pygame.Rect(0, 0, 0, 0)
        self.headers = []
        self.block_dock = False
        self.dock_focus = None
        self.hover = None
        self.back = pygame.Rect(0, 0, 0, 0)

    def items(self, hud, scheme):
        if self.view in ("features", "face"):
            from preferences import DEFAULT_FEATURES, LABELS, FACE_KEYS, REMOVED_FEATURES
            keys = [k for k in DEFAULT_FEATURES if k not in REMOVED_FEATURES and (k in FACE_KEYS) == (self.view == "face")]
            return [("feature:" + key, LABELS[key], "Ativado" if hud.features.get(key, True) else "Desativado",
                     "on" if hud.features.get(key, True) else "off") for key in keys]
        if self.view == "sims":
            from actions import ACTIONS
            running = set(getattr(hud, "active_actions", ()) or ())
            return [("sim:" + name, info["label"], "Em andamento · tocar para parar" if name in running else info["description"][:46],
                     "on" if name in running else "off") for name, info in ACTIONS.items()]
        if self.view == "api":
            a = getattr(hud, "api", {}) or {}
            has_key = a.get("configured", False)
            return [
                ("api:key", "Chave da OpenAI", (f"{a.get('masked', '')} · tocar para trocar" if has_key
                                                else "Não configurada · tocar para colar"), ""),
                ("api:test", "Testar conexão", a.get("test", "Tocar para testar a Realtime"), ""),
                ("api:remove", "Remover chave", "Toque de novo para confirmar" if a.get("confirm_remove")
                 else "Apaga a chave do arquivo .env", ""),
                ("api:model", "Modelo Realtime", a.get("model", "") + " · tocar para trocar", ""),
                ("api:voice", "Voz", a.get("voice", "") + " · tocar para trocar", ""),
                ("api:robot", "Voz robótica", "IA e operador com a mesma voz", "on" if a.get("robot_voice") else "off"),
            ]
        if self.view == "remote":
            r = getattr(hud, "remote", {}) or {}
            on = r.get("enabled", False)
            return [
                ("remote:enabled", "Sala do operador", (r.get("status") or "") if on else "Desligada", "on" if on else "off"),
                ("remote:url", "Endereço (no navegador do operador)", r.get("url", "—") if on else "Ligue a sala primeiro", ""),
                ("remote:pin", "PIN", (r.get("pin", "") + " · tocar para gerar outro") if on else "—", ""),
                ("remote:kick", "Desconectar operador", r.get("operator") or "Nenhum conectado", ""),
                ("remote:badge", "Aviso na tela", "Barra \"Operador conectado · nome · modo\"",
                 "on" if r.get("badge", True) else "off"),
            ]
        if self.view == "arduino":
            a = getattr(hud, "arduino", {}) or {}
            on = a.get("enabled", False)
            from arduino_led import SIZE_LABELS
            return [
                ("arduino:enabled", "Painel LED 32×8", (a.get("status", "") or "Desligado") if on else "Desligado",
                 "on" if on else "off"),
                ("arduino:port", "Porta USB", a.get("port_label", "Automática") + " · tocar para trocar", ""),
                ("arduino:brightness", "Brilho", f"{a.get('brightness', 2)} de 15 · tocar para mudar", ""),
                ("arduino:size", "Tamanho dos olhos", SIZE_LABELS.get(a.get("size", "medio"), "Médios"), ""),
                ("arduino:test", "Padrão de teste", "Ponto em cima à esquerda e seta → para a direita",
                 "on" if a.get("test") else "off"),
                ("arduino:order", "Entrada dos fios", "À direita (vista de frente)" if a.get("input_right", True)
                 else "À esquerda (vista de frente)", ""),
                ("arduino:rotate", "Girar cada módulo", f"{a.get('rotation', 0)}°", ""),
                ("arduino:mirror", "Espelhar módulos", "Sim" if a.get("mirror") else "Não", ""),
                ("arduino:flip", "Painel de cabeça para baixo", "Sim" if a.get("upside_down") else "Não", ""),
            ]
        if self.view == "cameras":
            return [("camera:auto", "Seleção automática", "Preferir webcam física RGB", "Auto")] + [
                (f"camera:{index}", name[:34], f"Índice {index}", str(index))
                for index, name in hud.camera_choices
            ]
        paused = hud.camera_status == "paused"
        return [
            ("pause", ("Retomar câmera" if paused else "Pausar câmera") if not hud.simulated else "Pausar simulação", "Processamento local · sem gravar imagens", "P"),
            ("cameras", "Escolher câmera", (hud.camera_name[:30] + f" · #{hud.camera_index}") if hud.camera_name else "Seleção automática", ""),
            ("theme", "Aparência", scheme.label + " · alternar", "C"),
            ("motion", "Reduzir movimento", "Ligado" if hud.reduce_motion else "Desligado", "M"),
            ("features", "Reações", "Mãos, língua, sorriso e mais", ""),
            ("face", "Rosto", "Olhos redondos, contorno e boca", ""),
            ("sims", "Simulações", "Falar, chorar e mais", ""),
            ("remote", "Controle remoto", "Sala do operador · " + ((getattr(hud, "remote", {}) or {}).get("status") or "desligada"), ""),
            ("api", "API da OpenAI", "Chave " + ((getattr(hud, "api", {}) or {}).get("masked") or "não configurada"), ""),
            ("arduino", "Arduino", "Painel LED 32×8 · " + ((getattr(hud, "arduino", {}) or {}).get("status") or "desligado"), ""),
            ("fullscreen", "Tela cheia", "Sair da tela cheia" if hud.fullscreen else "Entrar", "F11"),
            ("help", "Ajuda e atalhos", "Mouse e teclado", "F1"),
            ("debug", "Diagnóstico", "Visível" if hud.debug else "Oculto", "D"),
            ("quit", "Sair", "Encerrar o aplicativo", "Q"),
        ]

    def _section(self, action):
        if action.startswith("sim:"):
            return "SIMULAÇÕES"
        if action.startswith("remote:"):
            return "SALA DO OPERADOR"
        if action.startswith("api:"):
            return "CONEXÃO" if action in ("api:key", "api:test", "api:remove") else "VOZ E MODELO"
        if action.startswith("arduino:"):
            key = action.split(":")[1]
            return "PAINEL" if key in ("enabled", "port", "brightness", "size", "test") else "MONTAGEM"
        if action.startswith("feature:"):
            key = action.split(":")[1]
            return "PERCEPÇÃO" if key in ("hands", "tongue", "smile", "objects") else "ROSTO" if key in ("round_eyes", "outline", "mouth") else "EXPRESSÃO"
        if action.startswith("camera:") or action in ("pause", "cameras"):
            return "CÂMERA"
        if action in ("theme", "motion", "fullscreen"):
            return "APARÊNCIA"
        if action in ("features", "face", "sims") or action.startswith("sim:"):
            return "PERSONALIDADE"
        if action in ("arduino", "api", "remote"):
            return "HARDWARE" if action == "arduino" else "INTELIGÊNCIA"
        return "APLICATIVO"

    def layout(self, screen, scale, count=8, items=None):
        pad = int(theme.PADDING * scale)
        target, gap = int(48 * scale), int(8 * scale)
        dock_w = 4 * target + 3 * gap + 2 * int(8 * scale)
        face_w = screen.get_width() - getattr(self, "reserve_right", 0)   # dock centrado sob o rosto
        dock_x = (face_w - dock_w) // 2 + int(8 * scale)
        dock_y = screen.get_height() - pad - target - int(8 * scale)
        self.dock = [pygame.Rect(dock_x + i * (target + gap), dock_y, target, target) for i in range(4)]
        self.button = self.dock[-1]
        compact = screen.get_height() <= 600
        width = screen.get_width() - 2 * pad if compact else min(int(480 * scale), screen.get_width() - 2 * pad)
        row_h = int((52 if compact else 64) * scale)
        available = self.dock[0].top - int(12 * scale) - int(80 * scale)
        self.focus = min(self.focus, count - 1)
        self.offset = max(0, min(self.offset, count - 1))
        if self.focus < self.offset:
            self.offset = self.focus
        self.card = pygame.Rect(screen.get_width() - pad - width, int(80 * scale), width, available)
        while True:
            self.rows, self.headers = [], []
            y, previous = self.card.y + int((58 if compact else 68) * scale), None
            for index in range(self.offset, count):
                group = self._section(items[index][0]) if items else "AJUSTES"
                label_h = int((24 if compact else 28) * scale) if group != previous else 0
                if y + label_h + row_h + int(48 * scale) > self.card.bottom and self.rows:
                    break
                if label_h:
                    self.headers.append((group, pygame.Rect(self.card.x + 2 * pad, y, width - 4 * pad, label_h)))
                    y += label_h
                self.rows.append(pygame.Rect(self.card.x + pad, y, width - 2 * pad, row_h))
                y += row_h
                previous = group
            if self.focus < self.offset + len(self.rows):
                break
            self.offset += 1
        self.card.h = available if len(self.rows) < count else min(available, y - self.card.y + int(48 * scale))
        target = ceil(44 * scale)
        self.close = pygame.Rect(self.card.right - pad - target, self.card.y + pad, target, target)
        self.back = pygame.Rect(self.card.x + pad, self.card.y + pad, target, target)

    def activate(self, action):
        if action in ("cameras", "features", "face", "sims", "arduino", "api", "remote", "back"):
            self.view = action if action != "back" else "root"
            self.focus, self.offset, self.pressed = 0, 0, None
            return True, None
        if action.startswith(("feature:", "sim:", "arduino:", "api:", "remote:")):
            self.pressed = None
            return True, action
        self.open = False
        return True, action

    def handle_event(self, event, items):
        """Devolve (consumido, ação); clicar fora fecha sem atingir a simulação."""
        count = len(items)
        if event.type in (pygame.KEYDOWN, pygame.MOUSEMOTION, pygame.MOUSEBUTTONDOWN, pygame.MOUSEWHEEL):
            self.last_input = pygame.time.get_ticks()
            self.dock_visible = True
        if event.type == pygame.KEYDOWN:
            if not self.open and not self.block_dock:
                if event.key == pygame.K_F6:
                    self.dock_focus = 0
                    return True, None
                if self.dock_focus is not None:
                    if event.key in (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_TAB):
                        self.dock_focus = (self.dock_focus + (-1 if event.key == pygame.K_LEFT else 1)) % 4
                        return True, None
                    if event.key in (pygame.K_RETURN, pygame.K_SPACE):
                        action = ("pause", "theme", "motion", "menu")[self.dock_focus]
                        self.dock_focus = None
                        if action == "menu":
                            self.open = True
                            self.view, self.focus, self.offset = "root", 0, 0
                            return True, None
                        return True, action
                    if event.key == pygame.K_ESCAPE:
                        self.dock_focus = None
                        return True, None
            if event.key == pygame.K_F10:
                self.open = not self.open
                self.view = "root"
                self.focus = 0
                self.offset = 0
                self.pressed = None
                return True, None
            if self.open:
                if event.key == pygame.K_ESCAPE:
                    if self.view != "root":
                        return self.activate("back")
                    self.open = False
                    self.pressed = None
                    return True, None
                if event.key in (pygame.K_UP, pygame.K_DOWN, pygame.K_TAB, pygame.K_HOME, pygame.K_END):
                    if event.key == pygame.K_HOME:
                        self.focus = 0
                    elif event.key == pygame.K_END:
                        self.focus = count - 1
                    else:
                        back = event.key == pygame.K_UP or (event.key == pygame.K_TAB
                                                           and getattr(event, "mod", 0) & pygame.KMOD_SHIFT)
                        self.focus = (self.focus + (-1 if back else 1)) % count
                    return True, None
                if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                    return self.activate(items[self.focus][0])
        if event.type == pygame.MOUSEMOTION:
            self.hover = next((i for i, r in enumerate(self.dock) if r.collidepoint(event.pos)), None)
            if self.open:
                for i, row in enumerate(self.rows):
                    if row.collidepoint(event.pos):
                        self.focus = i + self.offset
                return True, None
            return not self.block_dock and self.hover is not None, None
        if event.type == pygame.MOUSEWHEEL and self.open:
            self.focus = (self.focus - event.y) % count
            return True, None
        if event.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
            dock_hit = next((action for action, rect in zip(("pause", "theme", "motion", "menu"), self.dock)
                             if self.dock_visible and not self.block_dock and rect.collidepoint(event.pos)), None)
            inside_button = dock_hit is not None
            if event.button != 1:
                return self.open or inside_button, None
            hit = dock_hit
            if self.open and self.close.collidepoint(event.pos):
                hit = "close"
            elif self.open and self.view != "root" and self.back.collidepoint(event.pos):
                hit = "back"
            if self.open:
                for i, row in enumerate(self.rows):
                    if row.collidepoint(event.pos):
                        hit = items[i + self.offset][0]
                        self.focus = i + self.offset
            if event.type == pygame.MOUSEBUTTONDOWN:
                self.pressed = hit
                if self.open and hit is None and not self.card.collidepoint(event.pos):
                    self.open = False
                    return True, None
                return self.open or inside_button, None
            previous, self.pressed = self.pressed, None
            if hit is not None and hit == previous:
                if hit == "menu":
                    self.open = not self.open
                    self.view, self.focus, self.offset = "root", 0, 0
                    return True, None
                if hit == "close":
                    self.open = False
                    return True, None
                return self.activate(hit)
            return self.open or inside_button, None
        return False, None

    def draw(self, renderer, hud):
        sc, scale = renderer.scheme, renderer.s
        items = self.items(hud, sc)
        self.layout(renderer.screen, scale, len(items), items)
        pad = int(theme.PADDING * scale)
        self.dock_visible = not hud.show_help and not hud.debug and (self.open or hud.hint_age < theme.HINT_SECONDS or pygame.time.get_ticks() - self.last_input < 4500)
        if self.dock_visible:
            dock = self.dock[0].union(self.dock[-1]).inflate(int(16 * scale), int(16 * scale))
            renderer._glass(dock, int(28 * scale), blur=True)
            for i, (name, rect) in enumerate(zip(("camera", "theme", "motion", "settings"), self.dock)):
                if name == "settings" and self.open:
                    pygame.draw.rect(renderer.screen, theme.mix(sc.chip, sc.text, .08), rect, border_radius=int(18 * scale))
                renderer._icon(name, rect, sc.text)
                if i == self.dock_focus:
                    pygame.draw.rect(renderer.screen, sc.text, rect, max(1, int(scale)), border_radius=int(18 * scale))
            active = self.dock_focus if self.dock_focus is not None else self.hover
            if active is not None:
                text = ("Câmera · P", "Aparência · C", "Movimento · M", "Ajustes · F10")[active]
                image = renderer._text("caption", text, sc.text)
                tooltip = image.get_rect(centerx=self.dock[active].centerx, bottom=dock.top - int(8 * scale)).inflate(2 * pad, pad)
                renderer._glass(tooltip, int(16 * scale))
                renderer.screen.blit(image, image.get_rect(center=tooltip.center))
        if not self.open:
            return
        # Escurece apenas a arte; o indicador de privacidade permanece intacto.
        shade = pygame.Surface((renderer.screen.get_width(), renderer.screen.get_height() - int(72 * scale)), pygame.SRCALPHA)
        shade.fill((0, 0, 0, 42 if sc.key != "light" else 20))
        renderer.screen.blit(shade, (0, int(72 * scale)))
        renderer._glass(self.card, int(24 * scale), blur=True)
        title = renderer._text("title", "Ajustes", sc.text)
        title_x = self.card.x + 2 * pad
        if self.view != "root":
            x, y = self.back.center
            k = int(5 * scale)
            pygame.draw.lines(renderer.screen, sc.text, False, [(x+k,y-k), (x-k,y), (x+k,y+k)], max(2, int(2*scale)))
            title_x = self.back.right + int(8 * scale)
        renderer.screen.blit(title, (title_x, self.card.y + pad))
        renderer._close_button(self.close)
        for label, header in self.headers:
            renderer.screen.blit(renderer._text("section", label, sc.text_dim), (header.x, header.y + int(4 * scale)))
        groups = []
        previous = None
        for item, rect in zip(items[self.offset:], self.rows):
            name = self._section(item[0])
            if name != previous:
                groups.append(rect.copy())
            else:
                groups[-1].union_ip(rect)
            previous = name
        for group in groups:
            # O mesmo desfoque atravessa cartões e intervalos, sem manchas nas frestas.
            fill = pygame.Surface(group.size, pygame.SRCALPHA)
            pygame.draw.rect(fill, (*sc.text, 9), fill.get_rect(), border_radius=int(20 * scale))
            renderer.screen.blit(fill, group)
        for i, ((action, name, state, shortcut), rect) in enumerate(zip(items[self.offset:], self.rows)):
            if i and self._section(action) == self._section(items[self.offset + i - 1][0]) and action != "quit":
                pygame.draw.line(renderer.screen, theme.mix(sc.chip, sc.text, .12), (rect.x + pad, rect.y), (rect.right - pad, rect.y))
            if i + self.offset == self.focus:
                pygame.draw.rect(renderer.screen, theme.mix(sc.chip, sc.text, 0.08), rect,
                                 border_radius=int(20 * scale))
                pygame.draw.rect(renderer.screen, sc.text, rect, width=max(1, int(scale)),
                                 border_radius=int(20 * scale))
            toggle = action.startswith(("feature:", "sim:")) or action in ("arduino:enabled", "arduino:test", "api:robot",
                                                                         "remote:enabled")
            key = renderer._text("caption", "" if toggle else shortcut, sc.text_dim)
            available = rect.w - 3 * pad - max(key.get_width(), int((48 if toggle else 28) * scale))
            name_img = renderer._text("body", renderer._fit("body", name, available), sc.palette["red"] if action == "quit" else sc.text)
            state_img = renderer._text("subheadline", renderer._fit("subheadline", state, available), sc.text_dim)
            x = rect.x + pad
            y = rect.y + max(2, (rect.h - name_img.get_height() - state_img.get_height()) // 2)
            renderer.screen.blit(name_img, (x, y))
            renderer.screen.blit(state_img, (x, y + name_img.get_height()))
            renderer.screen.blit(key, (rect.right - pad - key.get_width(), rect.centery - key.get_height() // 2))
            if toggle:
                switch = pygame.Rect(rect.right - pad - int(48 * scale), rect.centery - int(14 * scale),
                                     int(48 * scale), int(28 * scale))
                on = shortcut == "on"
                color = sc.palette["green"] if on else theme.mix(sc.chip, sc.text, .18)
                pygame.draw.rect(renderer.screen, color, switch, border_radius=switch.h // 2)
                radius = int(11 * scale)
                cx = switch.right - int(14 * scale) if on else switch.left + int(14 * scale)
                pygame.draw.circle(renderer.screen, (255, 255, 255), (cx, switch.centery), radius)
        footer = f"{self.focus + 1} de {len(items)} · Role para ver mais" if len(self.rows) < len(items) else "Preferências salvas neste dispositivo"
        image = renderer._text("caption", footer, sc.text_dim)
        renderer.screen.blit(image, image.get_rect(left=self.card.x + 2 * pad, centery=self.card.bottom - int(24 * scale)))
        if len(self.rows) < len(items):
            viewport = pygame.Rect(self.card.right - int(8 * scale), self.rows[0].y,
                                   int(3 * scale), self.rows[-1].bottom - self.rows[0].y)
            renderer._scrollbar(viewport, int(viewport.h * len(items) / len(self.rows)),
                                int(viewport.h * self.offset / len(self.rows)))
