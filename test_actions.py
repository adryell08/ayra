"""Simulações (falar/chorar): catálogo, determinismo e API para a IA; sem câmera."""
import numpy as np

from actions import ACTIONS
from face import render_field
from expressions import EXPRESSIONS
from robot import Robot
from tracker import SimulatedSource


def test_actions(check):
    print("Simulações: falar e chorar")
    check("Catálogo expõe falar e chorar com descrição", {"speak", "cry"} <= set(ACTIONS)
          and all(ACTIONS[n]["label"] and ACTIONS[n]["description"] for n in ACTIONS))

    def run(seed=3, level=None):
        robot, src = Robot(seed=seed), SimulatedSource(seed=seed)
        robot.features["mouth"] = True
        trace = []
        robot.step(src.poll(1 / 60), 0.0, 1 / 60)
        robot.play("speak"); robot.play("cry")
        for i in range(1, 400):
            if level is not None and i > 100:
                robot.set_speech_level(level)
            robot.step(src.poll(1 / 60), i / 60, 1 / 60)
            trace.append((round(robot.actions._talk, 4), len(robot.actions._tears)))
        return robot, trace

    robot, trace = run()
    check("Falar abre e fecha a boca (variação ampla)", max(t for t, _ in trace) > .5 and min(t for t, _ in trace[60:]) < .2)
    check("Chorar gera lágrimas e a expressão exibida é triste", max(n for _, n in trace) >= 2 and robot.last.shown == "sad")
    check("Simulações são determinísticas por seed", trace == run()[1])
    check("Nome desconhecido é recusado e stop encerra", not robot.play("voar") and (robot.stop("all") or robot.actions.active() == []))
    _, loud = run(level=1.0)
    check("Nível de fala externo (IA) sobrepõe a simulação", min(t for t, _ in loud[140:200]) > .85)
    pose = np.array(EXPRESSIONS["neutral"], dtype=np.float32)
    closed, _ = render_field(80, 45, pose, (1, 1), (0, 0), 1.0, None, {"mouth": True}, "neutral", {"talk": 0.0})
    opened, _ = render_field(80, 45, pose, (1, 1), (0, 0), 1.0, None, {"mouth": True}, "neutral", {"talk": 1.0})
    check("Boca aberta acende mais LEDs que a fechada", opened.sum() > closed.sum() + 8)
    plain, _ = render_field(80, 45, pose, (1, 1), (0, 0), 1.0, None, {}, "neutral", {})
    tear, _ = render_field(80, 45, pose, (1, 1), (0, 0), 1.0, None, {}, "neutral", {"tears": [(-1, .4, 1.2)]})
    check("Lágrima acende LEDs abaixo do olho", tear.sum() > plain.sum() + 2)
