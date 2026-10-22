"""Orquestra a pergunta da Nuria: ouvir → capturar imagem → transcrever → interpretar → falar.

Tudo roda numa thread de trabalho: o laço da interface só lê o estado (RNF03).
Nenhuma falha encerra o app: cada etapa vira um estado de erro/aviso com mensagem (RNF02).
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import credenciais
import ia
from voz import Player, Recorder, VoiceError

STATES = ("idle", "connecting", "listening", "capturing", "transcribing", "thinking", "speaking", "error")
BUSY = ("capturing", "transcribing", "thinking")
LOG_DIR = Path(__file__).resolve().parent / "logs"


@dataclass
class Snapshot:
    state: str = "idle"
    question: str = ""
    answer: str = ""
    emotion: str = "neutral"
    action: str = ""                 # simulação pedida pela IA: cry, plead, wink
    expr_version: int = 0            # muda quando a IA pede nova expressão (tempo real)
    vision: bool = True              # False = visão pausada: perguntas vão sem imagem
    mode: str = "autonomo"           # autonomo | assistido | teleoperado (controle remoto, etapa 2)
    operator: str = ""               # operador remoto conectado ("" = nenhum)
    image: object = None             # BGR da última pergunta (só em memória)
    image_version: int = 0
    message: str = ""                # aviso/erro em texto
    message_kind: str = ""           # "" | warning | error | done
    timings: dict = field(default_factory=dict)
    backend: str = ""
    model: str = ""
    level: float = 0.0               # medidor do microfone 0..1
    spoken_offline: bool = False


class Assistant:
    def __init__(self, still: Callable[[], object], backend_factory: Optional[Callable[[], object]] = None,
                 recorder: Optional[Recorder] = None, player: Optional[Player] = None,
                 offline_tts: Callable[[str], bool] = ia.offline_say, log: bool = True):
        self._still = still
        self._factory = backend_factory or self._default_backend
        self._backend = None
        self.recorder = recorder or Recorder()
        self.player = player or Player()
        self._offline_tts = offline_tts
        self._log = log
        self._lock = threading.Lock()
        self._snap = Snapshot()
        self._gen = 0
        self._stop_listen = threading.Event()
        self._stop_speak = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.history: List[Tuple[str, str]] = []
        self.state_since = time.perf_counter()
        self.offline_speaking = False

    # ---- leitura pela interface --------------------------------------------
    def snapshot(self) -> Snapshot:
        with self._lock:
            s = Snapshot(**self._snap.__dict__)
        s.level = self.recorder.level if s.state == "listening" else 0.0
        return s

    @property
    def state(self) -> str:
        return self._snap.state

    def set_vision(self, on: bool) -> None:
        """Pausa/retoma o envio de imagens (o rastreamento local do rosto continua)."""
        self._set(vision=bool(on))

    def busy(self) -> bool:
        return self._snap.state in BUSY

    def speech_level(self) -> float:
        return self.player.level() if self._snap.state == "speaking" else 0.0

    def backend_label(self) -> str:
        if self._backend is not None:
            return self._backend.name
        cfg = credenciais.load()
        return "OpenAI" if cfg.configured else "chave ausente"

    # ---- comandos -------------------------------------------------------------
    def press_voice(self) -> str:
        """Botão/tecla de falar: inicia; durante a escuta, envia; durante a fala, interrompe e escuta (barge-in)."""
        st = self._snap.state
        if st == "listening":
            self._stop_listen.set()
            return "send"
        if st in BUSY:
            return "busy"
        if st == "speaking":
            self._stop_speak.set()
            self._join()
        self._start(self._voice_flow, "listening")
        return "listen"

    def ask_text(self, text: str) -> bool:
        text = " ".join((text or "").split())
        if not text or self._snap.state in BUSY or self._snap.state == "listening":
            return False
        if self._snap.state == "speaking":
            self._stop_speak.set()
            self._join()
        self._start(lambda gen: self._text_flow(gen, text), "capturing")
        return True

    def cancel(self) -> None:
        with self._lock:
            self._gen += 1
        self._stop_listen.set()
        self._stop_speak.set()
        self._set(state="idle", message="Cancelado.", message_kind="warning")

    def shutdown(self) -> None:
        self.cancel()
        self._join(1.0)

    # ---- internos ----------------------------------------------------------
    def _join(self, timeout: float = 0.6) -> None:
        if self._thread and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout)

    def _start(self, flow, first_state: str) -> None:
        with self._lock:
            self._gen += 1
            gen = self._gen
        # feedback imediato: o estado muda no mesmo quadro do clique/tecla
        self._set(state=first_state, message="", message_kind="", timings={})
        self._stop_listen.clear()
        self._stop_speak.clear()
        self._thread = threading.Thread(target=self._guard, args=(flow, gen), name="nuria", daemon=True)
        self._thread.start()

    def _alive(self, gen: int) -> bool:
        return gen == self._gen

    def _set(self, gen: Optional[int] = None, **kw) -> bool:
        with self._lock:
            if gen is not None and gen != self._gen:
                return False
            if "state" in kw and kw["state"] != self._snap.state:
                self.state_since = time.perf_counter()
            for k, v in kw.items():
                setattr(self._snap, k, v)
        return True

    def _default_backend(self):
        return ia.OpenAIBackend(credenciais.load())

    def _get_backend(self):
        if self._backend is None:
            self._backend = self._factory()           # recria até dar certo: corrigir .env sem reiniciar
        return self._backend

    def _guard(self, flow, gen) -> None:
        try:
            flow(gen)
        except ia.AIError as error:
            if error.kind == "auth":
                self._backend = None
            self._fail(gen, str(error))
        except VoiceError as error:
            self._fail(gen, str(error) + " Você pode digitar a pergunta (Enter).")
        except Exception as error:                     # nunca derruba a interface
            self._fail(gen, f"Erro inesperado: {error}")

    def _fail(self, gen, message: str) -> None:
        self._set(gen, state="error", message=message, message_kind="error")
        self._write_log(dict(erro=message))

    def _capture(self, gen):
        self._set(gen, state="capturing", message="", message_kind="")
        image = self._still()
        if image is None:
            raise ia.AIError("camera", "Câmera indisponível: não consegui capturar a imagem. Verifique a webcam ou retome a câmera (P).")
        with self._lock:
            if gen == self._gen:
                self._snap.image = image
                self._snap.image_version += 1
        return image

    def _voice_flow(self, gen: int) -> None:
        t0 = time.perf_counter()
        self._set(gen, state="listening", message="", message_kind="", timings={})
        wav = self.recorder.record(self._stop_listen)
        if not self._alive(gen):
            return
        if wav is None:
            self._set(gen, state="idle", message="Não ouvi nada. Fale mais perto do microfone ou digite a pergunta (Enter).",
                      message_kind="warning")
            return
        image = self._capture(gen)
        backend = self._get_backend()
        self._set(gen, state="transcribing", backend=backend.name)
        t1 = time.perf_counter()
        question = backend.transcribe(wav)
        stt = time.perf_counter() - t1
        if not self._alive(gen):
            return
        if not question:
            self._set(gen, state="idle", message="Não entendi a pergunta. Tente de novo ou digite (Enter).",
                      message_kind="warning")
            return
        self._answer(gen, question, image, dict(escuta=t1 - t0, stt=stt), t0)

    def _text_flow(self, gen: int, question: str) -> None:
        t0 = time.perf_counter()
        self._set(gen, timings={})
        image = self._capture(gen)
        self._answer(gen, question, image, {}, t0)

    def _answer(self, gen, question, image, timings, t0) -> None:
        backend = self._get_backend()
        self._set(gen, state="thinking", question=question, answer="", action="", backend=backend.name)
        t1 = time.perf_counter()
        result = backend.answer(ia.encode_jpeg(image), question, self.history)
        timings["ia"] = time.perf_counter() - t1
        if not self._alive(gen):
            return
        self.history = (self.history + [(question, result.text)])[-6:]
        self._set(gen, answer=result.text, emotion=result.emotion, model=result.model, action=result.action)
        t2 = time.perf_counter()
        samples, warning = None, ""
        try:
            audio = backend.speak(result.text, result.emotion)
            samples = audio
        except ia.AIError as error:
            warning = f"Voz online indisponível ({error})"
        timings["voz"] = time.perf_counter() - t2
        timings["total_ate_falar"] = time.perf_counter() - t0
        if not self._alive(gen):
            return
        self._set(gen, state="speaking", timings=dict(timings), spoken_offline=samples is None)
        try:
            if samples is not None:
                self.player.play(samples[0], samples[1], self._stop_speak)
            else:
                self.offline_speaking = True
                if not self._offline_tts(result.text):
                    warning = (warning + "; " if warning else "") + "sem voz disponível, resposta só em texto"
        except VoiceError as error:
            warning = str(error)
        finally:
            self.offline_speaking = False
        self._write_log(dict(pergunta=question, resposta=result.text, emocao=result.emotion, acao=result.action, modelo=result.model,
                             tempos={k: round(v, 3) for k, v in timings.items()}, aviso=warning))
        if warning:
            self._set(gen, state="idle", message=warning[:1].upper() + warning[1:] + ".", message_kind="warning")
        else:
            self._set(gen, state="idle", message="", message_kind="done")

    def _write_log(self, entry: dict) -> None:
        """Registro local de testes: só texto e tempos, nunca imagem nem chave."""
        if not self._log:
            return
        try:
            LOG_DIR.mkdir(exist_ok=True)
            entry = dict(quando=time.strftime("%Y-%m-%d %H:%M:%S"), backend=self._snap.backend, **entry)
            with open(LOG_DIR / "sessoes.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass

    # ---- rosto ---------------------------------------------------------------
    def drive_robot(self, robot, now: float, prev_state: str) -> str:
        """Traduz o estado em comportamento do rosto: ouvir, pensar (olhar para cima), falar (boca)."""
        snap_state = self._snap.state
        if snap_state != prev_state:
            if snap_state == "listening":
                robot.expr.pulse("wide", 0.5)
            elif snap_state == "speaking":
                self._apply_expression(robot)
                if self.offline_speaking or self._snap.spoken_offline:
                    robot.play("speak", 30.0)
            elif snap_state == "error":
                robot.expr.pulse("sad", 1.4)
            if prev_state == "speaking":
                robot.stop("speak")
                robot.stop("cry")
                robot.stop("plead")
                if getattr(self, "_held_mood", False):
                    robot.set_mood("auto")
                    self._held_mood = False
        elif snap_state == "speaking" and self._snap.expr_version != getattr(self, "_applied_expr", 0):
            self._apply_expression(robot)                 # expressão chegou no meio da fala (tempo real)
        if snap_state in ("transcribing", "thinking", "capturing", "connecting"):
            robot.attention.look_at(-0.38, -0.42, 0.25, now)      # olhar "pensativo"
        elif snap_state == "speaking" and not self._snap.spoken_offline:
            robot.set_speech_level(self.player.level())
        return snap_state

    def _apply_expression(self, robot) -> None:
        """Emoção da resposta fica fixa até o fim da fala (reações rápidas não a apagam);
        só se o humor estava em automático ou foi fixado por nós (não desfaz as teclas 1–8)."""
        self._applied_expr = self._snap.expr_version
        action = self._snap.action
        emotion = "sad" if action == "cry" else self._snap.emotion
        ours = getattr(self, "_held_mood", False)
        if emotion != "neutral" and (robot.expr.mood == "auto" or ours):
            robot.set_mood(emotion)
            self._held_mood = True
        elif emotion == "neutral" and ours:
            robot.set_mood("auto")
            self._held_mood = False
        if action in ("cry", "plead"):
            robot.play(action, 30.0)               # simulação nomeada, encerrada com a fala
        elif action == "wink":
            robot.wink()
