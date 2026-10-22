"""Verificações locais com sinais e desenhos sintéticos, nunca imagens da webcam."""
import random
import numpy as np

from perception import VisualReactor, tongue_out_score
from robot import Robot
from tracker import SimulatedSource


def test_tongue(check):
    import cv2
    print("E3: língua, padrão bravo e reinterpretação")
    face = np.array([20, 10, 100, 100, 50, 40, 90, 40, 70, 60, 50, 78, 90, 78, .99])
    image = np.full((150, 150, 3), (100, 150, 200), dtype=np.uint8)
    check("E3: pele e boca fechada não bastam", tongue_out_score(image, face) == 0)
    cv2.rectangle(image, (50, 78), (90, 83), (100, 95, 225), -1)
    check("E3: lábio colorido sem protrusão não basta", tongue_out_score(image, face) == 0)
    cv2.ellipse(image, (70, 88), (12, 12), 0, 0, 360, (100, 95, 225), -1)
    check("E3: forma/cor sintéticas compatíveis geram escore", tongue_out_score(image, face) >= .70)
    check("E3: ROI não altera nem retém a imagem", np.array_equal(image[0, 0], [100, 150, 200])
          and isinstance(tongue_out_score(image, face), float))
    small = face.copy(); small[3] = 20
    check("E3: rosto pequeno não gera evidência frágil", tongue_out_score(image, small) == 0)
    state = VisualReactor()
    one = state.update({"tongue_out": .75}, 0)
    empty = state.update({}, .1)
    check("E3: sinal isolado não dispara", not one and not empty)
    state.update({"tongue_out": .75}, .2)
    state.update({"tongue_out": .75}, .3)
    events = state.update({"tongue_out": .75}, .4)
    check("E3: confirmação temporal dispara um evento", len(events) == 1 and events[0].kind == "tongue_out")
    held = [state.update({"tongue_out": .95}, .5 + i / 10) for i in range(60)]
    check("E3: gesto mantido não repete após cooldown", not any(held))
    state.update({}, 6.6)
    state.update({}, 6.75)
    state.update({"tongue_out": .75}, 6.8)
    state.update({"tongue_out": .75}, 6.9)
    check("E3: gesto novo rearma após cooldown", len(state.update({"tongue_out": .75}, 7.0)) == 1)
    state.update({}, 7.1)
    state.update({}, 7.25)
    check("E3: novo gesto respeita cooldown de 4 s", not state.update({"tongue_out": .95}, 7.3))
    check("E3: NaN/inf não disparam", not state.update({"tongue_out": float('nan')}, 12.)
          and not state.update({"tongue_out": float('inf')}, 12.2))

    def run(reduced=False):
        r, src = Robot(seed=8, reduce_motion=reduced), SimulatedSource(seed=7)
        r.set_feature("tongue", True)   # recurso removido da interface; o motor ainda é testado
        seen = []
        for i in range(90):
            src.set_cue("tongue_out", float(i >= 40))
            fr = r.step(src.poll(1 / 60), i / 60, 1 / 60)
            seen.append((fr.shown, tuple(r.expr.blink), tuple(r.expr.pose)))
        return r, seen
    r, trace = run()
    check("E3: SimulatedSource provoca bravo por padrão", trace[-1][0] == "angry")
    check("E3: resultado determinístico por seed/tempo", trace == run()[1])
    check("E3: Reduzir Movimento preserva a reação", run(True)[1][-1][0] == "angry")
    r.set_mood("happy")
    check("E3: IA pode fixar outro humor", r.step(None, 1.6, 1 / 60).shown == "happy")
    r.set_mood("auto"); r.expr.pulse("happy", 1.)
    check("E3: IA pode reinterpretar via pulse", r.step(None, 1.7, 1 / 60).shown == "happy")
    r.perception.reactions["tongue_out"] = None
    check("E3: mapeamento futuro pode desativar reação", r.perception.reactions["tongue_out"] is None)


def test_sensitivity(check):
    import contextlib
    import io
    import main
    from auxiliary import Observation
    from gestures import HandReactor
    print("Tuning: sensibilidade e resposta breve")
    fast = VisualReactor()
    check("Tuning: evidência forte responde no primeiro quadro", len(fast.update({"tongue_out": .95}, 0)) == 1)
    mild = VisualReactor()
    mild.update({"tongue_out": .7}, 0)
    mild.update({"tongue_out": .7}, .1)
    check("Tuning: evidência moderada exige confirmação estável", len(mild.update({"tongue_out": .7}, .19)) == 1)
    low, high = VisualReactor(.5), VisualReactor(2.)
    for now in (0, .1, .2):
        a, b = low.update({"tongue_out": .5}, now), high.update({"tongue_out": .5}, now)
    check("Tuning: --sens maior aceita sinais menores", not a and len(b) == 1)
    check("Tuning: proximidade forte da palma responde sem confirmação extra",
          len(HandReactor().update([Observation("hand", .95, .5, .5, .36)], 0)) == 1)
    check("Tuning: CLI aceita faixa de sensibilidade", main.parse_args(["--sens", "1.5"]).sens == 1.5)
    rejected = []
    for value in ("nan", "0", "3"):
        try:
            with contextlib.redirect_stderr(io.StringIO()): main.parse_args(["--sens", value])
        except SystemExit: rejected.append(value)
    check("Tuning: CLI rejeita NaN e valores fora da faixa", len(rejected) == 3)


def test_hands(check):
    import hashlib
    import io
    import uuid
    import threading
    import time
    from pathlib import Path
    from unittest.mock import patch
    import preparar_modelos as setup
    from auxiliary import AuxiliaryWorker, Observation, ObservedBatch, PalmDetector
    from diagnostics import TimedDetector
    from gestures import HandReactor
    from expressions import ExpressionEngine
    print("E1: mãos, fila privada e recuo")

    class Net:
        def setInput(self, blob): self.input_shape = blob.shape
        def getUnconnectedOutLayersNames(self): return ["boxes", "scores"]
        def forward(self, names):
            boxes = np.zeros((1, 2016, 18), np.float32)
            logits = np.full((1, 2016, 1), -100, np.float32)
            boxes[0, 0, :4] = [44, 92, 32, 32]
            logits[0, 0] = 5
            return boxes, logits
    net = Net()
    palm = PalmDetector(net=net)
    result = palm.infer(np.zeros((240, 320, 3), np.uint8))
    check("E1: tensores ONNX decodificados, coordenadas normalizadas/espelhadas",
          len(result) == 1 and abs(result[0].x - .75) < .001 and abs(result[0].y - .5) < .001
          and net.input_shape == (1, 192, 192, 3))
    check("E1: observações só contêm números e categoria", all(isinstance(v, (str, float))
          for v in vars(result[0]).values()))
    scores = np.full(2016, -100.)
    check("E1: confiança baixa não produz mãos", not palm.decode(np.zeros((2016, 18)), scores, 320, 240, .6, 0, 24))
    class FaceDetector:
        name = "fake"
        def detect(self, frame): return ObservedBatch([], result, "teste local")
    batch = TimedDetector(FaceDetector()).detect(None)
    check("E1: sinais numéricos atravessam instrumentação do mesmo quadro", batch.observations == result
          and batch.perception_status == "teste local")

    h = HandReactor()
    stationary = [h.update([Observation("hand", .9, .5, .5, .2)], i / 5) for i in range(12)]
    check("E1: palma parada não é aceno", not any(e.kind == "wave" for events in stationary for e in events))
    h = HandReactor()
    events = []
    for i, x in enumerate((.30, .50, .70, .50, .30, .50, .70)):
        events += h.update([Observation("hand", .9, x, .5, .2)], i / 5)
    check("E1: trajetória de ida/volta reconhece um aceno", [e.kind for e in events] == ["wave"])
    more = []
    for i, x in enumerate((.5, .3, .5, .7, .5, .3, .5)):
        more += h.update([Observation("hand", .9, x, .5, .2)], 1.4 + i / 5)
    check("E1: cumprimento respeita cooldown", not more)
    h = HandReactor()
    h.update([Observation("hand", .9, .5, .5, .20)], 0)
    events = h.update([Observation("hand", .9, .5, .5, .42)], .2)
    check("E1: mão cresce rápido e dispara reflexo antes do limite de distância", [e.kind for e in events] == ["hand_threat"])
    held = [h.update([Observation("hand", .9, .5, .5, .6)], .4 + i / 5) for i in range(20)]
    check("E1: mão mantida perto não repete", not any(held))
    check("E1: mão sem confiança é ignorada", not HandReactor().update([Observation("hand", .3, .5, .5, .8)], 0))

    def sim_gesture(kind, reduced=False):
        r, src = Robot(seed=22, reduce_motion=reduced), SimulatedSource(seed=6)
        shown, reflex, recoil = [], [], []
        for i in range(150):
            if i == 50: src.gesture(kind)
            f = r.step(src.poll(1 / 60), i / 60, 1 / 60)
            shown.append(f.shown)
            reflex.append(r.expr.blinker.reflex_active)
            recoil.append(not np.array_equal(r.expr.visual_pose(), r.expr.pose))
        return shown, reflex, recoil
    check("E1: SimulatedSource acena e recebe cumprimento feliz", "happy" in sim_gesture("wave")[0][70:])
    normal, reduced = sim_gesture("tap"), sim_gesture("tap", True)
    check("E1: tapa simulado provoca piscada rápida e recuo", any(normal[1]) and any(normal[2]))
    check("E1: Reduzir Movimento conserva reflexo e elimina recuo", any(reduced[1]) and not any(reduced[2]))
    e = ExpressionEngine(); e.recoil_now(); e.update(.1); e.set_reduce_motion(True)
    check("E1: ativar Reduzir Movimento cancela recuo em curso", e._recoil_left == 0 and e.visual_pose() is e.pose)

    started, release = threading.Event(), threading.Event()
    class Slow:
        def infer(self, image):
            started.set(); release.wait(1)
            return [Observation("hand", .9, .5, .5, .3)]
    worker = AuxiliaryWorker([Slow()])
    try:
        worker.submit(np.zeros((480, 640, 3), np.uint8))
        began = started.wait(1)
        for _ in range(10): worker.submit(np.zeros((4, 4, 3), np.uint8))
        check("E1: worker ocupado não acumula quadros", began and worker._pending is None)
        worker.reset(); release.set()
        limit = time.perf_counter() + 1
        while worker._busy and time.perf_counter() < limit: time.sleep(.005)
        check("E1: pausa invalida resultado de inferência em curso", worker.take() is None)
    finally:
        release.set(); worker.close()
    check("E1: fechar worker libera fila e thread", not worker._thread.is_alive() and worker._pending is None)
    worker = AuxiliaryWorker([], clock=lambda: 1.)
    worker._result = (0., result)
    check("E1: resultados com idade >350 ms são descartados", worker.take() is None)
    worker.close()
    temp = Path(__file__).resolve().parent / ("qa_models_" + uuid.uuid4().hex)
    temp.mkdir()  # DACL herdada; mkdtemp(mode=0700) é inacessível no sandbox Windows.
    try:
        payload = b"model-test-only"
        entry = ("hands/test.onnx", hashlib.sha256(payload).hexdigest())
        with patch.object(setup, "ROOT", Path(temp)), patch.dict(setup.MODELS, {"hands": entry}):
            setup.install("hands", opener=lambda *a, **k: io.BytesIO(payload))
            check("E1: preparador verifica SHA-256 antes de instalar", setup.valid_model("hands"))
            setup.model_path("hands").unlink()
            failed = False
            try: setup.install("hands", opener=lambda *a, **k: io.BytesIO(b"invalid"))
            except ValueError: failed = True
            check("E1: hash inválido nunca instala arquivo", failed and not setup.model_path("hands").exists())
    finally:
        for name in ("test.onnx", "test.part"):
            (temp / name).unlink(missing_ok=True)
        temp.rmdir()
    # Este teste é real quando os pesos estiverem presentes; ausência é explícita.
    if setup.valid_model("hands"):
        real = PalmDetector()
        check("E1: modelo ONNX real processa quadro sintético", real.infer(np.zeros((240, 320, 3), np.uint8)) == [])
    else:
        print("  (E1: smoke do modelo real pendente; execute preparar_modelos.bat)")


def test_objects(check):
    import time
    from unittest.mock import patch
    import auxiliary
    from auxiliary import ObjectDetector, AuxiliaryDetector, Observation
    from gestures import ObjectReactor
    import main
    print("E2: NanoDet, eventos e diagnóstico por módulo")
    model = ObjectDetector(net=object())
    classes, boxes = [], []
    for stride in (8, 16, 32):
        n = (416 // stride) ** 2
        classes.append(np.zeros((1, n, 80), np.float32))
        box = np.full((1, n, 32), -20, np.float32)
        box.reshape(1, n, 4, 8)[..., 2] = 20
        boxes.append(box)
    classes[0][0, 52 * 25 + 13, 16] = .8
    out = model.decode([boxes[1], classes[2], classes[0], boxes[2], boxes[0], classes[1]], 416, 416, 1., 0, 0)
    check("E2: outputs fora de ordem reconhecem COCO dog e espelham centro",
          len(out) == 1 and out[0].kind == "dog" and out[0].x > .7 and abs(out[0].score - .8) < .001)
    classes[0][0, 52 * 25 + 13, 0] = .9
    check("E2: person dominante não vira dog por classe secundária", not model.decode(classes + boxes, 416, 416, 1., 0, 0))
    reactor = ObjectReactor()
    obs = [Observation("dog", .7, .5, .5, .3)]
    check("E2: objeto exige confirmação breve", not reactor.update(obs, 0))
    reactor.update(obs, .4)
    check("E2: dog fraco confirmado dispara reação", [e.kind for e in reactor.update(obs, .8)] == ["dog"])
    check("E2: cachorro mantido não repete", not any(reactor.update(obs, 1. + i * .2) for i in range(30)))
    reactor.update([], 7.)
    reactor.update([], 7.6)
    reactor.update(obs, 7.7)
    reactor.update(obs, 8.1)
    check("E2: objeto novo rearma depois do cooldown", len(reactor.update(obs, 8.5)) == 1)
    r, src = Robot(seed=4), SimulatedSource(seed=8)
    for i in range(150):
        if i == 80: src.gesture("dog")
        f = r.step(src.poll(1 / 60), i / 60, 1 / 60)
    check("E2: cachorro em SimulatedSource mantém feliz além da saudação inicial", f.shown == "happy")
    defaults = main.parse_args([])
    off = main.parse_args(["--no-hands", "--no-objects"])
    check("E2: mãos/objetos ligados por padrão e flags para desligar", defaults.hands and defaults.objects
          and not off.hands and not off.objects)
    check("E2: launcher respeita preferências e permite flags explícitas", "main.py %*" in
          (main.Path(main.__file__).parent / "iniciar.bat").read_text(encoding="utf-8"))
    class Base:
        name="fake"
        def detect(self, image): return []
    with patch.object(auxiliary, "valid_model", return_value=False):
        detector=AuxiliaryDetector(Base())
        status=detector.module_statuses()
        check("E2: pesos ausentes aparecem como não carregados com erro", all(not m["loaded"] and m["error"] for m in status.values()))
        detector.close()
    from preparar_modelos import valid_model
    if valid_model("objects"):
        start=time.perf_counter(); real=ObjectDetector(); load_ms=(time.perf_counter()-start)*1000
        start=time.perf_counter(); output=real.infer(np.zeros((240,320,3),np.uint8)); ms=(time.perf_counter()-start)*1000
        check("E2: ONNX real carrega e processa quadro sintético", output == [], f"carga {load_ms:.1f} ms; inferência {ms:.1f} ms")
    else:
        print("  (E2: modelo real ausente, preparar_modelos.bat)")


def test_smile(check):
    from auxiliary import SmileDetector
    from perception import smile_landmark_score
    from tracker import Detection
    from preparar_modelos import valid_model, main as setup_main
    from unittest.mock import patch
    print("E4: sorriso real por FER e fallback sinalizado")
    identity = SmileDetector.alignment(SmileDetector.canonical)
    check("E4: alinhamento canônico preserva geometria", np.allclose(identity, [[1,0,0],[0,1,0]], atol=1e-5))
    class HappyNet:
        def setInput(self, value): self.shape=value.shape; self.minimum=float(value.min()); self.maximum=float(value.max())
        def forward(self): return np.array([[.01,.01,.01,.94,.01,.01,.01]],np.float32)
    net=HappyNet(); model=SmileDetector(net=net, mirror=False)
    face=Detection(0,0,1,1,.5,.4,landmarks=tuple(map(tuple, SmileDetector.canonical / 112)))
    observations=model.infer(np.full((112,112,3),127,np.uint8), [face])
    check("E4: FER usa RGB NCHW 112×112 em [-1,1]", net.shape==(1,3,112,112) and -1<=net.minimum<=net.maximum<=1)
    check("E4: classe happy (3) alimenta sinal smile", len(observations)==1 and observations[0].score>.9 and model.last_class=="happy")
    check("E4: sem landmarks não inventa sorriso", model.infer(np.zeros((112,112,3),np.uint8),[])[0].score==0)
    raw=np.array([0,0,100,100,30,35,70,35,50,55,34,75,66,75,.99],np.float32)
    neutral=smile_landmark_score(raw)
    raw[10],raw[12]=27,73
    check("E4: landmarks distinguem sorriso largo do neutro", neutral<.3 and smile_landmark_score(raw)>.9)
    r,src=Robot(seed=9),SimulatedSource(seed=9); r.set_feature("tongue", True)
    for i in range(150):
        src.set_cue("smile",float(i>=100))
        fr=r.step(src.poll(1/60),i/60,1/60)
    check("E4: sorriso numérico realimenta happy no Robot", fr.shown=="happy")
    r,src=Robot(seed=9),SimulatedSource(seed=9); r.set_feature("tongue", True)
    for i in range(150):
        src.set_cue("smile",float(i>=100)); src.set_cue("tongue_out",float(i>=100))
        fr=r.step(src.poll(1/60),i/60,1/60)
    check("E4: sorriso não sobrescreve língua→bravo padrão", fr.shown=="angry")
    reactor=VisualReactor()
    event=reactor.update({"smile":.95},0,{"smile"})
    held=[reactor.update({"smile":.95},i/10,{"smile"}) for i in range(1,50)]
    check("E4: sorriso forte reage em uma inferência, com cooldown/latch", len(event)==1 and event[0].kind=="smile" and not any(held))
    with patch("preparar_modelos.install", side_effect=AssertionError("rede indevida")):
        check("E4: launcher simulado não tenta baixar pesos", setup_main(["--startup","--sim","--run-seconds","1"])==0)
    if valid_model("smile"):
        import time
        start=time.perf_counter(); real=SmileDetector(mirror=False)
        result=real.infer(np.zeros((112,112,3),np.uint8),[face])
        check("E4: modelo FER real processa face sintética", len(result)==1 and 0<=result[0].score<=1,
              f"carga+inferência {(time.perf_counter()-start)*1000:.1f} ms")
    else:
        print("  (E4: peso FER ainda ausente; fallback landmarks explícito; launcher prepara automaticamente)")
