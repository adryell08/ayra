"""Gemini Live API (Google) atrás da mesma interface da OpenAI Realtime.

`GeminiAdapter` é um transporte: recebe os eventos que o `RealtimeAssistant` já envia (session.update,
input_audio_buffer.*, conversation.item.create, response.create/cancel) e devolve eventos no formato da
Realtime (response.created, response.output_audio.delta, response.done…). Assim estados, interrupção,
expressões, gestos, operador e testes são os mesmos para os dois provedores.

Por baixo: WebSocket `BidiGenerateContent` (setup → realtimeInput/clientContent/toolResponse). Fim de fala
manual (activityStart/activityEnd), como na Realtime. Áudio: entra 16 kHz, sai 24 kHz (PCM 16 bits).
A pergunta falada é guardada localmente e enviada no `response.create`, junto com a foto: assim
"cancelar"/"não ouvi nada" não gastam nada e a imagem sempre entra na mesma vez da pergunta.
"""
from __future__ import annotations

import base64
import json
import queue
import re
import threading
import time
from typing import Optional

import numpy as np

import realtime as rt

IN_RATE = 16000


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _to_16k(pcm: np.ndarray) -> np.ndarray:
    try:
        from scipy.signal import resample_poly
        return np.clip(resample_poly(pcm.astype(np.float32), 2, 3), -32768, 32767).astype(np.int16)
    except ImportError:
        return rt.resample(pcm, rt.RATE, IN_RATE)


def _schema(s: dict) -> dict:
    """JSON Schema (OpenAI) -> Schema do Gemini (tipos em maiúsculas)."""
    out = {}
    if "type" in s:
        out["type"] = str(s["type"]).upper()
    for key in ("description", "enum"):
        if key in s:
            out[key] = s[key]
    if "properties" in s:
        out["properties"] = {k: _schema(v) for k, v in s["properties"].items()}
    if "required" in s:
        out["required"] = list(s["required"])
    return out


def build_setup(session: dict, model: str, voice: str, max_tokens: int) -> dict:
    tools = [{"name": t["name"], "description": t.get("description", ""), "parameters": _schema(t["parameters"])}
             for t in session.get("tools", []) if t.get("type") == "function"]
    gen = {"responseModalities": ["AUDIO"],
           "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}}
    if max_tokens:
        gen["maxOutputTokens"] = int(max_tokens)
    setup = {
        "model": model if model.startswith("models/") else "models/" + model,
        "generationConfig": gen,
        "systemInstruction": {"parts": [{"text": session.get("instructions", "")}]},
        "realtimeInputConfig": {"automaticActivityDetection": {"disabled": True}},
        "inputAudioTranscription": {},
        "outputAudioTranscription": {},
        "contextWindowCompression": {"slidingWindow": {}},   # sessões longas com imagem não expiram em 2 min
    }
    if tools:
        setup["tools"] = [{"functionDeclarations": tools}]
    return {"setup": setup}


class GeminiSocket:
    """WebSocket real (sem chave no texto de erros)."""

    def __init__(self, url: str, api_key: str, timeout: float = 10.0):
        self._key = api_key
        try:
            from websockets.sync.client import connect
        except ImportError as error:
            raise rt.TransportClosed("Pacote 'websockets' ausente: pip install -r requirements.txt") from error
        try:
            self._ws = connect(url + ("&" if "?" in url else "?") + "key=" + api_key, open_timeout=timeout,
                               max_size=None, ping_interval=20, ping_timeout=20)
        except Exception as error:
            raise rt.TransportClosed(self._describe(error)) from error

    def _clean(self, text: str) -> str:
        return str(text).replace(self._key, "…") if self._key else str(text)

    def _describe(self, error) -> str:
        text = self._clean(error)
        status = getattr(getattr(error, "response", None), "status_code", None)
        if status in (400, 401, 403) or "API key" in text or "API_KEY" in text:
            return "Chave do Gemini inválida ou sem acesso ao Live. Confira a chave na aba API."
        if status == 404 or "404" in text:
            return "Modelo do Gemini Live não encontrado (NURIA_MODELO_GEMINI)."
        if status == 429 or "429" in text or "quota" in text.lower():
            return "Limite ou cota do Gemini esgotados."
        if isinstance(error, (OSError, TimeoutError)) or "timed out" in text.lower():
            return "Sem conexão com o Gemini. Verifique a internet."
        return f"Não foi possível conectar ao Gemini Live: {text[:120]}"

    def send(self, text: str) -> None:
        try:
            self._ws.send(text)
        except Exception as error:
            raise rt.TransportClosed(f"Conexão com o Gemini perdida ao enviar: {self._clean(error)[:120]}") from error

    def recv(self) -> str:
        try:
            msg = self._ws.recv()
        except Exception as error:
            reason = self._clean(getattr(error, "reason", "") or error)
            if "API key" in reason or "API_KEY" in reason:
                raise rt.TransportClosed("Chave do Gemini inválida. Confira a chave na aba API.") from error
            raise rt.TransportClosed(f"O Gemini encerrou a conexão: {reason[:140]}") from error
        return msg.decode("utf-8") if isinstance(msg, (bytes, bytearray)) else msg

    def close(self) -> None:
        try:
            self._ws.close()
        except Exception:
            pass


class GeminiAdapter:
    """Transporte com a interface do `RealtimeClient` (send/recv/close de JSON) falando Gemini Live por baixo."""

    def __init__(self, raw, model: str, voice: str, max_tokens: int = 0):
        self.raw, self.model, self.voice, self.max_tokens = raw, model, voice, max_tokens
        self._out: "queue.Queue[str]" = queue.Queue()
        self._lock = threading.Lock()
        self._setup_sent = False
        self._audio: list = []                 # pergunta falada (16 kHz) até o response.create
        self._committed = False
        self._texts: list = []
        self._images: list = []
        self._seq = 0
        self._rid: Optional[str] = None        # resposta em andamento (ids no formato da Realtime)
        self._item: Optional[str] = None
        self._cancelled: set = set()
        self._calls: list = []
        self._transcript = ""
        self._question = ""
        self._started_audio = False
        self._open = False                     # o Gemini ainda está gerando um turno (sem turnComplete)
        self._stale = False                    # conteúdo que chega agora é do turno anterior (interrompido)
        self._closed = False

    # ---- utilitários ----
    def _emit(self, event: dict) -> None:
        self._out.put(json.dumps(event))

    def _raw(self, msg: dict) -> None:
        self.raw.send(json.dumps(msg))

    def _new_ids(self):
        self._seq += 1
        self._rid, self._item = f"resp_g{self._seq}", f"item_g{self._seq}"
        self._transcript, self._calls, self._started_audio = "", [], False
        self._emit({"type": "response.created", "response": {"id": self._rid, "status": "in_progress"}})

    # ---- cliente -> Gemini ----
    def send(self, text: str) -> None:
        if self._closed:
            raise rt.TransportClosed("Conexão com o Gemini encerrada.")
        ev = json.loads(text)
        kind = ev.get("type", "")
        with self._lock:
            if kind == "session.update":
                if not self._setup_sent:
                    session = ev.get("session") or {}
                    tokens = session.get("max_output_tokens", self.max_tokens)
                    self._raw(build_setup(session, self.model, self.voice, 0 if tokens == "inf" else int(tokens or 0)))
                    self._setup_sent = True
                else:                                   # o Gemini não muda a sessão aberta: confirma e segue
                    self._emit({"type": "session.updated"})
            elif kind == "input_audio_buffer.append":
                pcm = np.frombuffer(base64.b64decode(ev.get("audio", "")), dtype=np.int16)
                self._audio.append(_to_16k(pcm))
            elif kind == "input_audio_buffer.clear":
                self._audio, self._committed = [], False
            elif kind == "input_audio_buffer.commit":
                self._committed = True
            elif kind == "conversation.item.create":
                self._item_create(ev.get("item") or {})
            elif kind == "response.create":
                self._respond(ev.get("response") or {})
            elif kind == "response.cancel":
                if self._rid and self._rid not in self._cancelled:
                    self._cancelled.add(self._rid)
                    self._emit({"type": "response.done", "response": {"id": self._rid, "status": "cancelled", "output": []}})
            # conversation.item.truncate/delete: o Gemini mantém o próprio histórico (janela deslizante)

    def _item_create(self, item: dict) -> None:
        if item.get("type") == "function_call_output":
            return                                      # já respondido na hora da chamada
        for part in item.get("content") or []:
            if part.get("type") == "input_text" and part.get("text"):
                text = part["text"]
                if text.startswith("(Imagem da webcam"):
                    continue
                self._texts.append(text)
            elif part.get("type") == "input_image":
                url = part.get("image_url", "")
                if "," in url:
                    self._images.append(url.split(",", 1)[1])

    def _respond(self, response: dict) -> None:
        instr = response.get("instructions") or ""
        if instr:                                       # frase do operador no modo assistido
            said = re.search(r"«(.+)»", instr, re.S)
            self._texts.append("Diga exatamente esta frase, com naturalidade e sem acrescentar nada: "
                               + (said.group(1) if said else instr))
        if self._open:                                  # turno anterior ainda saindo: o novo o interrompe
            self._stale = True
        self._new_ids()
        if self._committed and self._audio:
            audio = np.concatenate(self._audio)
            if self._texts:                             # notas/contexto entram no histórico sem gerar resposta
                self._raw({"clientContent": {"turns": [{"role": "user", "parts": [{"text": " ".join(self._texts)}]}],
                                             "turnComplete": False}})
            self._raw({"realtimeInput": {"activityStart": {}}})
            step = IN_RATE // 2                         # blocos de 0,5 s
            for i in range(0, len(audio), step):
                self._raw({"realtimeInput": {"audio": {"data": _b64(audio[i:i + step].tobytes()),
                                                       "mimeType": f"audio/pcm;rate={IN_RATE}"}}})
            for img in self._images:
                self._raw({"realtimeInput": {"video": {"data": img, "mimeType": "image/jpeg"}}})
            self._raw({"realtimeInput": {"activityEnd": {}}})
        elif self._texts or self._images:
            parts = [{"text": " ".join(self._texts)}] if self._texts else []
            parts += [{"inlineData": {"mimeType": "image/jpeg", "data": img}} for img in self._images]
            self._raw({"clientContent": {"turns": [{"role": "user", "parts": parts}], "turnComplete": True}})
        else:                                           # nada novo a dizer (ex.: só a expressão veio): encerra
            self._emit({"type": "response.done", "response": {"id": self._rid, "status": "completed", "output": []}})
            self._rid = None
        self._audio, self._committed, self._texts, self._images = [], False, [], []

    # ---- Gemini -> cliente ----
    def recv(self) -> str:
        while True:
            try:
                return self._out.get_nowait()
            except queue.Empty:
                pass
            raw = self.raw.recv()
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            with self._lock:
                self._translate(msg)

    def _translate(self, msg: dict) -> None:
        if "setupComplete" in msg:
            self._emit({"type": "session.created"})
            self._emit({"type": "session.updated"})
            return
        if "error" in msg:
            err = msg["error"] if isinstance(msg["error"], dict) else {"message": str(msg["error"])}
            self._emit({"type": "error", "error": {"code": str(err.get("code", "")), "message": err.get("message", "Erro no Gemini.")}})
            return
        if "toolCall" in msg:
            responses = []
            for call in msg["toolCall"].get("functionCalls") or []:
                cid = call.get("id") or f"call_g{self._seq}"
                args = json.dumps(call.get("args") or {}, ensure_ascii=False)
                if self._rid is None:
                    self._new_ids()
                self._calls.append({"type": "function_call", "call_id": cid, "name": call.get("name"), "arguments": args})
                if self._rid not in self._cancelled:
                    self._emit({"type": "response.function_call_arguments.done", "response_id": self._rid,
                                "call_id": cid, "name": call.get("name"), "arguments": args})
                responses.append({"id": cid, "name": call.get("name"), "response": {"result": "ok"}, "scheduling": "SILENT"})
            if responses:
                self._raw({"toolResponse": {"functionResponses": responses}})
            return
        sc = msg.get("serverContent")
        if not sc:
            return                                       # usageMetadata, goAway, sessionResumptionUpdate…
        if self._stale:                                  # restos do turno interrompido: descarta até o fim dele
            if sc.get("interrupted") or sc.get("turnComplete"):
                self._stale = False
                self._open = False
            return
        if sc.get("modelTurn") or sc.get("outputTranscription"):
            self._open = True
        if sc.get("inputTranscription", {}).get("text"):
            self._question += sc["inputTranscription"]["text"]
        if self._rid is None and (sc.get("modelTurn") or sc.get("outputTranscription")):
            self._new_ids()                              # fala sem pedido explícito (ex.: depois de uma função)
        rid = self._rid
        live = rid is not None and rid not in self._cancelled
        if self._question and (sc.get("modelTurn") or sc.get("turnComplete")):
            self._emit({"type": "conversation.item.input_audio_transcription.completed", "item_id": "in_" + str(rid),
                        "transcript": self._question.strip()})
            self._question = ""
        for part in (sc.get("modelTurn") or {}).get("parts") or []:
            data = (part.get("inlineData") or {}).get("data")
            if data and live:
                if not self._started_audio:
                    self._started_audio = True
                    self._emit({"type": "response.output_item.added", "response_id": rid,
                                "item": {"id": self._item, "type": "message", "role": "assistant"}})
                self._emit({"type": "response.output_audio.delta", "response_id": rid, "item_id": self._item,
                            "delta": data})
        text = (sc.get("outputTranscription") or {}).get("text")
        if text and live:
            self._transcript += text
            self._emit({"type": "response.output_audio_transcript.delta", "response_id": rid, "item_id": self._item,
                        "delta": text})
        if sc.get("interrupted"):
            self._open = False
            if live:
                self._cancelled.add(rid)
                self._emit({"type": "response.done", "response": {"id": rid, "status": "cancelled", "output": []}})
        if sc.get("turnComplete"):
            self._open = False
        if sc.get("turnComplete") and rid is not None:
            if rid not in self._cancelled:
                output = list(self._calls)
                if self._started_audio or self._transcript:
                    output.append({"id": self._item, "type": "message", "role": "assistant",
                                   "content": [{"type": "output_audio", "transcript": self._transcript}]})
                self._emit({"type": "response.done", "response": {"id": rid, "status": "completed", "output": output}})
            self._rid = None

    def close(self) -> None:
        self._closed = True
        try:
            self.raw.close()
        except Exception:
            pass


def connect(cfg, raw=None) -> GeminiAdapter:
    if raw is None:
        raw = GeminiSocket(cfg.gemini_url, cfg.gemini_key, cfg.timeout)
    return GeminiAdapter(raw, cfg.gemini_model, cfg.gemini_voice, cfg.max_tokens)


# ---------------------------------------------------------------------------
# Servidor Gemini Live simulado (testes e ensaio sem chave/sem rede)
# ---------------------------------------------------------------------------
class FakeGeminiServer:
    """Fala o protocolo do Gemini Live: setup, realtimeInput (atividade manual), clientContent e toolResponse."""

    def __init__(self, latency: float = 0.05, speed: float = 8.0, reject_key: bool = False):
        self.latency, self.speed, self.reject_key = latency, speed, reject_key
        self.received: list = []
        self._out: "queue.Queue[Optional[str]]" = queue.Queue()
        self._closed = False
        self.setup = None
        self._activity = False
        self._audio_ms = 0.0
        self._images = 0
        self._pending_tool = None
        self._turn = 0                          # novo turno interrompe o anterior (como o Gemini real)
        self._answering = False

    def send(self, text: str) -> None:
        if self._closed:
            raise rt.TransportClosed("fechado")
        msg = json.loads(text)
        self.received.append(msg)
        if "setup" in msg:
            if self.reject_key:
                self._closed = True
                self._out.put(None)
                return
            self.setup = msg["setup"]
            self._out.put(json.dumps({"setupComplete": {}}))
        elif "realtimeInput" in msg:
            ri = msg["realtimeInput"]
            if "activityStart" in ri:
                self._activity, self._audio_ms, self._images = True, 0.0, 0
            elif "audio" in ri and self._activity:
                self._audio_ms += len(base64.b64decode(ri["audio"]["data"])) / 2 / IN_RATE * 1000
            elif "video" in ri and self._activity:
                self._images += 1
            elif "activityEnd" in ri and self._activity:
                self._activity = False
                self._interrupt_current()
                self._turn += 1
                threading.Thread(target=self._answer, args=("(voz)", self._images, self._turn), daemon=True).start()
        elif "clientContent" in msg:
            parts = (msg["clientContent"].get("turns") or [{}])[-1].get("parts", [])
            text = " ".join(p.get("text", "") for p in parts)
            imgs = sum(1 for p in parts if "inlineData" in p)
            if msg["clientContent"].get("turnComplete"):
                self._interrupt_current()
                self._turn += 1
                threading.Thread(target=self._answer, args=(text, imgs, self._turn), daemon=True).start()
        elif "toolResponse" in msg and self._pending_tool:
            ev, self._pending_tool = self._pending_tool, None
            ev.set()

    def _put(self, msg):
        if not self._closed:
            self._out.put(json.dumps(msg))

    def _interrupt_current(self):
        if self._answering:
            self._answering = False
            self._put({"serverContent": {"interrupted": True}})

    def _answer(self, question: str, images: int, turn: int = 0) -> None:
        self._answering = True
        time.sleep(self.latency)
        q = question.lower()
        if question == "(voz)":
            self._put({"serverContent": {"inputTranscription": {"text": "o que você está vendo?"}}})
        gesture = next((g for k, g in rt.FAKE_GESTURES if k in q), None)
        if gesture or "chor" in q:
            done = threading.Event()
            self._pending_tool = done
            args = {"emocao": "sad" if "chor" in q else "happy", "acao": "chorar" if "chor" in q else "nenhuma",
                    "gesto": gesture or "nenhum"}
            self._put({"toolCall": {"functionCalls": [{"id": "fc_1", "name": "expressar", "args": args}]}})
            done.wait(2.0)
        if "diga exatamente" in q:
            said = re.search(r"sem acrescentar nada: (.+)$", question)
            text = said.group(1).strip() if said else "Ok."
        else:
            text = ("Gemini simulado: " + ("vejo a cena pela foto." if images else "sem imagem desta vez."))
            if "longa" in q:
                text += " Era uma vez um robô muito curioso que queria ver o mundo inteiro pela webcam." * 3
        for word in text.split(" "):
            if self._closed:
                return
            if turn and turn != self._turn:
                return
            t = np.arange(int(IN_RATE * 1.5 * 0.18)) / 24000
            pcm = (np.sin(2 * np.pi * 200 * t) * 5000).astype(np.int16)
            self._put({"serverContent": {"modelTurn": {"parts": [{"inlineData": {"mimeType": "audio/pcm;rate=24000",
                                                                                    "data": _b64(pcm.tobytes())}}]}}})
            self._put({"serverContent": {"outputTranscription": {"text": word + " "}}})
            time.sleep(0.18 / self.speed)
        if turn and turn != self._turn:
            return
        self._answering = False
        self._put({"serverContent": {"generationComplete": True}})
        self._put({"serverContent": {"turnComplete": True}})
        self._put({"usageMetadata": {"totalTokenCount": 123}})

    def recv(self) -> str:
        msg = self._out.get()
        if msg is None:
            raise rt.TransportClosed("Chave do Gemini inválida. Confira a chave na aba API."
                                     if self.reject_key else "Conexão encerrada.")
        return msg

    def close(self) -> None:
        self._closed = True
        self._out.put(None)
