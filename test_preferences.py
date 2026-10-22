"""Preferências, descarregamento e interruptores; sem câmera física."""
import uuid
from pathlib import Path
from unittest.mock import patch

import preferences
from auxiliary import AuxiliaryDetector
from robot import Robot
from tracker import Detection, SimulatedSource


def test_preferences(check):
    print("Ajustes: persistência, flags e descarregamento")
    folder = Path(__file__).parent / ("tmp-preferences-" + uuid.uuid4().hex)
    folder.mkdir()
    path = folder / "config.local.json"
    values = dict(preferences.DEFAULT_FEATURES, hands=False, smile=False)
    try:
        check("Config ausente: reações ligadas, língua removida, boca ligada e olhos redondos/contorno desligados", all(v for k, v in preferences.load(path).items() if k not in preferences.FACE_KEYS and k not in preferences.REMOVED_FEATURES) and not preferences.load(path)["tongue"] and preferences.load(path)["mouth"] and not preferences.load(path)["round_eyes"] and not preferences.load(path)["outline"])
        preferences.save(values, path)
        check("Config local conserva desligados entre execuções", preferences.load(path) == values)
        path.write_text('{"hands":"false","tongue":false,"unknown":true}', encoding="utf-8")
        loaded = preferences.load(path)
        check("Config ignora tipos/chaves inválidos", loaded["hands"] and not loaded["tongue"] and "unknown" not in loaded)
    finally:
        path.unlink(missing_ok=True)
        folder.rmdir()
    import main
    args = main.parse_args(["--no-hands", "--no-tongue", "--no-smile", "--no-reflex", "--no-auto-blink"])
    check("Flags explícitas sobrescrevem preferências", all(args.feature_overrides[k] is False
          for k in ("hands", "tongue", "smile", "reflex", "auto_blink")))
    class Base:
        name = "fake"
        def detect(self, image): return []
    class Model:
        def __init__(self, **kwargs): pass
        def infer(self, image): return []
    with patch("auxiliary.valid_model", return_value=True), patch("auxiliary.PalmDetector", Model), \
         patch("auxiliary.SmileDetector", Model), patch("auxiliary.ObjectDetector", Model):
        detector = AuxiliaryDetector(Base())
        workers = dict(detector.workers)
        for name in ("hands", "objects", "smile", "tongue"):
            detector.set_feature(name, False)
        status = detector.module_statuses()
        check("Desligar remove workers e libera referências dos modelos", not detector.workers
              and all(not w._thread.is_alive() and not w.detectors for w in workers.values()))
        check("D distingue desligado de modelo com erro", all(not row["enabled"] and not row["loaded"]
              and not row["error"] and row["label"] == "Desligado" for row in status.values()))
        detector.set_feature("hands", True)
        check("Religar cria um worker novo", "hands" in detector.workers and detector.workers["hands"] is not workers["hands"])
        detector.close()
    robot, source = Robot(seed=7), SimulatedSource(seed=7)
    for name in ("tongue", "smile", "hands", "objects", "reflex", "auto_blink"):
        robot.set_feature(name, False)
    for i in range(360):
        source.set_cue("tongue_out", 1.); source.set_cue("smile", 1.)
        if i == 60: source.gesture("dog")
        frame = robot.step(source.poll(1/60), i/60, 1/60)
    check("Desligados ignoram sinais tardios/fortes na simulação", frame.shown == "neutral"
          and not any(e["kind"] in ("tongue_out", "smile", "dog") for e in robot.trigger_history))
    check("Piscadas automáticas desligadas não fecham os olhos", robot.expr.blink == (1., 1.))
    check("Reflexo desligado bloqueia API; piscar manual continua", not robot.blink(reflex=True) and robot.blink())
    robot.set_feature("tongue", True)
    for i in range(30):
        frame = robot.step([Detection(.38,.3,.24,.3,.5,.4,.99,{"tongue_out":1.})], 7+i/30, 1/30)
    check("Religar reação retoma sem reiniciar o aplicativo", frame.shown == "angry")
    from renderer import HudInfo
    from menu import OptionsMenu
    import theme
    menu = OptionsMenu(); menu.open=True; menu.view="features"
    items = menu.items(HudInfo(features=values), theme.DARK_SCHEME)
    consumed, action = menu.activate(items[0][0])
    check("Interruptor permanece na folha para mudar outros ajustes", consumed and action == "feature:hands" and menu.open)
