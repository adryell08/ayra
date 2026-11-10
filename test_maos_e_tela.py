"""Testes do nome Ayra, da miniatura ao vivo, da sala do operador no celular e do dedo do meio (OpenCV, sem IA).

Uso: python test_maos_e_tela.py
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import numpy as np
import pygame

RESULTS = []


def check(name, cond):
    RESULTS.append((name, bool(cond)))
    print(f"  [{'OK' if cond else 'FALHOU'}] {name}")


def hand(states, angle=0.0, scale=80.0, center=(320, 300), thumb="c"):
    """21 pontos de uma mão em pé (punho embaixo); 'e' = dedo esticado, 'c' = dobrado. Gira e escala.

    Polegar: 'c' dobrado sobre a palma, 'up' esticado para cima (joinha), 'ok' tocando a ponta do indicador.
    """
    p = np.zeros((21, 2))
    p[1:5] = {"c": [(-.3, -.15), (-.4, -.35), (-.3, -.55), (-.15, -.65)],
              "up": [(-.35, -.3), (-.45, -.75), (-.48, -1.15), (-.5, -1.5)],
              "ok": [(-.3, -.15), (-.45, -.4), (-.45, -.7), (-.33, -1.05)]}[thumb]
    for base, x in {5: -.3, 9: -.1, 13: .1, 17: .28}.items():
        mcp = np.array([x, -1.0])
        p[base] = mcp
        if states[base] == "e":
            for k in range(1, 4):
                p[base + k] = mcp + (0, -.35 * k)
        else:
            p[base + 1:base + 4] = [mcp + (0, -.3), mcp + (0, -.1), mcp + (0, .1)]
    if thumb == "ok":                                      # indicador dobra até encostar no polegar
        p[6], p[7], p[8] = (-.38, -1.3), (-.42, -1.2), (-.36, -1.08)
    c, s = np.cos(angle), np.sin(angle)
    return p @ np.array([[c, s], [-s, c]]) * scale + center


def test_name():
    print("Nome: Ayra")
    import ia
    import assistente_realtime as ar
    check("persona: o nome é Ayra", "Seu nome é Ayra" in ia.PERSONA and "Nuria" not in ia.PERSONA)
    check("instruções da Realtime sem o nome antigo", "Nuria" not in ar.INSTRUCTIONS)
    html = (HERE / "operador_web" / "index.html").read_text(encoding="utf-8")
    check("sala do operador: Ayra", "<title>Ayra - Sala do Operador</title>" in html and "Nuria" not in html)
    texts = [p for p in HERE.glob("*.py") if not p.name.startswith("test_")]
    leftovers = [p.name for p in texts if p.name != "contexto.py" and not p.name.startswith("avaliar_") and re.search(r"\bNuria\b", p.read_text(encoding="utf-8"))]   # contexto.py: moldura interna fixada por test_contexto
    check("nenhum texto visível com 'Nuria' no código", not leftovers)
    import credenciais
    check(".env continua o mesmo (variáveis NURIA_* preservadas)", "NURIA_MODELO_REALTIME" in credenciais.DEFAULTS)


def test_live_thumbnail():
    print("Miniatura ao vivo na coluna")
    import tracker
    src = tracker.CameraSource.__new__(tracker.CameraSource)
    import threading
    src._lock = threading.Lock()
    src._paused = threading.Event()
    src.status = "ok"
    src._preview, src._preview_seq, src._preview_wanted, src._preview_last = None, 0, -1e9, -1e9
    img, seq = src.preview()
    check("câmera só gera miniatura quando a tela pede (pedido registrado)", img is None and src._preview_wanted > 0)
    src._preview, src._preview_seq = np.zeros((240, 320, 3), np.uint8), 5
    src._paused.set()
    check("câmera pausada: sem miniatura", src.preview()[0] is None)
    pygame.init()
    screen = pygame.display.set_mode((1280, 720))
    import theme
    from renderer import Renderer
    from painel_assistente import AssistantPanel
    from assistente import Snapshot
    r = Renderer(screen, (80, 45), theme.SCHEMES["dark"])
    panel = AssistantPanel()
    r.reserve_right = r.menu.reserve_right = panel.reserve(screen, r.s)
    r.resize(screen)
    panel.layout(r)

    class A:
        def __init__(self, snap):
            self.snap = snap

        def snapshot(self):
            return self.snap

        def backend_label(self):
            return "Realtime"

        def can_repeat(self):
            return False
    live = np.zeros((240, 320, 3), np.uint8)
    live[:] = (0, 0, 255)                                  # vermelho (BGR)
    sent = np.zeros((480, 640, 3), np.uint8)
    sent[:] = (255, 0, 0)                                  # azul (BGR)
    panel.live = (live, 1)
    panel.draw(r, A(Snapshot(state="idle")), .1, True)
    shot = pygame.surfarray.array3d(screen)
    region = shot[panel.rect.x:panel.rect.right, panel.rect.y:panel.rect.y + panel.rect.h // 2]
    red = ((region[..., 0] > 200) & (region[..., 2] < 60)).sum()
    check("quadro ao vivo aparece na coluna", red > 20000)
    panel.live = (live, 2)
    panel.draw(r, A(Snapshot(state="speaking", image=sent, image_version=1)), .1, True)
    shot = pygame.surfarray.array3d(screen)
    region = shot[panel.rect.x:panel.rect.right, panel.rect.y:panel.rect.y + panel.rect.h // 2]
    blue = ((region[..., 2] > 200) & (region[..., 0] < 60)).sum()
    red2 = ((region[..., 0] > 200) & (region[..., 2] < 60)).sum()
    check("foto enviada à IA aparece em miniatura no canto (o ao vivo continua)", 2000 < blue < red2)
    panel.live = (None, 3)
    panel.draw(r, A(Snapshot(state="idle", image=sent, image_version=1)), .1, True)
    check("sem câmera ao vivo: volta a mostrar a última foto enviada", panel._live_surf is None)
    main_src = (HERE / "main.py").read_text(encoding="utf-8")
    check("o envio à IA não mudou (uma foto por pergunta)", "capture_still" in (HERE / "assistente.py").read_text(encoding="utf-8")
          or "capture_still" in main_src)


def test_mobile_page():
    print("Sala do operador no celular")
    html = (HERE / "operador_web" / "index.html").read_text(encoding="utf-8")
    check("viewport cobre a tela toda (iPhone com entalhe) e respeita áreas seguras",
          "viewport-fit=cover" in html and "safe-area-inset-bottom" in html and "safe-area-inset-top" in html)
    check("Falar e Interromper ficam na faixa de ícones, ao alcance do polegar (painel v2; o resto está em test_painel)",
          'id="talk"' in html and 'id="stop"' in html and 'id="strip"' in html)
    check("celular: gestos e voz/expressões abrem por cima (openLeft/openRight)", 'id="openLeft"' in html and 'id="openRight"' in html)
    check("celular deitado: vídeo à esquerda, controles à direita", "orientation: landscape" in html)
    check("campos com 16 px (iOS não dá zoom ao digitar)", "font-size: 16px" in html)
    check("botão de falar sem menu de toque longo nem seleção", "-webkit-touch-callout: none" in html
          and 'addEventListener("contextmenu"' in html)
    check("alvos de toque ≥ 44 px (botões, chaves e campos)", "min-height: 44px" in html and "min-height: 52px" in html)


def test_middle_finger():
    print("Gestos vistos pela câmera (OpenCV, sem IA): dedo do meio, joinha, paz e amor, OK")
    from gestures import classify_hand, SignReactor
    check("dedo do meio reconhecido", classify_hand(hand({5: "c", 9: "e", 13: "c", 17: "c"}))[0] == "middle_finger")
    check("também com a mão girada/inclinada", all(classify_hand(hand({5: "c", 9: "e", 13: "c", 17: "c"}, a))[0] == "middle_finger"
                                                 for a in (-.8, -.3, .4, 1.2, 3.1)))
    check("mão aberta não é dedo do meio", classify_hand(hand({5: "e", 9: "e", 13: "e", 17: "e"}))[0] == "open")
    check("punho fechado não é dedo do meio", classify_hand(hand({5: "c", 9: "c", 13: "c", 17: "c"}))[0] == "fist")
    check("apontar (indicador) não é dedo do meio", classify_hand(hand({5: "e", 9: "c", 13: "c", 17: "c"}))[0] != "middle_finger")
    check("joinha reconhecido (polegar para cima)", classify_hand(hand({5: "c", 9: "c", 13: "c", 17: "c"}, thumb="up"))[0] == "thumbs_up")
    check("polegar para baixo não é joinha", classify_hand(hand({5: "c", 9: "c", 13: "c", 17: "c"}, angle=np.pi,
                                                               thumb="up"))[0] != "thumbs_up")
    check("paz e amor reconhecido", classify_hand(hand({5: "e", 9: "e", 13: "c", 17: "c"}))[0] == "peace")
    check("OK reconhecido (polegar encostando no indicador)",
          classify_hand(hand({5: "c", 9: "e", 13: "e", 17: "e"}, thumb="ok"))[0] == "ok_sign")
    from auxiliary import Observation, ObservedBatch
    rx = SignReactor()
    mf = [Observation("middle_finger", .9, .5, .5, .2)]
    check("uma leitura só não basta (evita falso positivo)", rx.update(mf, 0.0) == [])
    ev = rx.update(mf, .15)
    check("duas leituras em 0,8 s confirmam", len(ev) == 1 and ev[0].kind == "middle_finger")
    rx.update(mf, 1.0)
    check("espera 6 s antes de reagir de novo", rx.update(mf, 1.2) == [] and len(rx.update(mf, 7.0) + rx.update(mf, 7.1)) == 1)
    import robot as robot_mod
    rb = robot_mod.Robot((80, 45))
    for i in range(6):
        rb.step(ObservedBatch([], [Observation("hand", .9, .5, .5, .2)] + mf, "", {"hand"}), i * .12, .12)
    check("robô reage sozinho: fica bravo e registra o gatilho", rb.last_trigger and rb.last_trigger["kind"] == "middle_finger"
          and rb.expr.shown_name() == "angry")
    rb2 = robot_mod.Robot((80, 45))
    rb2.set_feature("middle_finger", False)
    for i in range(6):
        rb2.step(ObservedBatch([], mf, "", {"hand"}), i * .12, .12)
    check("Ajustes → Reações → Devolver dedo do meio desliga a reação",
          not (rb2.last_trigger and rb2.last_trigger["kind"] == "middle_finger"))
    rb3 = robot_mod.Robot((80, 45))
    up = [Observation("thumbs_up", .9, .5, .5, .2)]
    for i in range(6):
        rb3.step(ObservedBatch([], [Observation("hand", .9, .5, .5, .2)] + up, "", {"hand"}), i * .12, .12)
    check("joinha visto: ela fica feliz e registra (o app devolve joia no corpo 3D)",
          rb3.last_trigger and rb3.last_trigger["kind"] == "thumbs_up" and rb3.expr.shown_name() == "happy")
    rb4 = robot_mod.Robot((80, 45))
    rb4.set_feature("gesture_reply", False)
    for i in range(6):
        rb4.step(ObservedBatch([], up, "", {"hand"}), i * .12, .12)
    check("Reações → Responder gestos desliga joinha/paz/OK",
          not (rb4.last_trigger and rb4.last_trigger["kind"] == "thumbs_up"))
    main_src = (HERE / "main.py").read_text(encoding="utf-8")
    check("cada gesto visto tem resposta: aceno→tchau, joinha→joia, paz→paz, OK→ok, dedo do meio→dedo do meio",
          all(f'"{a}": "{b}"' in main_src for a, b in (("middle_finger", "dedo_do_meio"), ("thumbs_up", "joia"),
                                                       ("peace", "paz"), ("ok_sign", "ok"), ("wave", "tchau"))))
    import preparar_modelos as pm
    check("modelo de pontos da mão (OpenCV Zoo) com hash conferido",
          pm.MODELS["handpose"][1] == "db0898ae717b76b075d9bf563af315b29562e11f8df5027a1ef07b02bef6d81c")
    main_src = (HERE / "main.py").read_text(encoding="utf-8")
    check("com o corpo 3D ligado, a Ayra devolve o gesto", "assistant.request_gesture(reply)" in main_src)
    import auxiliary
    if auxiliary.valid_model("handpose") and auxiliary.valid_model("hands"):
        det = auxiliary.HandGestureDetector()
        out = det.infer(np.zeros((480, 640, 3), np.uint8))
        check("detector real carrega e não acusa gesto numa imagem vazia", not any(o.kind == "middle_finger" for o in out))
    else:
        print("  [PULADO] modelos de mão não baixados aqui (preparar_modelos.bat baixa no PC)")


if __name__ == "__main__":
    test_name()
    test_live_thumbnail()
    test_mobile_page()
    test_middle_finger()
    ok = sum(1 for _, v in RESULTS if v)
    print(f"\n{ok}/{len(RESULTS)} verificações OK")
    sys.exit(0 if ok == len(RESULTS) else 1)
