#!/usr/bin/env python3
"""Nuria — visão e interpretação do ambiente com LLM.

Rosto de LEDs que acompanha as pessoas pela webcam e responde, por voz, perguntas sobre o que vê:
Espaço (ou o botão) = perguntar; a foto do momento e a pergunta vão para um modelo multimodal.
Sem câmera? `--sim` (mouse = rosto) e `--imagem foto.jpg`.   Ajuda dentro do app: F1.
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pygame

import theme
from expressions import KEY_ORDER, LABELS
from diagnostics import FrameMetrics, TimedDetector
from renderer import HudInfo, Renderer
from robot import Robot
from tracker import CameraSource, SimulatedSource, make_detector


def parse_args(argv=None):
    def sensitivity(value):
        number = float(value)
        if not .5 <= number <= 2.0:
            raise argparse.ArgumentTypeError("--sens deve ficar entre 0.5 e 2.0")
        return number
    ap = argparse.ArgumentParser(description="Nuria: robô virtual que vê pela webcam e responde por voz.")
    ap.add_argument("--camera", type=int, default=None, help="fixar índice da webcam (padrão: selecionar câmera física automaticamente)")
    ap.add_argument("--sim", action="store_true", help="não usar a câmera: o mouse vira um rosto")
    window = ap.add_mutually_exclusive_group()
    window.add_argument("--windowed", action="store_true", help="abrir em janela (padrão)")
    window.add_argument("--fullscreen", action="store_true", help="abrir em tela cheia")
    ap.add_argument("--size", default="1280x720", help="tamanho da janela com --windowed")
    ap.add_argument("--grid", default="80x45", help="resolução da matriz de LEDs, ex.: 64x36 ou 96x54")
    ap.add_argument("--scheme", default="auto", choices=["auto", "dark", "light", "contrast"],
                    help="aparência (auto = segue o sistema)")
    ap.add_argument("--reduce-motion", action="store_true", help="forçar 'reduzir movimento'")
    ap.add_argument("--no-mirror", action="store_true", help="não espelhar a imagem da câmera")
    ap.add_argument("--no-glow", action="store_true", help="desligar o brilho dos LEDs (mais leve)")
    ap.add_argument("--det-width", type=int, default=416, help="largura usada na detecção (menor = mais rápido)")
    ap.add_argument("--model", default=None, help="caminho do modelo YuNet (.onnx)")
    ap.add_argument("--sleep-after", type=float, default=None, help="segundos sem ninguém até dormir")
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument("--debug", action="store_true", help="mostrar diagnóstico numérico (tecla D alterna)")
    from preferences import DEFAULT_FEATURES, LABELS as FEATURE_LABELS
    for name in DEFAULT_FEATURES:
        ap.add_argument("--" + name.replace("_", "-"), action=argparse.BooleanOptionalAction,
                        default=argparse.SUPPRESS, help=FEATURE_LABELS[name] + " (respeita preferência salva)")
    ap.add_argument("--sens", type=sensitivity, default=1.0, help="sensibilidade 0.5–2.0; maior reage com sinais menores")
    ap.add_argument("--ia", default="realtime", choices=["realtime", "simulada", "legado"],
                    help="realtime = OpenAI Realtime (padrão); simulada = servidor Realtime falso, sem rede/chave; "
                         "legado = 3 chamadas separadas (transcrição, visão, voz)")
    ap.add_argument("--sem-ia", action="store_true", help="esconder a coluna da Nuria (só o rosto)")
    ap.add_argument("--imagem", default=None, help="foto usada quando não há câmera (ensaio/demonstração)")
    ap.add_argument("--sem-registro", action="store_true", help="não gravar logs/sessoes.jsonl (só texto e tempos)")
    ap.add_argument("--run-seconds", type=float, default=0, help=argparse.SUPPRESS)   # só para testes
    parsed = ap.parse_args(argv)
    parsed.feature_overrides = {key: getattr(parsed, key) for key in DEFAULT_FEATURES if hasattr(parsed, key)}
    for key, default in DEFAULT_FEATURES.items():
        if not hasattr(parsed, key):
            setattr(parsed, key, default)
    return parsed


def set_dpi_aware() -> None:
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def main(argv=None) -> int:
    args = parse_args(argv)
    import preferences
    features = preferences.load()
    features.update(args.feature_overrides)
    for key, value in features.items():
        setattr(args, key, value)
    set_dpi_aware()
    pygame.init()
    pygame.display.set_caption("Nuria")
    gw, gh = (int(v) for v in args.grid.lower().split("x"))
    ww, wh = (max(minimum, int(v)) for minimum, v in zip((640, 480), args.size.lower().split("x")))
    fullscreen = args.fullscreen

    def open_window():
        if fullscreen:
            return pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        return pygame.display.set_mode((ww, wh), pygame.RESIZABLE)

    screen = open_window()
    scheme = theme.resolve_scheme(args.scheme)
    reduce_motion = args.reduce_motion or theme.system_prefers_reduced_motion()

    from attention import AttentionConfig
    cfg = AttentionConfig()
    if args.sleep_after:
        cfg.sleep_after = args.sleep_after
        cfg.sleepy_after = args.sleep_after * 0.5
    robot = Robot((gw, gh), reduce_motion=reduce_motion, cfg=cfg, sensitivity=args.sens)
    for key, value in features.items():
        robot.set_feature(key, value)
    renderer = Renderer(screen, (gw, gh), scheme, glow=not args.no_glow)
    from assistente import Assistant
    from painel_assistente import AssistantPanel
    import credenciais
    panel = AssistantPanel()
    panel.visible = not args.sem_ia

    def relayout(surface):
        renderer.resize(surface)
        renderer.reserve_right = renderer.menu.reserve_right = panel.reserve(surface, renderer.s)
        renderer.resize(surface)

    relayout(screen)
    hud = HudInfo(reduce_motion=reduce_motion, debug=args.debug, sensitivity=args.sens)
    hud.features = dict(features)

    # ---- painel LED físico (Arduino): só espelha o rosto; a interface na tela não muda ----
    import arduino_led
    led = arduino_led.ArduinoSettings.load()
    led_link = arduino_led.ArduinoLink()
    led_ports = []
    led_last = 0.0
    if led.enabled:
        led_link.start(led.port)
        led_link.set_brightness(led.brightness)

    def led_state():
        port_label = "Automática" if led.port == "auto" else led.port
        if led.enabled and led_link.port and led.port == "auto":
            port_label = f"Automática ({led_link.port})"
        hud.arduino = dict(enabled=led.enabled, status=led_link.status if led.enabled else "desligado",
                           port_label=port_label, brightness=led.brightness, size=led.size, test=led.test,
                           input_right=led.input_right, rotation=led.rotation, mirror=led.mirror,
                           upside_down=led.upside_down)

    def led_action(name):
        nonlocal led_ports
        if name == "enabled":
            led.enabled = not led.enabled
            if led.enabled:
                led_link.start(led.port)
                led_link.set_brightness(led.brightness)
            else:
                led_link.stop()
        elif name == "port":
            led_ports = [d for d, _ in arduino_led.list_ports()]
            options = ["auto"] + led_ports
            led.port = options[(options.index(led.port) + 1) % len(options)] if led.port in options else "auto"
            if led.enabled:
                led_link.reconnect(led.port)
        elif name == "brightness":
            steps = arduino_led.BRIGHTNESS_STEPS
            led.brightness = next((v for v in steps if v > led.brightness), steps[0])
            led_link.set_brightness(led.brightness)
        elif name == "size":
            order = list(arduino_led.SIZES)
            led.size = order[(order.index(led.size) + 1) % len(order)]
        elif name == "test":
            led.test = not led.test
        elif name == "order":
            led.input_right = not led.input_right
        elif name == "rotate":
            led.rotation = (led.rotation + 90) % 360
        elif name == "mirror":
            led.mirror = not led.mirror
        elif name == "flip":
            led.upside_down = not led.upside_down
        try:
            led.save()
        except OSError:
            toast("Ajuste do Arduino aplicado; não foi possível salvar")
        led_state()

    # ---- aba API: chave, teste, modelo e voz (grava no .env; a sessão Realtime se refaz sozinha) ----
    from entrada_chave import KeyEntry
    key_entry = KeyEntry()
    api_ui = dict(test="Tocar para testar a Realtime", confirm_remove=False)

    def api_state():
        cfg = credenciais.load()
        hud.api = dict(configured=cfg.configured, masked=credenciais.mask(cfg.api_key) if cfg.configured else "",
                       model=cfg.realtime_model, voice=cfg.realtime_voice, test=api_ui["test"],
                       confirm_remove=api_ui["confirm_remove"], source=credenciais.key_source(),
                       robot_voice=cfg.robot_voice)

    def api_reconnect():
        if hasattr(assistant, "warmup") and args.ia == "realtime" and credenciais.load().configured:
            assistant.warmup()                   # chave/modelo/voz novos: a sessão é recriada na hora

    def api_test():
        api_ui["test"] = "Testando…"
        api_state()

        def run():
            from assistente_realtime import check_connection
            ok, msg = check_connection(credenciais.load())
            api_ui["test"] = ("OK · " if ok else "Falhou · ") + msg
            api_state()
        threading.Thread(target=run, name="api-test", daemon=True).start()

    def cycle(options, current):
        return options[(options.index(current) + 1) % len(options)] if current in options else options[0]

    def api_action(name):
        cfg = credenciais.load()
        if name != "remove":
            api_ui["confirm_remove"] = False
        try:
            if name == "key":
                key_entry.open()
            elif name == "test":
                api_test()
            elif name == "model":
                credenciais.set_env_values({"NURIA_MODELO_REALTIME": cycle(credenciais.REALTIME_MODELS, cfg.realtime_model)})
                api_reconnect()
            elif name == "voice":
                credenciais.set_env_values({"NURIA_VOZ_REALTIME": cycle(credenciais.REALTIME_VOICES, cfg.realtime_voice)})
                api_reconnect()
            elif name == "robot":
                set_robot_voice(not cfg.robot_voice)
            elif name == "remove":
                if api_ui["confirm_remove"]:
                    credenciais.set_env_values({"OPENAI_API_KEY": None})
                    api_ui["confirm_remove"] = False
                    api_ui["test"] = "Chave removida do .env"
                else:
                    api_ui["confirm_remove"] = True
        except OSError as error:
            toast(f"Não foi possível gravar o .env: {error}")
        api_state()

    def set_robot_voice(on):
        credenciais.set_env_values({"NURIA_VOZ_ROBOTICA": "1" if on else "0"})
        if hasattr(assistant, "robot_voice"):
            assistant.robot_voice = bool(on)
        toast("Voz robótica " + ("ligada" if on else "desligada"))

    def api_save_key(key):
        try:
            credenciais.set_env_values({"OPENAI_API_KEY": key})
        except OSError as error:
            toast(f"Não foi possível gravar o .env: {error}")
            return
        warn = " (a variável do Windows tem prioridade)" if credenciais.key_source() == "windows" else ""
        toast("Chave salva" + warn)
        ai_label.at = -9                         # o subtítulo da coluna relê o .env já
        api_reconnect()
        api_test()

    api_state()

    # ---- controle remoto (etapa 2): sala do operador no navegador, por WebRTC ----
    import json as _json
    remote_cfg_path = Path(__file__).resolve().parent / "remote.local.json"
    try:
        remote_cfg = _json.loads(remote_cfg_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        remote_cfg = {}
    remote = dict(server=None, enabled=bool(remote_cfg.get("enabled", False)), port=int(remote_cfg.get("port", 8765)),
                  pin=str(remote_cfg.get("pin", "")), badge=bool(remote_cfg.get("badge", True)), faces=0)

    def remote_save():
        try:
            remote_cfg_path.write_text(_json.dumps(dict(enabled=remote["enabled"], port=remote["port"], pin=remote["pin"],
                                                        badge=remote["badge"]), indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass

    def remote_frame():
        if not using_sim and hasattr(src, "capture_still"):
            return src.capture_still(0.5)
        if args.imagem:
            import cv2
            return cv2.imread(args.imagem)
        return None

    def remote_telemetry():
        s = assistant.snapshot()
        return dict(estado=s.state, modo=getattr(s, "mode", "autonomo"), pergunta=s.question, resposta=s.answer,
                    mensagem=s.message if s.message_kind in ("warning", "error") else "", expressao=LABELS.get(
                        robot.expr.shown_name(), robot.expr.shown_name()), visao=s.vision,
                    voz_robotica=bool(getattr(assistant, "robot_voice", False)), rostos=remote["faces"])

    def remote_start():
        from operador import OperatorServer, new_pin
        remote["pin"] = remote["pin"] if (len(remote["pin"]) == 6 and remote["pin"].isdigit()) else new_pin()
        server = OperatorServer(remote_frame, remote_telemetry,
                                lambda pcm: assistant.operator_audio(pcm) if hasattr(assistant, "operator_audio") else None,
                                robot_muted=lambda: assistant.player.pending() > 0 if hasattr(assistant.player, "pending") else False,
                                port=remote["port"], pin=remote["pin"])
        if server.start():
            remote["server"] = server
        else:
            toast(server.status)
            remote["enabled"] = False
        remote_save()
        remote_state()

    def remote_stop():
        if remote["server"] is not None:
            remote["server"].stop()
            remote["server"] = None
        if hasattr(assistant, "set_operator"):
            assistant.set_operator("")

    def remote_state():
        server = remote["server"]
        hud.remote = dict(enabled=remote["enabled"] and server is not None,
                          status=server.status if server else "desligada", url=server.url if server else "",
                          pin=server.pin if server else "", operator=server.operator if server else "",
                          badge=remote["badge"])

    def remote_action(name):
        if name == "enabled":
            remote["enabled"] = not remote["enabled"]
            remote_start() if remote["enabled"] else (remote_stop(), remote_save())
        elif name == "pin" and remote["server"]:
            remote["pin"] = remote["server"].regenerate_pin()
            remote_save()
        elif name == "kick" and remote["server"]:
            remote["server"].disconnect_operator()
        elif name == "badge":
            remote["badge"] = not remote["badge"]
            remote_save()
        remote_state()

    def remote_command(kind, data):
        nowt = time.perf_counter() - t0
        if kind == "operator":
            if hasattr(assistant, "set_operator"):
                assistant.set_operator(data)
            toast(f"Operador conectado: {data}" if data else "Operador desconectado")
        elif kind == "mode" and hasattr(assistant, "set_mode"):
            assistant.set_mode(str(data.get("modo", "")))
        elif kind == "say" and hasattr(assistant, "operator_say"):
            assistant.operator_say(str(data.get("texto", "")))
        elif kind == "interrupt":
            assistant.cancel()
        elif kind == "vision":
            assistant.set_vision(bool(data.get("ligado")))
        elif kind == "robot_voice":
            set_robot_voice(bool(data.get("ligado")))
            api_state()
        elif kind == "look":
            try:
                gx, gy = float(data.get("gx", 0)), float(data.get("gy", 0))
                robot.attention.look_at(max(-1, min(1, gx)), max(-1, min(1, gy)), 3.0, nowt)
            except (TypeError, ValueError):
                pass
        elif kind == "expression":
            name = str(data.get("nome", ""))
            if name == "auto":
                robot.set_mood("auto")
                robot.stop("cry")
                robot.stop("plead")
            elif name in ("cry", "plead"):
                robot.play(name)
            elif name == "wink":
                robot.wink()
            elif name == "blink":
                robot.blink()
            elif name == "center":
                robot.attention.look_at(0.0, 0.0, 2.0, nowt)
            elif name in LABELS:
                robot.set_mood(name)
        remote_state()

    if remote["enabled"]:
        remote_start()
    remote_state()


    led_state()
    try:
        git = Path(__file__).resolve().parent.parent / ".git"
        head = (git / "HEAD").read_text().strip()
        branch = head.removeprefix("ref: ")
        revision = (git / branch).read_text().strip() if head.startswith("ref: ") else head
        hud.build_label = branch.removeprefix("refs/heads/") + " · " + revision[:7]
    except OSError:
        hud.build_label = "desenvolvimento"
    metrics = FrameMetrics()

    # ---- fonte de detecções --------------------------------------------------
    src = None
    sim = SimulatedSource()
    if not args.sim:
        try:
            detector, warn = make_detector(args.model, args.det_width, mirror=not args.no_mirror,
                                           hands=args.hands, objects=args.objects, sensitivity=args.sens,
                                           tongue=args.tongue, smile=args.smile)
            hud.detector = detector.name
            src = CameraSource(TimedDetector(detector), args.camera)
            src.start()
            if warn:
                hud.toast, hud.toast_age = warn, 0.0
        except Exception as e:
            hud.camera_note = str(e)
            src = None
    using_sim = src is None
    if using_sim:
        src = sim
        sim.add_roamer()
    hud.simulated, hud.fullscreen = using_sim, fullscreen

    def still():
        """Foto do momento da pergunta: câmera real; sem câmera, a imagem de --imagem."""
        if not using_sim and hasattr(src, "capture_still"):
            image = src.capture_still()
            if image is not None:
                return image
        if args.imagem:
            import cv2
            return cv2.imread(args.imagem)
        return None

    if args.ia == "legado":
        def backend_factory():
            import ia
            return ia.OpenAIBackend(credenciais.load())
        assistant = Assistant(still, backend_factory, log=not args.sem_registro)
    else:
        from assistente_realtime import RealtimeAssistant
        if args.ia == "simulada":
            import realtime
            assistant = RealtimeAssistant(still, transport_factory=lambda cfg: realtime.FakeRealtimeServer(),
                                          config_loader=lambda: credenciais.load(environ={"OPENAI_API_KEY": "simulada"}),
                                          log=not args.sem_registro, label="Realtime simulada · sem rede")
        else:
            assistant = RealtimeAssistant(still, log=not args.sem_registro)
        if args.ia == "simulada" or credenciais.load().configured:
            assistant.warmup()                  # conecta já; a 1ª pergunta não espera o aperto de mão
    assistant_state = "idle"

    def ai_label():
        if args.ia == "simulada":
            return assistant.backend_label()
        if time.perf_counter() - getattr(ai_label, "at", -9) > 2.0:     # relê o .env sem reiniciar
            ai_label.cfg, ai_label.at = credenciais.load(), time.perf_counter()
        cfg = ai_label.cfg
        if not cfg.configured:
            return "Chave da OpenAI ausente · veja .env.example"
        if args.ia == "legado":
            return f"Modo legado (3 chamadas) · {assistant.snapshot().model or cfg.vision_model}"
        return f"{assistant.backend_label()} · {cfg.realtime_model}"

    clock = pygame.time.Clock()
    t0 = time.perf_counter()
    last = t0
    fps_avg, slow_frames = 60.0, 0
    speaker_cycle = 0
    last_logged_trigger = 0

    def toast(msg: str) -> None:
        hud.toast, hud.toast_age = msg, 0.0

    running = True
    while running:
        clock.tick(args.fps)
        now_abs = time.perf_counter()
        elapsed = now_abs - last
        dt = min(elapsed, 0.05)
        last = now_abs
        now = now_abs - t0
        if args.run_seconds and now > args.run_seconds:
            running = False
        hud.hint_age += dt
        hud.active_actions = tuple(robot.actions.active())
        hud.toast_age += dt

        for ev in pygame.event.get():
            if key_entry.active and ev.type not in (pygame.QUIT, pygame.VIDEORESIZE):
                result = key_entry.handle_event(ev)
                if result and result[0] == "save":
                    api_save_key(result[1])
                continue
            if panel.typing and ev.type not in (pygame.QUIT, pygame.VIDEORESIZE):
                panel.handle_event(ev, assistant)
                continue
            consumed, action = renderer.handle_event(ev, hud)
            if action and action.startswith("feature:"):
                name = action.split(":", 1)[1]
                enabled = not features[name]
                features[name] = enabled
                setattr(args, name, enabled)
                robot.set_feature(name, enabled)
                hud.features = dict(features)
                if not using_sim and hasattr(src, "set_feature"):
                    src.set_feature(name, enabled)
                try:
                    preferences.save(features)
                except OSError:
                    toast("Ajuste aplicado; não foi possível salvar a preferência")
                continue
            if action and action.startswith("remote:"):
                remote_action(action.split(":", 1)[1])
                continue
            if action and action.startswith("api:"):
                api_action(action.split(":", 1)[1])
                continue
            if action and action.startswith("arduino:"):
                led_action(action.split(":", 1)[1])
                continue
            if action and action.startswith("sim:"):
                name = action.split(":", 1)[1]
                if name in robot.actions.active():
                    robot.stop(name)
                else:
                    robot.play(name)
                continue
            if action and action.startswith("camera:"):
                selected = None if action == "camera:auto" else int(action.split(":")[1])
                if using_sim:
                    try:
                        detector, warn = make_detector(args.model, args.det_width, mirror=not args.no_mirror,
                                                       hands=args.hands, objects=args.objects, sensitivity=args.sens,
                                                       tongue=args.tongue, smile=args.smile)
                        src = CameraSource(TimedDetector(detector), selected)
                        src.start()
                        using_sim = False
                        hud.detector = detector.name
                    except Exception as error:
                        hud.camera_note = str(error)
                else:
                    src.set_camera(selected)
                metrics.reset()
                continue
            if action:
                keys = {"pause": pygame.K_p, "theme": pygame.K_c, "motion": pygame.K_m,
                        "fullscreen": pygame.K_F11, "help": pygame.K_F1, "debug": pygame.K_d, "quit": pygame.K_q}
                ev = pygame.event.Event(pygame.KEYDOWN, key=keys[action])
            elif consumed:
                continue
            if not action and panel.handle_event(ev, assistant):
                continue
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.VIDEORESIZE:
                if not fullscreen:
                    ww, wh = max(640, ev.w), max(480, ev.h)
                    if pygame.display.get_surface().get_size() != (ww, wh):
                        open_window()
                screen = pygame.display.get_surface()
                relayout(screen)
            elif ev.type == pygame.KEYDOWN:
                k = ev.key
                if k in (pygame.K_ESCAPE, pygame.K_q):
                    running = False
                elif k == pygame.K_F11:
                    fullscreen = not fullscreen
                    hud.fullscreen = fullscreen
                    screen = open_window()
                    relayout(screen)
                elif pygame.K_1 <= k <= pygame.K_8:
                    name = KEY_ORDER[k - pygame.K_1]
                    robot.set_mood(name)
                    toast(f"Expressão: {LABELS[name]}")
                elif k == pygame.K_0:
                    robot.set_mood("auto")
                    toast("Expressão: automático")
                elif k == pygame.K_b:
                    robot.blink()
                elif k == pygame.K_k:
                    robot.wink()
                elif k == pygame.K_p:
                    paused = not getattr(src, "paused", False)
                    src.set_paused(paused)
                    toast("Câmera pausada" if paused else "Câmera retomada")
                elif k == pygame.K_TAB:
                    order = ["full", "minimal", "off"]
                    hud.hud_mode = order[(order.index(hud.hud_mode) + 1) % 3]
                elif k == pygame.K_c:
                    order = theme.SCHEME_ORDER
                    scheme = theme.SCHEMES[order[(order.index(scheme.key) + 1) % len(order)]]
                    renderer.set_scheme(scheme)
                    toast(f"Tema: {scheme.label}")
                elif k == pygame.K_m:
                    reduce_motion = not reduce_motion
                    robot.set_reduce_motion(reduce_motion)
                    hud.reduce_motion = reduce_motion
                    toast("Movimento reduzido: " + ("ligado" if reduce_motion else "desligado"))
                elif k == pygame.K_g:
                    renderer.glow_on = not renderer.glow_on
                    toast("Brilho: " + ("ligado" if renderer.glow_on else "desligado"))
                elif k == pygame.K_d:
                    hud.debug = not hud.debug
                elif k == pygame.K_s:
                    ids = [t.id for t in robot.tracker.tracks if t.confirmed and t.visible]
                    if ids:
                        speaker_cycle = (speaker_cycle + 1) % (len(ids) + 1)
                        sid = None if speaker_cycle == 0 else ids[speaker_cycle - 1]
                        robot.attention.set_speaker(sid)
                        toast("Falando: ninguém" if sid is None else f"Falando: rosto #{sid}")
                elif using_sim and k == pygame.K_t:
                    sim.set_cue("tongue_out", 1.0)
                elif using_sim and k in (pygame.K_w, pygame.K_j):
                    sim.gesture("wave" if k == pygame.K_w else "tap")
                elif using_sim and k == pygame.K_o:
                    sim.gesture("dog")
                elif using_sim and k == pygame.K_u:
                    sim.set_cue("smile", 1.0)
                elif k in (pygame.K_F1, pygame.K_h, pygame.K_QUESTION, pygame.K_SLASH):
                    hud.show_help = not hud.show_help
                    hud.help_scroll = 0
                elif using_sim and k in (pygame.K_PLUS, pygame.K_EQUALS, pygame.K_KP_PLUS):
                    sim.add_roamer()
                elif using_sim and k in (pygame.K_MINUS, pygame.K_KP_MINUS):
                    sim.remove_roamer()
            elif using_sim and ev.type == pygame.KEYUP and ev.key == pygame.K_t:
                sim.set_cue("tongue_out", 0.0)
            elif using_sim and ev.type == pygame.KEYUP and ev.key == pygame.K_u:
                sim.set_cue("smile", 0.0)
            elif using_sim and ev.type == pygame.MOUSEWHEEL:
                sim.zoom(0.03 * ev.y)
            elif using_sim and ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 3:
                sim.toggle_mouse_face()

        # ---- entrada ---------------------------------------------------------
        if using_sim:
            mx, my = pygame.mouse.get_pos()
            p = renderer.panel
            sim.set_mouse((mx - p.x) / p.w, (my - p.y) / p.h)
            dets = sim.poll(dt)
        else:
            dets = src.poll()

        if remote["server"] is not None:
            while True:
                try:
                    kind, data = remote["server"].commands.get_nowait()
                except Exception:
                    break
                remote_command(kind, data)
            if int(now * 2) != int((now - dt) * 2):
                remote_state()
        assistant_state = assistant.drive_robot(robot, now, assistant_state)
        frame = robot.step(dets, now, dt)
        if led.enabled and now - led_last >= 1 / 30 and getattr(robot, "last_render_args", None):
            led_last = now                       # 30 quadros/s bastam ao painel; render 32×8 próprio
            canvas = arduino_led.test_pattern() if led.test else arduino_led.render_canvas(robot.last_render_args, led.size)
            led_link.submit(arduino_led.encode(canvas, led))
        if led.enabled and int(now * 2) != int((now - dt) * 2):
            led_state()                          # status da conexão atualizado 2×/s
        hud.visual_scores = dict(robot.perception.last_scores)
        hud.face_proximity = frame.attention.proximity
        remote["faces"] = frame.attention.n_faces
        hud.last_trigger = dict(robot.last_trigger) if robot.last_trigger else None
        hud.trigger_age = max(0., now - robot.last_trigger["time"]) if robot.last_trigger else None
        if hud.debug:
            for trigger in robot.trigger_history:
                if trigger["sequence"] > last_logged_trigger:
                    print(f"[gatilho] {trigger['kind']} score/medida={trigger['score']:.3f} t={trigger['time']:.3f}s", flush=True)
                    last_logged_trigger = trigger["sequence"]
        if dets is not None:
            hud.perception_note = getattr(dets, "perception_status", "")
        camera_info = src.capture_info() if not using_sim and hasattr(src, "capture_info") else None
        camera_ready = not using_sim and (camera_info["state"] == "ok" if camera_info else src.status == "ok")
        if not camera_ready:
            metrics.reset()
        else:
            metrics.accept(dets)

        # ---- desenho ---------------------------------------------------------
        fps_avg = 0.95 * fps_avg + 0.05 * (1.0 / max(elapsed, 1e-3))
        hud.fps = fps_avg
        hud.simulated = using_sim
        hud.detect_fps = src.detect_fps if camera_ready else None
        hud.latency_ms, hud.latency_p95_ms = metrics.latency_ms, metrics.p95_ms
        hud.detection_ms, hud.latency_samples = metrics.detection_ms, len(metrics.samples)
        hud.sample_age_ms = ((time.perf_counter() - metrics.received_at) * 1000
                             if metrics.received_at is not None else None)
        if using_sim:
            hud.camera_status = "paused" if sim.paused else ("error" if hud.camera_note and not args.sim else "sim")
        else:
            hud.camera_status = camera_info["state"] if camera_info else src.status
            if camera_info:
                hud.camera_note = camera_info["note"]
                hud.camera_index, hud.camera_name = camera_info["index"], camera_info["name"]
                hud.camera_backend = camera_info["backend"]
                hud.capture_resolution = camera_info["resolution"]
                hud.capture_fps, hud.mean_brightness = camera_info["capture_fps"], camera_info["brightness"]
                if hud.mean_brightness is not None:
                    robot.set_ambient(hud.mean_brightness / 255.0)
                hud.camera_choices = camera_info["choices"]
                hud.perception_modules = camera_info.get("modules", {})
        renderer.draw_panel(frame, dt, reduce_motion)
        panel.draw(renderer, assistant, dt, reduce_motion, ai_label())
        renderer.draw_hud(frame, hud)
        if hud.remote.get("operator") and hud.remote.get("badge", True):   # Ajustes → Controle remoto → Aviso na tela
            modo = {"autonomo": "autônomo", "assistido": "assistido", "teleoperado": "teleoperado"}.get(
                assistant.snapshot().mode, "")
            renderer._chip([f"Operador conectado · {hud.remote['operator']} · vendo e ouvindo · modo {modo}"],
                           (renderer.panel.centerx, int(16 * renderer.s)), "tc", "circle",
                           renderer.scheme.palette["red"])
        key_entry.draw(renderer, hud.api.get("source", ""))
        pygame.display.flip()
        metrics.presented(time.perf_counter())

        # qualidade adaptativa: se estiver pesado, desliga o brilho
        slow_frames = slow_frames + 1 if elapsed > 0.026 else 0
        if slow_frames > 90 and renderer.glow_on:
            renderer.glow_on = False
            toast("Brilho desligado para manter a fluidez")
            slow_frames = 0

    assistant.shutdown()
    led_link.stop()
    remote_stop()
    if hasattr(src, "stop"):
        src.stop()
    pygame.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
