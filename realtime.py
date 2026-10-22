"""Cliente da OpenAI Realtime API (GA) por WebSocket, fora do laço da interface.

Uma thread lê eventos do servidor; outra envia (fila única = ordem garantida: a imagem sempre entra
na sessão antes do `response.create`). O transporte é injetável: `WebSocketTransport` (real) ou
`FakeRealtimeServer` (simulação/testes, sem rede). Áudio: PCM 16 bits mono a 24 kHz nos dois sentidos.
"""
from __future__ import annotations

import base64
import json
import math
import queue
import threading
import time
from typing import Callable, Optional

import numpy as np

URL = "wss://api.openai.com/v1/realtime?model={model}"
RATE = 24000


class TransportClosed(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Transportes
# ---------------------------------------------------------------------------
class WebSocketTransport:
    def __init__(self, url: str, api_key: str, timeout: float = 10.0):
        try:
            from websockets.sync.client import connect
        except ImportError as error:
            raise TransportClosed("Pacote 'websockets' ausente: pip install -r requirements.txt") from error
        try:
            self._ws = connect(url, additional_headers={"Authorization": f"Bearer {api_key}"},
                               open_timeout=timeout, max_size=None, ping_interval=20, ping_timeout=20)
        except Exception as error:
            raise TransportClosed(_describe_connect_error(error)) from error

    def send(self, text: str) -> None:
        try:
            self._ws.send(text)
        except Exception as error:
            raise TransportClosed(f"Conexão perdida ao enviar: {error}") from error

    def recv(self) -> str:
        try:
            return self._ws.recv()
        except Exception as error:
            raise TransportClosed(f"Conexão encerrada: {error}") from error

    def close(self) -> None:
        try:
            self._ws.close()
        except Exception:
            pass


def _describe_connect_error(error: Exception) -> str:
    text = str(error)
    status = getattr(getattr(error, "response", None), "status_code", None)
    if status == 401 or "401" in text:
        return "Chave da OpenAI inválida ou revogada. Confira o arquivo .env."
    if status == 403 or "403" in text:
        return "A chave não tem acesso ao modelo Realtime configurado."
    if status == 404 or "404" in text:
        return "Modelo Realtime não encontrado nesta conta (NURIA_MODELO_REALTIME)."
    if status == 429 or "429" in text:
        return "Limite ou créditos da API esgotados."
    if isinstance(error, (OSError, TimeoutError)) or "timed out" in text.lower():
        return "Sem conexão com a OpenAI. Verifique a internet."
    return f"Não foi possível conectar à Realtime: {text}"


class FakeRealtimeServer:
    """Servidor Realtime simulado (mesmos eventos GA). Fala uma resposta sintética em áudio."""

    def __init__(self, latency: float = 0.05, speed: float = 4.0, fail_connect: bool = False):
        if fail_connect:
            raise TransportClosed("Sem conexão com a OpenAI. Verifique a internet.")
        self._out: "queue.Queue[Optional[str]]" = queue.Queue()
        self.received: list = []
        self.latency, self.speed = latency, speed
        self._closed = False
        self._cancel = threading.Event()
        self._seq = 0
        self._audio_items: dict = {}
        self._pending_text = ""
        self._has_image = False
        self._image_mean = None
        self._emit({"type": "session.created", "session": {"type": "realtime"}})

    # -- API do transporte --
    def send(self, text: str) -> None:
        if self._closed:
            raise TransportClosed("Conexão encerrada")
        event = json.loads(text)
        self.received.append(event)
        kind = event.get("type")
        if kind == "session.update":
            self._emit({"type": "session.updated", "session": event.get("session", {})})
        elif kind == "input_audio_buffer.commit":
            item = self._id("item")
            self._emit({"type": "input_audio_buffer.committed", "item_id": item})
            self._emit({"type": "conversation.item.input_audio_transcription.completed", "item_id": item,
                        "content_index": 0, "transcript": "O que você está vendo?"})
            self._pending_text = "O que você está vendo?"
        elif kind == "conversation.item.create":
            item = event.get("item", {})
            parts = item.get("content", []) or []
            with_image = any(p.get("type") == "input_image" for p in parts)
            for part in parts:
                if part.get("type") == "input_text" and not with_image:     # legenda da foto não é a pergunta
                    self._pending_text = part.get("text", "")
                if part.get("type") == "input_image":
                    self._has_image = True
                    self._image_mean = _image_mean(part.get("image_url", ""))
            self._emit({"type": "conversation.item.added", "item": dict(item, id=item.get("id") or self._id("item"))})
        elif kind == "response.create":
            self._cancel = threading.Event()              # cada resposta tem o próprio cancelamento
            threading.Thread(target=self._respond, args=(event, self._cancel), daemon=True).start()
        elif kind == "response.cancel":
            self._cancel.set()
        elif kind == "conversation.item.delete":
            self._emit({"type": "conversation.item.deleted", "item_id": event.get("item_id")})
        elif kind == "conversation.item.truncate":
            self._emit({"type": "conversation.item.truncated", "item_id": event.get("item_id"),
                        "content_index": 0, "audio_end_ms": event.get("audio_end_ms")})

    def recv(self) -> str:
        msg = self._out.get()
        if msg is None:
            raise TransportClosed("Conexão encerrada")
        return msg

    def close(self) -> None:
        self._closed = True
        self._out.put(None)

    def drop(self) -> None:
        """Simula queda da rede."""
        self._closed = True
        self._out.put(None)

    # -- internos --
    def _id(self, prefix):
        self._seq += 1
        return f"{prefix}_{self._seq}"

    def _emit(self, event):
        if not self._closed:
            self._out.put(json.dumps(event))

    def _respond(self, request, cancel):
        rid, mid, cid = self._id("resp"), self._id("item"), self._id("call")
        time.sleep(self.latency)
        self._emit({"type": "response.created", "response": {"id": rid, "status": "in_progress"}})
        q = self._pending_text.lower()
        outputs = []
        already_called = any(e.get("type") == "conversation.item.create"
                             and e.get("item", {}).get("type") == "function_call_output" for e in self.received[-3:])
        if ("chor" in q or "brav" in q) and not already_called:
            args = json.dumps({"emocao": "sad" if "chor" in q else "angry", "acao": "chorar" if "chor" in q else "nenhuma"})
            self._emit({"type": "response.output_item.added", "response_id": rid,
                        "item": {"id": cid, "type": "function_call", "name": "expressar", "call_id": cid}})
            self._emit({"type": "response.function_call_arguments.done", "response_id": rid, "item_id": cid,
                        "call_id": cid, "name": "expressar", "arguments": args})
            self._emit({"type": "response.done", "response": {"id": rid, "status": "completed", "output": [
                {"id": cid, "type": "function_call", "name": "expressar", "call_id": cid, "arguments": args}]}})
            return
        if self._image_mean is not None:
            light = "bem iluminada" if self._image_mean > 110 else "meio escura"
            text = f"Modo simulado, sem IA de verdade: a cena está {light}. Prometo ser mais esperta com a chave configurada!"
        else:
            text = "Modo simulado: não recebi imagem."
        self._emit({"type": "response.output_item.added", "response_id": rid,
                    "item": {"id": mid, "type": "message", "role": "assistant"}})
        words = text.split(" ")
        samples_per_word = int(RATE * 0.22)
        sent = 0
        for i, word in enumerate(words):
            if cancel.is_set() or self._closed:
                self._emit({"type": "response.done", "response": {"id": rid, "status": "cancelled", "output": []}})
                return
            self._emit({"type": "response.output_audio_transcript.delta", "response_id": rid, "item_id": mid,
                        "content_index": 0, "delta": (" " if i else "") + word})
            t = np.arange(samples_per_word) / RATE
            env = np.sin(np.pi * np.arange(samples_per_word) / samples_per_word)
            pcm = (np.sin(2 * np.pi * 180 * t) * env * 6000).astype(np.int16)
            self._emit({"type": "response.output_audio.delta", "response_id": rid, "item_id": mid, "output_index": 0,
                        "content_index": 0, "delta": base64.b64encode(pcm.tobytes()).decode()})
            sent += samples_per_word
            time.sleep(0.22 / self.speed)
        self._audio_items[mid] = sent
        self._emit({"type": "response.output_audio_transcript.done", "item_id": mid, "transcript": text})
        self._emit({"type": "response.output_audio.done", "item_id": mid})
        outputs.append({"id": mid, "type": "message", "role": "assistant",
                        "content": [{"type": "output_audio", "transcript": text}]})
        self._emit({"type": "response.done", "response": {"id": rid, "status": "completed", "output": outputs}})


def _image_mean(url: str):
    try:
        import cv2
        data = base64.b64decode(url.split(",", 1)[1])
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        return float(img.mean())
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Sessão
# ---------------------------------------------------------------------------
class RealtimeClient:
    """Conexão persistente: `connect()` (bloqueante, em thread de trabalho), `send(evento)` (não bloqueia).

    `on_event(evento)` e `on_closed(motivo)` são chamados na thread de leitura.
    """

    def __init__(self, transport_factory: Callable[[], object], on_event: Callable[[dict], None],
                 on_closed: Callable[[str], None]):
        self._factory = transport_factory
        self._on_event = on_event
        self._on_closed = on_closed
        self._transport = None
        self._outbox: "queue.Queue[Optional[str]]" = queue.Queue()
        self._lock = threading.Lock()
        self.connected = False
        self._generation = 0

    def connect(self, session: dict, timeout: float = 10.0) -> None:
        with self._lock:
            self._close_locked()
            transport = self._factory()            # pode lançar TransportClosed
            self._transport = transport
            self._generation += 1
            gen = self._generation
            self._outbox = queue.Queue()
            self.connected = True
            ready = threading.Event()
            self._ready, self._ready_error = ready, None
            threading.Thread(target=self._reader, args=(transport, gen), name="realtime-rx", daemon=True).start()
            threading.Thread(target=self._writer, args=(transport, self._outbox, gen), name="realtime-tx",
                             daemon=True).start()
        self.send({"type": "session.update", "session": session})
        if not ready.wait(timeout):
            self.close()
            raise TransportClosed("A Realtime não confirmou a sessão a tempo.")
        if self._ready_error:
            raise TransportClosed(self._ready_error)

    def send(self, event: dict) -> None:
        if not self.connected:
            raise TransportClosed("Sem conexão com a Realtime.")
        self._outbox.put(json.dumps(event))

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    def _close_locked(self):
        if self._transport is not None:
            self._generation += 1                     # leitor antigo não notifica queda
            self._outbox.put(None)
            try:
                self._transport.close()
            except Exception:
                pass
        self._transport = None
        self.connected = False

    def _writer(self, transport, outbox, gen):
        while True:
            msg = outbox.get()
            if msg is None or gen != self._generation:
                return
            try:
                transport.send(msg)
            except TransportClosed as error:
                self._lost(gen, str(error))
                return

    def _reader(self, transport, gen):
        while True:
            try:
                raw = transport.recv()
            except TransportClosed as error:
                self._lost(gen, str(error))
                return
            if gen != self._generation:
                return
            try:
                event = json.loads(raw)
            except ValueError:
                continue
            kind = event.get("type", "")
            if kind == "session.updated" and not self._ready.is_set():
                self._ready.set()
            elif kind == "error" and not self._ready.is_set():
                err = event.get("error", {}) or {}
                self._ready_error = err.get("message") or "Erro ao configurar a sessão Realtime."
                self._ready_error_param = err.get("param") or ""
                self._ready.set()
                continue
            try:
                self._on_event(event)
            except Exception:
                pass

    def _lost(self, gen, reason):
        if gen != self._generation:
            return
        self.connected = False
        if hasattr(self, "_ready") and not self._ready.is_set():
            self._ready_error = reason
            self._ready.set()
        self._on_closed(reason)


def b64_pcm(samples: np.ndarray) -> str:
    return base64.b64encode(np.asarray(samples, dtype=np.int16).tobytes()).decode("ascii")


def resample(samples: np.ndarray, src_rate: int, dst_rate: int = RATE) -> np.ndarray:
    """Converte a taxa (interp. linear) quando o microfone não abre direto em 24 kHz."""
    if src_rate == dst_rate or len(samples) == 0:
        return np.asarray(samples, dtype=np.int16)
    n = max(1, int(round(len(samples) * dst_rate / src_rate)))
    x = np.linspace(0, len(samples) - 1, n)
    return np.interp(x, np.arange(len(samples)), np.asarray(samples, dtype=np.float32)).astype(np.int16)


def ms(samples: int, rate: int = RATE) -> int:
    return int(math.floor(samples * 1000 / rate))
