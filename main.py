#!/usr/bin/env python3
"""Ayra — visão e interpretação do ambiente com LLM.

Rosto de LEDs que acompanha as pessoas pela webcam e responde, por voz, perguntas sobre o que vê:
Espaço (ou o botão) = perguntar; a foto do momento e a pergunta vão para um modelo multimodal.
Sem câmera? `--sim` (mouse = rosto) e `--imagem foto.jpg`.   Ajuda dentro do app: F1.
"""
from __future__ import annotations

import argparse
import os
import sys

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "2")      # bibliotecas numéricas não abrem uma thread por núcleo em cada chamada
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pygame

import theme
from expressions import KEY_ORDER, LABELS
import historico
from conversas_ui import ChatHistory
from diagnostics import FrameMetrics, TimedDetector, thread_cpu, thread_name
from renderer import HudInfo, Renderer
from robot import Robot
from tracker import CameraSource, SimulatedSource, make_detector


def parse_args(argv=None):
    def sensitivity(value):
        number = float(value)
        if not .5 <= number <= 2.0:
            raise argparse.ArgumentTypeError("--sens deve ficar entre 0.5 e 2.0")
        return number
    ap = argparse.ArgumentParser(description="Ayra: robô virtual que vê pela webcam e responde por voz.")
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
    ap.add_argument("--fps", type=int, default=None, help="limite de quadros/s (padrão 60; 30 no modo leve)")
    ap.add_argument("--uso", action="store_true",
                    help="abre direto no Modo de uso: só a interação com o robô, sem ajustes nem painéis (F10 sai)")
    ap.add_argument("--leve", action="store_true", default=None,
                    help="modo leve (Raspberry Pi): 30 fps, sem brilho nem vidro desfocado, detecção menor. "
                         "Liga sozinho em processadores ARM ou com NURIA_LEVE=1")
    ap.add_argument("--debug", action="store_true", help="mostrar diagnóstico numérico (tecla D alterna)")
    from preferences import DEFAULT_FEATURES, LABELS as FEATURE_LABELS
    for name in DEFAULT_FEATURES:
        ap.add_argument("--" + name.replace("_", "-"), action=argparse.BooleanOptionalAction,
                        default=argparse.SUPPRESS, help=FEATURE_LABELS[name] + " (respeita preferência salva)")
    ap.add_argument("--sens", type=sensitivity, default=1.0, help="sensibilidade 0.5–2.0; maior reage com sinais menores")
    ap.add_argument("--ia", default="realtime", choices=["realtime", "simulada", "legado"],
                    help="realtime = OpenAI Realtime (padrão); simulada = servidor Realtime falso, sem rede/chave; "
                         "legado = 3 chamadas separadas (transcrição, visão, voz)")
    ap.add_argument("--sem-ia", action="store_true", help="esconder a coluna da Ayra (só o rosto)")
    ap.add_argument("--imagem", default=None, help="foto usada quando não há câmera (ensaio/demonstração)")
    ap.add_argument("--sem-memoria", action="store_true", help="não guardar instruções e preferências entre conversas (logs/memoria.db)")
    ap.add_argument("--explorar", action="store_true",
                    help="experimental: com o robô parado, combina gestos sozinho (desligado por padrão)")
    ap.add_argument("--sem-registro", action="store_true", help="não gravar logs/sessoes.jsonl (só texto e tempos)")
    ap.add_argument("--run-seconds", type=float, default=0, help=argparse.SUPPRESS)   # só para testes
    parsed = ap.parse_args(argv)
    if parsed.leve is None:
        import platform
        parsed.leve = (os.environ.get("NURIA_LEVE", "").strip() in ("1", "true", "sim")
                       or platform.machine().lower().startswith(("arm", "aarch")))
    if parsed.fps is None:
        parsed.fps = 30 if parsed.leve else 60
    if parsed.leve and parsed.det_width == 416:
        parsed.det_width = 320
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


LOG_DIR = Path(__file__).resolve().parent / "logs"
_FAULT_FILE = None


def install_crash_logs() -> None:
    """Falha nativa (driver, áudio, vídeo) vai para logs/falhas.log; erro em thread, para logs/erros.log."""
    global _FAULT_FILE
    import faulthandler
    import traceback
    try:
        LOG_DIR.mkdir(exist_ok=True)
        if _FAULT_FILE is None:
            _FAULT_FILE = open(LOG_DIR / "falhas.log", "a", encoding="utf-8")
        faulthandler.enable(_FAULT_FILE, all_threads=True)
    except (OSError, RuntimeError, ValueError):
        pass

    def thread_hook(info):
        try:
            with open(LOG_DIR / "erros.log", "a", encoding="utf-8") as fh:
                fh.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} · thread {getattr(info.thread, 'name', '?')} ---\n"
                         + "".join(traceback.format_exception(info.exc_type, info.exc_value, info.exc_traceback)))
        except OSError:
            pass
    threading.excepthook = thread_hook


def talk_level(assistant, state: str) -> float:
    """Volume da fala (0..1) para o corpo 3D. `player.level` é método no StreamPlayer: chamar, nunca converter."""
    if state != "speaking":
        return 0.0
    try:
        level = assistant.speech_level() if hasattr(assistant, "speech_level") else assistant.player.level()
        return max(0.0, min(1.0, float(level) * 3))
    except Exception:
        return 0.0


def main(argv=None) -> int:
    install_crash_logs()
    sys.setswitchinterval(0.001)       # threads de áudio e rede ganham o GIL em 1 ms (padrão: 5 ms) → sem picotes
    args = parse_args(argv)
    import preferences
    features = preferences.load()
    features.update(args.feature_overrides)
    for key, value in features.items():
        setattr(args, key, value)
    if args.leve:
        CameraSource.DETECT_HZ = 10.0           # Raspberry: 10 detecções de rosto por segundo
    try:
        import cv2
        cv2.setNumThreads(2)                   # várias threads de visão x N núcleos cada = CPU saturada
    except Exception:
        pass
    set_dpi_aware()
    pygame.init()
    pygame.display.set_caption("Ayra")
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
    renderer = Renderer(screen, (gw, gh), scheme, glow=not (args.no_glow or args.leve))
    renderer.light = bool(args.leve)
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

    def toast(msg: str) -> None:
        hud.toast, hud.toast_age = msg, 0.0
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
    chat = ChatHistory()
    api_ui = dict(test="Tocar para testar o provedor escolhido", confirm_remove=False)

    def tokens_label(n):
        name = next((label for label, v in credenciais.TOKEN_LIMITS if v == n), f"{n} tokens")
        return name + (f" · até ~{max(1, round(n / 22))} s de fala" if n else "")

    def api_state():
        cfg = credenciais.load()
        gemini = cfg.provider == "gemini"
        hud.api = dict(configured=cfg.configured, masked=credenciais.mask(cfg.api_key) if cfg.configured else "",
                       gemini_configured=cfg.gemini_configured,
                       gmasked=credenciais.mask(cfg.gemini_key) if cfg.gemini_configured else "", provider=cfg.provider,
                       model=cfg.gemini_model if gemini else cfg.realtime_model,
                       voice=cfg.gemini_voice if gemini else cfg.realtime_voice, test=api_ui["test"],
                       tokens=tokens_label(cfg.max_tokens), confirm_remove=api_ui["confirm_remove"],
                       source=credenciais.key_source(name={"gemini": "GEMINI_API_KEY", "eleven": "ELEVENLABS_API_KEY"}.get(key_entry.kind, "OPENAI_API_KEY")),
                       robot_voice=cfg.robot_voice, robot_level=cfg.robot_level,
                       eleven_configured=cfg.eleven_configured, eleven_on=cfg.eleven_on,
                       emasked=credenciais.mask(cfg.eleven_key) if cfg.eleven_configured else "",
                       eleven_test=api_ui.get("eleven_test", "Tocar para ouvir uma frase"))

    def api_reconnect():
        if hasattr(assistant, "warmup") and args.ia == "realtime" and credenciais.load().live_configured:
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
            gemini = cfg.provider == "gemini"
            if name == "key":
                key_entry.open("openai")
            elif name == "gkey":
                key_entry.open("gemini")
            elif name == "ekey":
                key_entry.open("eleven")
            elif name == "evoice":
                credenciais.set_env_values({"NURIA_ELEVEN": "off" if cfg.eleven_on else "on"})
                toast("Voz única ElevenLabs " + ("desligada" if cfg.eleven_on else "ligada"))
            elif name == "etest":
                if not cfg.eleven_configured:
                    api_ui["eleven_test"] = "Cole a chave da ElevenLabs primeiro"
                else:
                    api_ui["eleven_test"] = "Testando…"
                    import eleven

                    def run_eleven_test():
                        ok, msg = eleven.ElevenTTS(cfg.eleven_key, cfg.eleven_voice, cfg.eleven_model).check()
                        api_ui["eleven_test"] = ("OK · " if ok else "Falhou · ") + msg
                        if ok:
                            assistant.operator_say("Olá! Esta é a minha voz.")
                        api_state()
                    threading.Thread(target=run_eleven_test, name="eleven-test", daemon=True).start()
            elif name == "provider":
                credenciais.set_env_values({"NURIA_PROVEDOR": "openai" if gemini else "gemini"})
                api_ui["test"] = "Tocar para testar o provedor escolhido"
                toast("Conversa pelo " + ("OpenAI Realtime" if gemini else "Gemini Live"))
                ai_label.at = -9
                api_reconnect()
            elif name == "test":
                api_test()
            elif name == "model":
                if gemini:
                    credenciais.set_env_values({"NURIA_MODELO_GEMINI": cycle(credenciais.GEMINI_MODELS, cfg.gemini_model)})
                else:
                    credenciais.set_env_values({"NURIA_MODELO_REALTIME": cycle(credenciais.REALTIME_MODELS, cfg.realtime_model)})
                api_reconnect()
            elif name == "voice":
                if gemini:
                    credenciais.set_env_values({"NURIA_VOZ_GEMINI": cycle(credenciais.GEMINI_VOICES, cfg.gemini_voice)})
                else:
                    credenciais.set_env_values({"NURIA_VOZ_REALTIME": cycle(credenciais.REALTIME_VOICES, cfg.realtime_voice)})
                api_reconnect()
            elif name == "tokens":
                values = [v for _, v in credenciais.TOKEN_LIMITS]
                credenciais.set_env_values({"NURIA_MAX_TOKENS": str(cycle(values, cfg.max_tokens))})
                api_reconnect()
            elif name == "robot":
                steps = (0, 25, 50, 75, 100)                       # toque cicla; a sala do operador tem controle fino
                set_robot_voice(steps[(min(range(5), key=lambda i: abs(steps[i] - cfg.robot_level)) + 1) % 5])
            elif name == "remove":
                if api_ui["confirm_remove"]:
                    credenciais.set_env_values({"GEMINI_API_KEY" if gemini else "OPENAI_API_KEY": None})
                    api_ui["confirm_remove"] = False
                    api_ui["test"] = "Chave removida do .env"
                else:
                    api_ui["confirm_remove"] = True
        except OSError as error:
            toast(f"Não foi possível gravar o .env: {error}")
        api_state()

    def set_robot_voice(percent, save=True, quiet=False):
        """Grau da voz robótica, 0–100 %. Vale na hora para a IA e para o operador.
        `quiet=True` (painel do operador): nenhum aviso na tela do robô, para a pessoa não perceber que há um operador."""
        percent = max(0, min(100, int(percent)))
        if save:
            try:
                credenciais.set_env_values({"NURIA_VOZ_ROBOTICA": str(percent)})
            except OSError as error:
                toast(f"Não foi possível gravar o .env: {error}")
        if hasattr(assistant, "robot_level"):
            assistant.robot_level = percent / 100.0
        if save and not quiet:
            toast("Voz robótica: " + (f"{percent} %" if percent else "desligada"))

    def api_save_key(key, kind="openai"):
        name = {"gemini": "GEMINI_API_KEY", "eleven": "ELEVENLABS_API_KEY"}.get(kind, "OPENAI_API_KEY")
        try:
            credenciais.set_env_values({name: key})
        except OSError as error:
            toast(f"Não foi possível gravar o .env: {error}")
            return
        warn = " (a variável do Windows tem prioridade)" if credenciais.key_source(name=name) == "windows" else ""
        toast("Chave salva" + warn)
        ai_label.at = -9                         # o subtítulo da coluna relê o .env já
        if kind == "eleven":
            api_ui["eleven_test"] = "Tocar para ouvir uma frase"
            api_state()
            return
        api_reconnect()
        api_test()

    api_state()

    # ---- áudio full-duplex: 1 microfone, eco do alto-falante cancelado, repartido entre IA e operador ----
    import json as _json
    import audio_duplex
    audio_cfg_path = Path(__file__).resolve().parent / "audio.local.json"
    try:
        audio_cfg = _json.loads(audio_cfg_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        audio_cfg = {}
    hub = audio_duplex.AudioHub(str(audio_cfg.get("eco", "eco")))

    import memoria
    mem_on = bool(audio_cfg.get("memoria", os.environ.get("NURIA_MEMORIA", "on").strip().lower() not in ("off", "0", "nao", "não")))
    mem_on = mem_on and not args.sem_registro and not args.sem_memoria
    memory = memoria.Memory(enabled=mem_on, retention_days=int(os.environ.get("NURIA_MEMORIA_DIAS", "90") or 90))
    forget_armed = {"t": 0.0}

    def audio_save():
        try:
            audio_cfg_path.write_text(_json.dumps({"eco": hub.mode, "memoria": memory.enabled}) + "\n", encoding="utf-8")
        except OSError:
            pass

    rag_path = Path(__file__).resolve().parent / "rag.local.json"

    def rag_read():
        try:
            data = _json.loads(rag_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def rag_toggle(key):
        """Liga/desliga `rag_ligado` ou `rag_voz` em rag.local.json (preserva o resto). O assistente relê o arquivo sozinho."""
        data = rag_read()
        if key == "rag_voz" and data.get("rag_ligado") is not True:
            toast("Ligue o RAG primeiro")
            return
        data[key] = data.get(key) is not True
        if key == "rag_ligado" and not data[key]:
            data["rag_voz"] = False
        try:
            rag_path.write_text(_json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        except OSError as error:
            toast(f"Não foi possível gravar rag.local.json: {error}")
            return
        if key == "rag_ligado":
            toast("RAG " + ("ligado: memória e fichas da Ayra entram quando a pergunta tem relação" if data[key] else "desligado"))
        else:
            toast("RAG por voz " + ("ligado: vale na próxima resposta" if data[key] else "desligado"))

    def audio_state():
        _rag = rag_read()
        hud.audio = dict(rag=_rag.get("rag_ligado") is True, rag_voz=_rag.get("rag_voz") is True, mode=hub.mode, label=audio_duplex.MODE_LABELS[hub.mode],
                         continuous=bool(getattr(state_ref.get("assistant"), "continuous", False)),
                         memory=memory.active, memory_blocked=bool(args.sem_registro or args.sem_memoria),
                         memory_n=len(memory.list_active_instructions()) if memory.active else 0)
    state_ref = {}

    # ---- controle remoto (etapa 2): sala do operador no navegador, por WebRTC ----
    remote_cfg_path = Path(__file__).resolve().parent / "remote.local.json"
    try:
        remote_cfg = _json.loads(remote_cfg_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        remote_cfg = {}
    remote = dict(server=None, enabled=bool(remote_cfg.get("enabled", False)), port=int(remote_cfg.get("port", 8765)),
                  fps=int(remote_cfg.get("fps", 10)) if int(remote_cfg.get("fps", 10)) in (5, 10, 15, 30) else 10,
                  pin=str(remote_cfg.get("pin", "")), badge=bool(remote_cfg.get("badge", True)), faces=0)

    def remote_save():
        try:
            remote_cfg_path.write_text(_json.dumps(dict(enabled=remote["enabled"], port=remote["port"], pin=remote["pin"],
                                                        badge=remote["badge"], fps=remote["fps"]), indent=2) + "\n",
                                       encoding="utf-8")
        except OSError:
            pass

    def remote_frame_info():                  # (quadro, sequência, captura) sem cópia; a trilha descarta repetidos e mede a idade
        if not using_sim and hasattr(src, "latest_frame_info"):
            return src.latest_frame_info()
        _sim_seq[0] += 1                      # simulação/imagem fixa: cada leitura conta como quadro novo
        return remote_frame(), _sim_seq[0], time.perf_counter()
    _sim_seq = [0]

    def remote_on_fps(fps):                   # a câmera só entrega à sala a taxa que ela pede (30 reais precisam disso)
        if hasattr(src, "set_operator_fps"):
            src.set_operator_fps(fps)

    def remote_frame():                       # thread do servidor: lê o último quadro, sem disputar a foto da IA
        if not using_sim and hasattr(src, "latest_frame"):
            return src.latest_frame()
        if args.imagem:
            import cv2
            return cv2.imread(args.imagem)
        return None

    def remote_telemetry():
        s = assistant.snapshot()
        return dict(estado=s.state, modo=getattr(s, "mode", "autonomo"), pergunta=s.question, resposta=s.answer,
                    mensagem=s.message if s.message_kind in ("warning", "error") else "", expressao=LABELS.get(
                        robot.expr.shown_name(), robot.expr.shown_name()), visao=s.vision,
                    voz_robotica=bool(getattr(assistant, "robot_voice", False)),
                    voz_robotica_nivel=int(round(100 * getattr(assistant, "robot_level", 0.0))), rostos=remote["faces"],
                    voz_perfil=getattr(assistant, "voice_profile", "original"),
                    voz_diag=assistant.voice_diag() if hasattr(assistant, "voice_diag") else {},
                    corpo3d=body["on"], gesto=GESTURES.get(body["gesture"], "") if time.perf_counter() - t0 < body["until"] else "",
                    fps=remote["fps"], eco=hub.mode, continua=bool(getattr(assistant, "continuous", False)))

    def remote_start():
        from operador import HubMic, OperatorServer, new_pin
        remote["pin"] = remote["pin"] if (len(remote["pin"]) == 6 and remote["pin"].isdigit()) else new_pin()
        server = OperatorServer(remote_frame, remote_telemetry,
                                lambda pcm: assistant.operator_audio(pcm) if hasattr(assistant, "operator_audio") else None,
                                robot_muted=lambda: assistant.player.pending() > 0 if hasattr(assistant.player, "pending") else False,
                                port=remote["port"], pin=remote["pin"], fps=remote["fps"],
                                mic_factory=lambda: HubMic(hub),
                                frame_info_provider=remote_frame_info,
                                on_fps=remote_on_fps)
        server.on_mic_gain = lambda pct: credenciais.set_env_values({"NURIA_GANHO_MIC_OPERADOR": str(pct)})
        server.memory = memory                    # painel /memoria (mesmo PIN)
        server.rag_provider = lambda: assistant.rag_assembler() if hasattr(assistant, "rag_assembler") else None     # painel /rag (métricas, sem texto)
        if server.start():
            remote["server"] = server
            remote_save()
        else:
            toast(server.status)                  # falhou (porta ocupada...): segue sem operador e NÃO regrava a preferência
            remote["enabled"] = False
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
                          badge=remote["badge"], fps=remote["fps"])

    def remote_action(name):
        if name == "enabled":
            remote["enabled"] = not remote["enabled"]
            remote_start() if remote["enabled"] else (remote_stop(), remote_save())
        elif name == "memory":
            toast(("Abra no navegador: " + remote["server"].url + "/memoria (PIN " + remote["server"].pin + ")")
                  if remote["server"] else "Ligue a sala do operador primeiro")
        elif name == "pin" and remote["server"]:
            remote["pin"] = remote["server"].regenerate_pin()
            remote_save()
        elif name == "kick" and remote["server"]:
            remote["server"].disconnect_operator()
        elif name == "fps":
            remote["fps"] = {5: 10, 10: 15, 15: 30, 30: 5}.get(remote["fps"], 10)
            if remote["server"]:
                remote["server"].fps = remote["fps"]           # vale na hora, sem reconectar
            remote_save()
        elif name == "badge":
            remote["badge"] = not remote["badge"]
            remote_save()
        remote_state()

    def remote_command(kind, data):
        nowt = time.perf_counter() - t0
        if kind == "operator":
            if hasattr(assistant, "set_operator"):
                assistant.set_operator(data)
        elif kind == "mode" and hasattr(assistant, "set_mode"):
            assistant.set_mode(str(data.get("modo", "")))
        elif kind == "memory_note" and hasattr(assistant, "memory_note"):
            assistant.memory_note(str(data.get("texto", "")))
            audio_state()
        elif kind == "instruction" and hasattr(assistant, "operator_instruction"):
            assistant.operator_instruction(str(data.get("texto", "")))
            toast("Instrução registrada")
        elif kind == "say" and hasattr(assistant, "operator_say"):
            assistant.operator_say(str(data.get("texto", "")))
        elif kind == "interrupt":
            assistant.cancel()
            orch.cancel("ai", nowt)                           # a IA para: gestos dela somem, os do operador ficam
        elif kind == "gesture":
            assistant.request_gesture(str(data.get("nome", "")), data.get("lado"), source="operator")
        elif kind == "body":
            set_body(bool(data.get("ligado")))
        elif kind == "vision":
            assistant.set_vision(bool(data.get("ligado")))
        elif kind == "robot_voice":
            if "nivel" in data:
                try:
                    set_robot_voice(float(data["nivel"]), save=bool(data.get("final", True)), quiet=True)
                except (TypeError, ValueError):
                    pass
            else:
                set_robot_voice(60 if data.get("ligado") else 0, quiet=True)
            if data.get("final", True):
                api_state()
        elif kind == "look":
            try:
                gx, gy = float(data.get("gx", 0)), float(data.get("gy", 0))
                robot.attention.look_at(max(-1, min(1, gx)), max(-1, min(1, gy)), 3.0, nowt)
            except (TypeError, ValueError):
                pass
        elif kind == "expression":
            name = str(data.get("nome", ""))
            if name != "auto":
                orch.claim_expression("operator", nowt)       # o operador segura o canal de expressão contra a IA
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
        from voz import MicStream
        if args.ia == "simulada":
            import realtime
            import gemini_live

            def fake_transport(cfg):                      # ensaio sem rede no provedor escolhido no .env
                if cfg.provider == "gemini":
                    return gemini_live.GeminiAdapter(gemini_live.FakeGeminiServer(), cfg.gemini_model,
                                                     cfg.gemini_voice, cfg.max_tokens)
                return realtime.FakeRealtimeServer()
            sim_provider = credenciais.load().provider
            assistant = RealtimeAssistant(still, transport_factory=fake_transport, mic=MicStream(hub=hub),
                                          config_loader=lambda: credenciais.load(environ={
                                              "OPENAI_API_KEY": "simulada", "GEMINI_API_KEY": "simulada"}),
                                          log=not args.sem_registro,
                                          label=("Gemini Live" if sim_provider == "gemini" else "Realtime") + " simulado · sem rede")
        else:
            assistant = RealtimeAssistant(still, log=not args.sem_registro, mic=MicStream(hub=hub))
        assistant.player.on_played = hub.submit_reference   # o que o alto-falante tocou = referência do eco
        if args.ia == "simulada" or credenciais.load().live_configured:
            assistant.warmup()                  # conecta já; a 1ª pergunta não espera o aperto de mão
    assistant.memory = memory                     # no-op se desligada, sem registro ou se o banco falhar
    faces = None
    if (args.ia != "legado" and not args.sem_registro and not args.sem_memoria
            and os.environ.get("NURIA_ROSTOS", "on").strip().lower() not in ("off", "0", "nao", "não")):
        import rostos

        def face_frame():
            if not using_sim and hasattr(src, "latest_frame"):
                return src.latest_frame()
            if args.imagem:
                import cv2
                return cv2.imread(args.imagem)
            return None
        faces = rostos.FaceRecognizer(rostos.FaceBook(), face_frame, on_change=assistant.identity_note)
        if not faces.embedder.available():
            faces.error = "modelo SFace ausente: execute preparar_modelos.bat"     # as ferramentas respondem "indisponível"
        assistant.faces = faces
        faces.start()
    state_ref["assistant"] = assistant
    audio_state()
    assistant_state = "idle"

    def ai_label():
        if args.ia == "simulada":
            return assistant.backend_label()
        if time.perf_counter() - getattr(ai_label, "at", -9) > 2.0:     # relê o .env sem reiniciar
            ai_label.cfg, ai_label.at = credenciais.load(), time.perf_counter()
        cfg = ai_label.cfg
        if args.ia == "legado":
            if not cfg.configured:
                return "Chave da OpenAI ausente · veja .env.example"
            return f"Modo legado (3 chamadas) · {assistant.snapshot().model or cfg.vision_model}"
        if not cfg.live_configured:
            return ("Chave do Gemini ausente · Ajustes → API" if cfg.provider == "gemini"
                    else "Chave da OpenAI ausente · veja .env.example")
        return f"{assistant.backend_label()} · {cfg.live_model}"

    clock = pygame.time.Clock()
    t0 = time.perf_counter()
    last = t0
    fps_avg, slow_frames = 60.0, 0
    speaker_cycle = 0
    last_logged_trigger = 0


    # ---- corpo 3D (Blender -> OpenGL): rosto atual no visor, gestos da IA e do operador ----
    import corpo3d
    from gestos import GESTURES, LIBRARY as GESTURE_LIBRARY, head_gaze
    # gesto visto pela câmera -> gesto devolvido pelo corpo 3D
    SIGN_REPLY = {"middle_finger": "dedo_do_meio", "thumbs_up": "joia", "peace": "paz", "ok_sign": "ok",
                  "wave": "tchau", "palm_hello": "tchau"}
    SIGN_NAMES = {"middle_finger": "dedo do meio", "thumbs_up": "joinha", "peace": "paz e amor", "ok_sign": "OK",
                  "wave": "aceno", "palm_hello": "oi"}
    body_cfg_path = Path(__file__).resolve().parent / "corpo3d.local.json"
    try:
        body_cfg = _json.loads(body_cfg_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        body_cfg = {}
    body = dict(obj=None, on=False, error="", face=None, img=None, last=-1.0, applied=0, gesture="", until=0.0)
    from orquestrador import Explorer, Orchestrator
    orch = Orchestrator()                                    # quem manda em cada canal do corpo (operador > IA > espontâneo)
    explorer = Explorer(orch, enabled=bool(args.explorar))
    assistant.expression_gate = lambda: orch.expression_allowed("ai")
    cancel_seen = [getattr(assistant, "cancel_epoch", 0)]

    def body_state():
        hud.body = dict(enabled=body["on"], error=body["error"], auto=bool(body_cfg.get("qualidade_auto", True)))

    perf = {"t": 0.0, "ms": [], "laps": {}, "mark": time.perf_counter(), "cpu": sum(os.times()[:2]), "thr": {}}

    def lap(name):
        """Cronometra uma etapa do quadro (alimenta logs/desempenho.log)."""
        t_now = time.perf_counter()
        perf["laps"][name] = perf["laps"].get(name, 0.0) + (t_now - perf["mark"]) * 1000
        perf["mark"] = t_now
    use = {"on": False, "corner": None}

    def set_use(on: bool) -> None:
        """Modo de uso: esconde ajustes, diagnóstico e painéis; fica só o robô, o botão de falar e a conversa."""
        use["on"], use["corner"] = on, None
        hud.use_mode, hud.use_exit, hud.hint_age = on, 0.0, 0.0
        panel.simple = on
        panel.visible = True
        if on:
            panel.typing = False
            hud.debug = hud.show_help = False
            renderer.menu.open = False

    def set_body(on: bool, save: bool = True):
        if on and body["obj"] is None:
            ok, why = corpo3d.available()
            if ok:
                try:
                    body["obj"] = corpo3d.Body3D()
                    body["obj"].set_adaptive(body_cfg.get("qualidade_auto", True))   # ligado: começa no máximo e só desce se o desenho passar de 14 ms (iGPU/Raspberry); desligado: sempre o máximo
                    try:
                        LOG_DIR.mkdir(exist_ok=True)
                        with open(LOG_DIR / "desempenho.log", "a", encoding="utf-8") as fh:
                            info = body["obj"].ctx.info
                            fh.write(f"# GPU do 3D: {info.get('GL_RENDERER', '?')} · {info.get('GL_VENDOR', '?')} · OpenGL {info.get('GL_VERSION', '?')[:20]}\n")
                    except Exception:
                        pass
                except Exception as error:                  # sem OpenGL 3.3 / driver antigo
                    ok, why = False, f"OpenGL indisponível ({str(error).splitlines()[0][:60]})"
            if not ok:
                body["error"] = why
                toast("Corpo 3D indisponível: " + why)
                on = False
        body["on"] = bool(on)
        body["img"] = None
        if save:
            try:
                body_cfg["ligado"] = body["on"]
                body_cfg_path.write_text(_json.dumps(body_cfg) + "\n", encoding="utf-8")
            except OSError:
                pass
        body_state()

    def body_cancel(cmd, now: float):
        """Cancela (com saída suave) os gestos de uma ordem `cancel` do orquestrador."""
        if body["on"] and body["obj"] is not None:
            arms = tuple(a for a, ch in (("L", "arm_l"), ("R", "arm_r")) if ch in cmd.channels)
            body["obj"].cancel(now, arms or None, head="head" in cmd.channels or not arms)
        if body.get("head2d") == cmd.name:
            body["head2d"] = None

    def body_gesture(name: str, now: float, side: str = ""):
        body["gesture"], body["until"] = name, now + GESTURE_LIBRARY[name].duration
        if body["on"] and body["obj"] is not None:
            body["obj"].play(name, now, side or "right")
        elif name in ("sim", "nao"):
            body["head2d"], body["head2d_t0"] = name, now      # rosto 2D (e painel LED): o olhar faz o sim/não
        else:
            toast(f"Gesto: {GESTURES[name]} (aparece com o corpo 3D ligado · F3)")

    def draw_body(frame, now, dt):
        sw, sh = screen.get_size()
        area = pygame.Rect(0, 0, max(16, sw - renderer.reserve_right), sh)
        if body["face"] is None:
            body["face"] = Renderer(pygame.Surface((640, 360)), (gw, gh), theme.SCHEMES["dark"], glow=True)
        fr = body["face"]
        obj = body["obj"]
        period = (1 / 20 if args.leve else 1 / 30) * (1.25 if obj.q >= 2 else 1.0)    # 3D mais leve = menos quadros
        if body["img"] is None or body["img"].get_size() != area.size or now - body["last"] >= period:
            body["last"] = now
            fr.glow_on = obj.q == 0 and not args.leve        # o rosto do visor só é desenhado quando o 3D redesenha
            fr.draw_panel(frame, dt, reduce_motion)
            k = min(1.0, 1280 / area.w)                     # placa de vídeo fraca: desenha menor e amplia
            size = (max(16, int(area.w * k)), max(16, int(area.h * k)))
            talk = talk_level(assistant, assistant_state)
            try:
                img = body["obj"].render(size, fr.screen.subsurface(fr.panel), now, frame.gaze, talk,
                                         renderer.scheme.bg, reduce_motion)
            except Exception as error:
                body["error"] = f"falha no desenho 3D ({str(error)[:60]})"
                toast("Corpo 3D desligado: " + body["error"])
                set_body(False)
                renderer.draw_panel(frame, dt, reduce_motion)
                return
            body["img"] = img if img.get_size() == area.size else pygame.transform.smoothscale(img, area.size)
        screen.fill(renderer.scheme.bg)
        screen.blit(body["img"], area)

    if body_cfg.get("ligado"):
        set_body(True, save=False)
    body_state()

    error_times = []

    def report_error(error) -> bool:
        """Grava o traceback em logs/erros.log e mostra um aviso. False = erros demais seguidos (encerra)."""
        import traceback
        now_e = time.perf_counter()
        error_times[:] = [x for x in error_times if now_e - x < 5.0] + [now_e]
        try:
            LOG_DIR.mkdir(exist_ok=True)
            with open(LOG_DIR / "erros.log", "a", encoding="utf-8") as fh:
                fh.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n{traceback.format_exc()}")
        except OSError:
            pass
        toast(f"Erro interno ({type(error).__name__}) registrado em logs/erros.log; o app segue")
        return len(error_times) <= 30

    if args.uso:
        set_use(True)
    running = True
    while running:
        try:
            clock.tick(args.fps)
            perf["mark"] = time.perf_counter()
            now_abs = time.perf_counter()
            elapsed = now_abs - last
            dt = min(elapsed, 0.05)
            last = now_abs
            now = now_abs - t0
            if args.run_seconds and now > args.run_seconds:
                running = False
            hud.hint_age += dt
            if use["corner"] is not None:
                hud.use_exit = min(1.0, (now - use["corner"]) / 1.2)
                if hud.use_exit >= 1.0:
                    set_use(False)
            hud.active_actions = tuple(robot.actions.active())
            hud.toast_age += dt

            for ev in pygame.event.get():
                if use["on"] and ev.type not in (pygame.QUIT, pygame.VIDEORESIZE):
                    # Modo de uso: só falar com o robô. F10 ou toque longo no canto superior direito saem.
                    corner = pygame.Rect(renderer.screen.get_width() - int(96 * renderer.s), 0,
                                         int(96 * renderer.s), int(96 * renderer.s))
                    if ev.type == pygame.KEYDOWN and ev.key == pygame.K_F10:
                        set_use(False)
                    elif ev.type == pygame.KEYDOWN and ev.key == pygame.K_F11:
                        fullscreen = not fullscreen
                        hud.fullscreen = fullscreen
                        screen = open_window()
                        relayout(screen)
                    elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1 and corner.collidepoint(ev.pos):
                        use["corner"] = now
                    elif ev.type == pygame.MOUSEBUTTONUP:
                        use["corner"], hud.use_exit = None, 0.0
                        panel.handle_event(ev, assistant)
                    elif ev.type != pygame.KEYDOWN or ev.key != pygame.K_ESCAPE or assistant.snapshot().state not in ("idle", "error"):
                        panel.handle_event(ev, assistant)      # Esc só interrompe a resposta; nunca fecha o app
                    continue
                if key_entry.active and ev.type not in (pygame.QUIT, pygame.VIDEORESIZE):
                    result = key_entry.handle_event(ev)
                    if result and result[0] == "save":
                        api_save_key(result[1], key_entry.kind)
                    continue
                if chat.active and ev.type not in (pygame.QUIT, pygame.VIDEORESIZE):
                    result = chat.handle_event(ev)
                    if result and result[0] == "new":
                        assistant.new_conversation()
                        toast("Nova conversa")
                    elif result and result[0] == "resume":
                        n = assistant.resume_session(result[1])
                        toast(f"Conversa retomada ({n} trocas)" if n else "Conversa vazia")
                    elif result and result[0] == "mem_note":
                        if result[1] and hasattr(assistant, "memory_note"):
                            assistant.memory_note(result[1])
                        toast("Memória atualizada")
                        audio_state()
                    elif result and result[0] == "copy":
                        md = historico.to_markdown(result[1])
                        if not md:
                            toast("Conversa vazia: nada para copiar")
                        else:
                            saved = ""
                            try:
                                out_dir = LOG_DIR / "exportacoes"
                                out_dir.mkdir(parents=True, exist_ok=True)
                                (out_dir / f"conversa_{result[1]}.md").write_text(md, encoding="utf-8")
                                saved = f"logs/exportacoes/conversa_{result[1]}.md"
                            except OSError:
                                pass
                            copied = False
                            try:
                                pygame.scrap.init()
                                pygame.scrap.put_text(md)
                                copied = True
                            except Exception:
                                pass
                            toast("Conversa copiada (.md)" + (f" e salva em {saved}" if saved else "") if copied else
                                  (f"Não consegui copiar; salva em {saved}" if saved else "Não foi possível copiar nem salvar"))
                    elif result and result[0] == "delete":
                        if result[1] == getattr(assistant, "session_id", None):
                            assistant.new_conversation()
                        historico.delete(result[1])
                        memory.forget_session(result[1])
                        toast("Conversa apagada")
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
                if action == "use":
                    set_use(True)
                    continue
                if action == "history":
                    chat.open(getattr(assistant, "session_id", ""), memory=memory)
                    continue
                if action and action.startswith("remote:"):
                    remote_action(action.split(":", 1)[1])
                    continue
                if action and action.startswith("audio:"):
                    name = action.split(":", 1)[1]
                    if name == "continuous" and hasattr(assistant, "set_continuous"):
                        assistant.set_continuous(not assistant.continuous)
                        toast("Conversa contínua " + ("ligada: fale quando quiser" if assistant.continuous else "desligada"))
                    elif name == "memory":
                        if args.sem_registro or args.sem_memoria:
                            toast("Memória bloqueada por --sem-registro/--sem-memoria")
                        else:
                            memory.enabled = not memory.enabled
                            if memory.enabled and memory._conn is None:
                                memory.healthy = True
                                memory._init_db()
                            audio_save()
                            toast("Memória " + ("ligada: instruções e preferências ficam salvas" if memory.active else "desligada"))
                    elif name == "rag":
                        rag_toggle("rag_ligado")
                    elif name == "ragvoz":
                        rag_toggle("rag_voz")
                    elif name == "forget":
                        if time.perf_counter() - forget_armed["t"] < 6.0:
                            forget_armed["t"] = 0.0
                            memory.forget_all()
                            toast("Memória apagada")
                        else:
                            forget_armed["t"] = time.perf_counter()
                            toast("Toque de novo em 6 s para apagar instruções, preferências e histórico da memória")
                    elif name == "eco":
                        order = audio_duplex.MODES
                        hub.set_mode(order[(order.index(hub.mode) + 1) % len(order)])
                        audio_save()
                        toast("Áudio: " + audio_duplex.MODE_LABELS[hub.mode])
                    audio_state()
                    continue
                if action and action.startswith("body:"):
                    if action == "body:enabled":
                        set_body(not body["on"])
                    elif action == "body:quality":
                        body_cfg["qualidade_auto"] = not body_cfg.get("qualidade_auto", True)
                        if body["obj"] is not None:
                            body["obj"].set_adaptive(body_cfg["qualidade_auto"])
                        try:
                            body_cfg_path.write_text(_json.dumps(body_cfg) + "\n", encoding="utf-8")
                        except OSError:
                            pass
                        toast("Qualidade 3D: " + ("automática" if body_cfg["qualidade_auto"] else "sempre no máximo"))
                        body_state()
                    else:
                        assistant.request_gesture(action.split(":", 2)[2], source="operator")
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
                    elif k == pygame.K_F3:
                        set_body(not body["on"])
                        if body["on"]:
                            toast("Corpo 3D ligado · F3 volta ao rosto 2D")
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

            lap("eventos")
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
            gsnap = assistant.snapshot()
            orch.set_mode(gsnap.mode, now)
            if getattr(assistant, "cancel_epoch", 0) != cancel_seen[0]:
                cancel_seen[0] = assistant.cancel_epoch
                orch.cancel("ai", now)                        # cancelamento/barge-in/teleoperado: nada da IA fica no corpo
            for g_name, g_side, g_src in assistant.pop_gesture_requests():
                orch.submit_gesture(g_src, g_name, now, side=g_side)
            orch.update(now)
            explorer.tick(now, can_run=gsnap.state == "idle")
            for cmd in orch.drain():
                if cmd.kind == "play" and cmd.name in GESTURES:
                    body_gesture(cmd.name, now, cmd.side)
                elif cmd.kind == "cancel":
                    body_cancel(cmd, now)
            if body.get("head2d"):
                look = head_gaze(body["head2d"], now - body["head2d_t0"])
                if look is None or now - body["head2d_t0"] > GESTURE_LIBRARY[body["head2d"]].duration:
                    body["head2d"] = None
                else:
                    robot.attention.look_at(look[0], look[1], .25, now)
            lap("entrada")
            frame = robot.step(dets, now, dt)
            lap("robo")
            lt = robot.last_trigger
            if lt and lt["sequence"] != body.get("seen_sign"):
                body["seen_sign"] = lt["sequence"]               # gesto visto pela câmera (local, sem IA)
                reply = SIGN_REPLY.get(lt["kind"])
                if reply and (lt["kind"] not in ("wave", "palm_hello") or features.get("gesture_reply", True)):
                    if body["on"]:
                        assistant.request_gesture(reply)
                        toast(f"Viu {SIGN_NAMES[lt['kind']]} · respondeu com {GESTURES[reply].lower()}")
                    elif lt["kind"] not in ("wave", "palm_hello"):
                        toast(f"Viu {SIGN_NAMES[lt['kind']]} · ligue o corpo 3D (F3) para ela devolver o gesto")
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

            lap("resto")
            # ---- desenho ---------------------------------------------------------
            fps_avg = 0.95 * fps_avg + 0.05 * (1.0 / max(elapsed, 1e-3))
            hud.fps = fps_avg
            perf["ms"].append(elapsed * 1000)
            if now - perf["t"] >= 10.0:                      # logs/desempenho.log: uma linha a cada 10 s (para medir no PC real)
                ms = sorted(perf["ms"]); n = len(ms)
                o3 = body["obj"] if body["on"] and body["obj"] is not None else None
                try:
                    LOG_DIR.mkdir(exist_ok=True)
                    with open(LOG_DIR / "desempenho.log", "a", encoding="utf-8") as fh:
                        fh.write(f"{time.strftime('%H:%M:%S')} fps={n / max(1e-3, now - perf['t']):.1f} "
                                 f"quadro_p50={ms[n // 2]:.1f}ms p95={ms[int(n * .95)]:.1f}ms max={ms[-1]:.1f}ms "
                                 f"3d={'%.1fms q%d x%.2f' % (o3.render_ms, o3.q, o3.scale) if o3 else 'off'} "
                                 f"operador={'sim' if remote.get('operator') else 'nao'} leve={'sim' if args.leve else 'nao'} "
                                 f"cpu={100 * (sum(os.times()[:2]) - perf['cpu']) / max(1e-3, now - perf['t']):.0f}% "
                                 f"etapas_ms[{' '.join('%s=%.1f' % (k, v / n) for k, v in perf['laps'].items())}]\n")
                except OSError:
                    pass
                try:                                         # quem está gastando CPU (identifica a thread culpada)
                    cur = thread_cpu()
                    top = sorted(((cur[k] - perf["thr"].get(k, 0.0), k) for k in cur), reverse=True)[:5]
                    if cur:
                        with open(LOG_DIR / "desempenho.log", "a", encoding="utf-8") as fh:
                            fh.write("    threads: " + " ".join("%s=%.0f%%" % (thread_name(k), 100 * d / max(1e-3, now - perf["t"]))
                                                               for d, k in top if d > 0.01) + "\n")
                    perf["thr"] = cur
                except Exception:
                    pass
                perf["t"], perf["ms"], perf["laps"], perf["cpu"] = now, [], {}, sum(os.times()[:2])
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
            if body["on"]:
                draw_body(frame, now, dt)
            else:
                renderer.draw_panel(frame, dt, reduce_motion)
            lap("cena")
            panel.live = src.preview() if camera_ready and hasattr(src, "preview") else (None, 0)
            panel.draw(renderer, assistant, dt, reduce_motion, ai_label())
            lap("painel")
            renderer.draw_hud(frame, hud)
            key_entry.draw(renderer, hud.api.get("source", ""))
            chat.draw(renderer, getattr(assistant, "session_id", ""))
            lap("hud")
            pygame.display.flip()
            lap("flip")
            metrics.presented(time.perf_counter())

            # qualidade adaptativa: se estiver pesado, desliga o brilho
            slow_frames = slow_frames + 1 if elapsed > 1.6 / max(1, args.fps) else 0
            if slow_frames > 90 and renderer.glow_on:
                renderer.glow_on = False
                toast("Brilho desligado para manter a fluidez")
                slow_frames = 0
        except Exception as error:               # nunca fecha "do nada": registra, avisa e segue
            if not report_error(error):
                raise

    assistant.shutdown()
    if faces is not None:
        faces.stop()
    memory.flush(1.0)
    memory.close()
    led_link.stop()
    remote_stop()
    hub.close()
    if body["obj"] is not None:
        body["obj"].release()
    if hasattr(src, "stop"):
        src.stop()
    pygame.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
