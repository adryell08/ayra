"""Regressão de alternância involuntária com rosto parado; somente números."""
import math
from unittest.mock import patch

from auxiliary import Observation, ObservedBatch
from gestures import HandReactor, ObjectReactor
from perception import VisualReactor
from robot import Robot
from tracker import Detection, FaceTracker


def face(h=.3, cues=None, conf=.95):
    return Detection(.5-h*.39, .45-h/2, h*.78, h, .5, .45-h*.12, conf, cues or {})


def test_stability(check):
    print("Estabilidade: evidência fraca, liberação e último gatilho")
    reactor = VisualReactor()
    events = []
    for i in range(600):
        score = .7 if i % 6 < 2 else 0.
        events.extend(reactor.update({"tongue_out": score, "smile": score}, i/30))
    check("Ruído de 1–2 quadros de língua/sorriso não alterna expressão", not events)
    for kind in ("tongue_out", "smile"):
        reactor = VisualReactor()
        accepted = reactor.update({kind: .95}, 0)
        for i in range(1, 300):
            accepted.extend(reactor.update({kind: 0. if i % 20 == 0 else .95}, i/30))
        check(f"{kind}: forte imediato; queda isolada não rearma", len(accepted) == 1)
    hands = HandReactor()
    accepted = []
    for i in range(240):
        accepted.extend(hands.update([Observation("hand", .95, .5, .5, .18)], i*.12))
    check("Palma mantida por 28 s cumprimenta só uma vez", [e.kind for e in accepted] == ["palm_hello"])
    weak = HandReactor()
    check("Palma fraca perto não dispara no primeiro quadro", not weak.update([Observation("hand", .5, .5, .5, .4)], 0))
    strong = HandReactor()
    check("Palma forte perto mantém reflexo imediato", strong.update([Observation("hand", .95, .5, .5, .4)], 0)[0].kind == "hand_threat")
    objects = ObjectReactor()
    accepted = []
    for i in range(60):
        obs = [Observation("dog", .4, .5, .5, .3)] if i % 3 < 2 else []
        accepted.extend(objects.update(obs, i*.5))
    check("Objeto fraco intermitente não gera reações", not accepted)
    check("Objeto forte mantém reação imediata", ObjectReactor().update([Observation("dog", .95, .5, .5, .3)], 0)[0].kind == "dog")
    objects = ObjectReactor()
    check("Objeto com score NaN/inf nunca dispara", not objects.update([Observation("dog", math.nan, .5, .5, .3)], 0)
          and not objects.update([Observation("dog", math.inf, .5, .5, .3)], 1))
    tracker = FaceTracker(min_hits=2)
    tracker.update([face(conf=.75)], 0); tracker.update([face(conf=.75)], .033)
    check("Rosto fraco em só dois quadros não gera ID", not tracker.tracks[0].confirmed)
    tracker.update([face(conf=.75)], .066); tracker.update([face(conf=.75)], .1)
    check("Rosto fraco estável confirma sem desabilitar rastreamento", tracker.tracks[0].confirmed)
    robot = Robot(seed=5)
    for i in range(1050):
        robot.step([face(.56)], i/30, 1/30)
    check("Rosto parado perto: surpresa de entrada não repete a cada 10 s",
          sum(e["kind"] == "too_close" for e in robot.trigger_history) <= 1 and robot.last.shown == "neutral")
    robot = Robot(seed=5)
    for i in range(900):
        cues = {"smile_landmarks": .95, "tongue_out": .7 if i % 6 < 2 else 0.}
        robot.step([face(.3 + .008*math.sin(i*1.7), cues)], i/30, 1/30)
    check("Rosto neutro largo + jitter + sinais fracos permanece neutro",
          robot.last.shown == "neutral" and not any(e["kind"] in ("tongue_out", "smile_landmarks", "face_reflex") for e in robot.trigger_history))
    robot.set_feature("tongue", True)
    robot.step([face(.3, {"tongue_out": .95})], 30.1, 1/30)
    trigger = robot.last_trigger
    check("Último gatilho registra tipo, escore e tempo do evento aceito",
          trigger["kind"] == "tongue_out" and trigger["score"] == .95 and trigger["time"] == 30.1)
    check("Histórico de gatilhos é limitado e só numérico/textual", len(robot.trigger_history) <= 32
          and all(isinstance(v, (str, int, float)) for e in robot.trigger_history for v in e.values()))
    from renderer import HudInfo, Renderer
    import pygame
    pygame.init()
    renderer = Renderer(pygame.display.set_mode((640, 480)), (80, 45), __import__('theme').DARK_SCHEME)
    hud = HudInfo(debug=True, last_trigger=trigger, trigger_age=.2)
    with patch.object(renderer, "_text", wraps=renderer._text) as text:
        renderer.draw_hud(robot.last, hud)
    check("D exibe último gatilho com tipo/score/tempo", any("Último gatilho:" in call.args[1]
          and "tongue_out" in call.args[1] for call in text.call_args_list))
