"""R1: regressões de layout e interação, sempre com cena numérica simulada."""
from unittest.mock import patch

import pygame

import theme
from renderer import HudInfo, Renderer
from robot import Robot


def test_interface(check):
    print("R1: interface discreta, rolagem e fechamento por mouse")
    pygame.init()
    frame = Robot(seed=5).step([], 0, 1 / 60)
    bounds, targets, scrolls, closed, content = True, True, True, True, True
    for size in ((640, 480), (1280, 720), (1920, 1080)):
        for scheme in theme.SCHEMES.values():
            screen = pygame.display.set_mode(size)
            renderer = Renderer(screen, (80, 45), scheme)
            hud = HudInfo(debug=True, camera_status="error", camera_name="Integrated Webcam " * 8,
                          camera_note="Câmera em uso por outro app. Feche o OBS e tente novamente.",
                          perception_modules={name: dict(loaded=False, inference_ms=None, score=0,
                                                         error="Falha/" * 80, label="aguardando")
                                              for name in ("hands", "objects", "smile", "tongue")})
            renderer.draw_panel(frame, 1 / 60, True)
            rectangles = []
            chip = renderer._chip
            def capture(*args, **kwargs):
                rect = chip(*args, **kwargs)
                rectangles.append(rect)
                return rect
            with patch.object(renderer, "_chip", side_effect=capture):
                renderer.draw_hud(frame, hud)
            bounds &= all(screen.get_rect().contains(rect) for rect in rectangles)
            bounds &= screen.get_rect().contains(renderer.debug_rect)
            bounds &= not rectangles[0].colliderect(renderer.menu.button)
            targets &= min(renderer.debug_close.size) >= int(44 * renderer.s)
            before = hud.debug_scroll
            renderer.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, y=-10000), hud)
            scrolls &= hud.debug_scroll == renderer._debug_max_scroll and hud.debug_scroll > before
            renderer.draw_panel(frame, 1 / 60, True)
            with patch.object(renderer, "_text", wraps=renderer._text) as rendered:
                renderer.draw_hud(frame, hud)
            content &= any("Língua" in call.args[1] for call in rendered.call_args_list)
            for event in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
                consumed, _ = renderer.handle_event(pygame.event.Event(event, button=1,
                                                    pos=renderer.debug_close.center), hud)
            closed &= consumed and not hud.debug
            hud.show_help = True
            renderer.draw_hud(frame, hud)
            bounds &= screen.get_rect().contains(renderer.help_close)
            targets &= min(renderer.help_close.size) >= int(44 * renderer.s)
            renderer.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, y=-10000), hud)
            scrolls &= hud.help_scroll == renderer._help_max_scroll > 0
            for event in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
                consumed, _ = renderer.handle_event(pygame.event.Event(event, button=1,
                                                    pos=renderer.help_close.center), hud)
            closed &= consumed and not hud.show_help
    check("R1: HUD/diagnóstico e câmera longa cabem e não cobrem Menu", bounds)
    check("R1: fechar ajuda/diagnóstico tem alvo de 44 pt", targets)
    check("R1: roda alcança o fim da ajuda e dos erros do diagnóstico", scrolls)
    check("R1: último módulo permanece disponível no diagnóstico", content)
    check("R1: mouse fecha ajuda/diagnóstico sem vazar à simulação", closed)
    screen = pygame.display.set_mode((640, 480))
    renderer = Renderer(screen, (80, 45), theme.DARK_SCHEME)
    renderer.draw_panel(frame, 1 / 60, True)
    with patch.object(renderer, "_minimap", wraps=renderer._minimap) as minimap, \
         patch.object(renderer, "_debug", wraps=renderer._debug) as debug:
        renderer.draw_hud(frame, HudInfo(hint_age=99))
    check("R1: padrão mínimo não desenha mapa nem diagnóstico", minimap.call_count == debug.call_count == 0
          and HudInfo().hud_mode == "minimal")
    renderer.menu.open = True
    renderer.menu.focus = 7
    renderer.menu.draw(renderer, HudInfo())
    check("R1: último ajuste entra na área visível com foco de teclado",
          renderer.menu.offset <= 7 < renderer.menu.offset + len(renderer.menu.rows))
    no_overlap, quiet, limits, glass_contrast = True, True, True, True
    for scheme in theme.SCHEMES.values():
        renderer = Renderer(screen, (80, 45), scheme)
        renderer.draw_panel(frame, 1/60, True)
        hud = HudInfo(show_help=True)
        renderer.draw_hud(frame, hud)
        hud.help_scroll = renderer._help_max_scroll
        renderer.draw_panel(frame, 1/60, True)
        renderer.draw_hud(frame, hud)
        no_overlap &= renderer.help_viewport.bottom + 8 <= renderer.help_footer.top and not renderer.menu.dock_visible
        pixels = pygame.surfarray.array3d(renderer.background)
        import numpy as np
        quiet &= np.abs(pixels.astype(float) - np.array(scheme.bg)).max() <= 3
        renderer.menu.open = True
        renderer.menu.focus = 0
        renderer.menu.draw(renderer, HudInfo())
        limits &= len(renderer.menu.rows) >= 3 and renderer.menu.card.bottom <= renderer.menu.dock[0].top - 12
        alpha = 255 if scheme.key == "contrast" else (233 if scheme.key == "light" else 238)
        for behind in ((0,0,0), (255,255,255)):
            back = theme.mix(behind, scheme.chip, alpha / 255)
            glass_contrast &= all(theme.contrast(text, back) >= 4.5 for text in (scheme.text, scheme.text_dim))
    check("V2: ajuda no fim não invade rodapé; dock oculto não cobre o texto", no_overlap)
    check("V2: fundo sem moldura e grade apagada quase invisível", quiet)
    check("V2: folha pequena tem 3 linhas e respeita o dock", limits)
    check("V2: vidro composto mantém texto >=4,5:1; mínimo 13 pt", glass_contrast and min(v[0] for v in theme.TEXT_ROLES.values()) >= 13)
    renderer.menu.open = False
    hud = HudInfo(show_help=True)
    for kind in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
        _, action = renderer.handle_event(pygame.event.Event(kind, button=1, pos=renderer.menu.dock[0].center), hud)
    check("V2: dock oculto sob ajuda não ativa câmera por clique", action is None)
    renderer.menu.block_dock = False
    renderer.menu.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F6), [])
    _, action = renderer.menu.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN), [])
    check("V2: teclado alcança dock e ativa ação com Enter", action == "pause")
    renderer = Renderer(pygame.display.set_mode((1440, 900)), (80,45), theme.DARK_SCHEME)
    renderer.menu.open, renderer.menu.view = True, "features"
    renderer.menu.draw(renderer, HudInfo())
    check("V2: folha grande mostra cinco interruptores sem linha oculta desnecessária",
          len(renderer.menu.rows) == len(renderer.menu.items(HudInfo(), theme.DARK_SCHEME)) == 5
          and min(renderer.menu.back.size) >= 44 * renderer.s)
    alpha_mask = pygame.surfarray.array_alpha(renderer.mask)
    check("Nitidez: máscara deixa vãos visíveis, núcleo opaco e borda antialiasada",
          alpha_mask.min() <= 26 and alpha_mask.max() == 255
          and ((alpha_mask > 26) & (alpha_mask < 255)).any())
    from face import render_field
    from expressions import EXPRESSIONS
    _, glint = render_field(80, 45, EXPRESSIONS["neutral"], (1, 1), (0, 0))
    check("Nitidez: catchlight compacto tem núcleo branco e vizinhos legíveis",
          glint.max() == 1 and (glint > 0).sum() <= 32 and (glint > .1).sum() >= 6)
