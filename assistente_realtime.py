"""Nuria em tempo real: uma sessão persistente da OpenAI Realtime API (áudio + imagem → áudio).

Fluxo de uma pergunta por voz:
  Espaço → OUVINDO: o microfone envia blocos de 40 ms (`input_audio_buffer.append`) enquanto você fala
  fim da fala (silêncio local ou Espaço) → foto da webcam → `input_audio_buffer.commit`
  → `conversation.item.create` (imagem) → `response.create`  (fila única: a imagem entra antes da resposta)
  → terminada a resposta, `conversation.item.delete` da imagem (cada foto é cobrada uma vez só)
  → PENSANDO → primeiros blocos de áudio já tocam → FALANDO (boca no volume do som reproduzido) → PRONTA

Interrupção (Espaço/Esc durante a resposta): `response.cancel`, áudio pendente descartado e
`conversation.item.truncate` no trecho de fato ouvido. Eco: o microfone só fica aberto enquanto
a Nuria NÃO fala (meia-duplex), então ela não ouve a própria voz.
"""
from __future__ import annotations

import base64
import json
import threading
import time
import uuid
from typing import Callable, Optional

import numpy as np

import credenciais
import ia
import realtime as rt
from assistente import Assistant
from voz import MicStream, StreamPlayer, VoiceError

TOOLS = [{
    "type": "function",
    "name": "expressar",
    "description": ("Muda a expressão do seu rosto de LED enquanto você fala. Chame no início da resposta quando a "
                    "emoção não for neutra ou quando pedirem uma demonstração (chorar, suplicar, piscar, ficar brava, "
                    "com sono). Nunca fale sobre esta função nem leia seus argumentos."),
    "parameters": {
        "type": "object",
        "properties": {
            "emocao": {"type": "string", "enum": list(ia.EMOTIONS)},
            "acao": {"type": "string", "enum": list(ia.ACTIONS)},
        },
        "required": ["emocao"],
    },
}]

INSTRUCTIONS = (ia.PERSONA + " Para mostrar emoção ou demonstrar algo, chame a função expressar e, na mesma resposta, "
                "fale normalmente; nunca leia JSON, nomes de funções ou comandos em voz alta.")


NO_VISION_NOTE = "(Visão pausada pelo usuário: nenhuma imagem nesta pergunta.)"


def build_session(cfg) -> dict:
    audio_in = {"format": {"type": "audio/pcm", "rate": rt.RATE}, "turn_detection": None}
    if cfg.transcription and cfg.transcription.lower() not in ("desligada", "off", "none"):
        audio_in["transcription"] = {"model": cfg.transcription, "language": "pt"}
    if cfg.noise and cfg.noise.lower() not in ("desligado", "off", "none"):
        audio_in["noise_reduction"] = {"type": cfg.noise}
    session = {
        "type": "realtime",
        "model": cfg.realtime_model,
        "output_modalities": ["audio"],
        "instructions": INSTRUCTIONS,
        "audio": {"input": audio_in,
                  "output": {"format": {"type": "audio/pcm", "rate": rt.RATE}, "voice": cfg.realtime_voice}},
        "tools": TOOLS,
        "tool_choice": "auto",
    }
    if cfg.reasoning:
        session["reasoning"] = {"effort": cfg.reasoning}
    return session


class RealtimeAssistant(Assistant):
    """Mesma interface do Assistant (painel e rosto não mudam), com o caminho Realtime por baixo."""

    def __init__(self, still: Callable[[], object], transport_factory: Optional[Callable[[object], object]] = None,
                 mic: Optional[MicStream] = None, player: Optional[StreamPlayer] = None,
                 config_loader: Callable[[], object] = credenciais.load, log: bool = True, label: str = "OpenAI Realtime"):
        super().__init__(still, backend_factory=lambda: None, player=player or StreamPlayer(), log=log)
        self.mic = mic or MicStream()
        self._config_loader = config_loader
        self._transport_factory = transport_factory or self._real_transport
        self._client: Optional[rt.RealtimeClient] = None
        self._session_key = None
        self._session_cfg = None
        self.label = label
        self.connection = "desconectado"          # desconectado | conectando | conectado | falha
        self._turn: dict = {}
        self._calls: dict = {}
        self._drain_thread: Optional[threading.Thread] = None
        self._mic_stop = threading.Event()
        self.events_log: list = []                # últimos eventos (diagnóstico/testes), sem áudio
        self._connect_lock = threading.Lock()

    # ---- leitura -----------------------------------------------------------------
    def snapshot(self):
        s = super().snapshot()
        s.level = self.mic.level if s.state == "listening" else 0.0
        return s

    def busy(self) -> bool:
        return self._snap.state in ("connecting", "capturing", "thinking")

    def backend_label(self) -> str:
        return f"{self.label} · {self.connection}"

    def speech_level(self) -> float:
        return self.player.level() if self._snap.state == "speaking" else 0.0

    # ---- conexão --------------------------------------------------------------------
    def _real_transport(self, cfg):
        if not cfg.configured:
            raise ia.AIError("auth", "Chave da OpenAI não configurada. Copie .env.example para .env e preencha OPENAI_API_KEY.")
        return rt.WebSocketTransport(cfg.realtime_url.format(model=cfg.realtime_model), cfg.api_key, cfg.timeout)

    def _ensure_session(self, gen) -> None:
        with self._connect_lock:                  # aquecimento e pergunta nunca abrem duas conexões
            self._ensure_session_locked(gen)

    def _ensure_session_locked(self, gen) -> None:
        cfg = self._config_loader()
        session = build_session(cfg)
        key = (cfg.realtime_model, cfg.realtime_voice, cfg.realtime_url, cfg.api_key)
        if self._client is not None and self._client.connected and key == self._session_key:
            if session != self._session_cfg:                        # mudanças seguras: session.update
                upd = {k: v for k, v in session.items() if k != "model"}
                upd["audio"] = json.loads(json.dumps(session["audio"]))
                upd["audio"]["output"].pop("voice", None)
                self._client.send({"type": "session.update", "session": upd})
                self._session_cfg = session
            return
        # modelo/voz/chave mudaram, ou não há conexão: (re)cria a sessão, com até 3 tentativas
        self._set(gen, state="connecting")
        self.connection = "conectando"
        last = None
        for attempt in range(3):
            if not self._alive(gen):
                return
            client = rt.RealtimeClient(lambda: self._transport_factory(cfg), self._on_event, self._on_closed)
            try:
                try:
                    client.connect(session, timeout=cfg.timeout)
                except rt.TransportClosed as error:
                    if "reasoning" in str(error).lower() and "reasoning" in session:
                        session = dict(session)
                        session.pop("reasoning")                     # modelo sem esforço configurável
                        client.connect(session, timeout=cfg.timeout)
                    else:
                        raise
                if self._client is not None:
                    self._client.close()
                self._client, self._session_key, self._session_cfg = client, key, session
                self.connection = "conectado"
                self._restore_context()
                return
            except ia.AIError:
                self.connection = "falha"
                raise
            except rt.TransportClosed as error:
                last = str(error)
                client.close()
                if "Chave" in last or "acesso" in last or "não encontrado" in last:
                    break
                time.sleep(0.4 * (attempt + 1))
        self.connection = "falha"
        kind = "auth" if last and "Chave" in last else "network"
        raise ia.AIError(kind, last or "Não foi possível conectar à Realtime.")

    def _restore_context(self) -> None:
        """Sessão nova (queda ou troca de modelo): leva as últimas trocas como contexto textual."""
        if not self.history:
            return
        recent = " | ".join(f"Usuário: {q} → Você: {a}" for q, a in self.history[-3:])
        self._client.send({"type": "conversation.item.create", "item": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": f"(Contexto da conversa anterior, não responda: {recent})"}]}})

    def _on_closed(self, reason: str) -> None:
        self.connection = "desconectado"
        st = self._snap.state
        if st in ("listening", "capturing", "thinking", "speaking"):
            self._mic_stop.set()
            self.player.clear()
            self._fail(self._gen, "A conexão com a OpenAI caiu. Vou reconectar na próxima pergunta.")

    def _send(self, event: dict) -> None:
        if self._client is None:
            raise ia.AIError("network", "Sem conexão com a Realtime.")
        try:
            self._client.send(event)
        except rt.TransportClosed as error:
            raise ia.AIError("network", "A conexão com a OpenAI caiu. Tente de novo.") from error

    # ---- comandos -----------------------------------------------------------------------
    def press_voice(self) -> str:
        st = self._snap.state
        if st == "listening":
            self._mic_stop.set()
            return "send"
        if st in ("connecting", "capturing"):
            return "busy"
        if st in ("thinking", "speaking"):
            self._interrupt()
        ready = self._client is not None and self._client.connected
        self._start(self._voice_flow, "listening" if ready else "connecting")
        return "listen"

    def warmup(self) -> None:
        """Conecta em segundo plano ao abrir o app: a primeira pergunta já sai sem esperar o aperto de mão."""
        def run():
            try:
                self._ensure_session(self._gen)
                if self._snap.state == "connecting":
                    self._set(state="idle")
            except Exception as error:
                if self._snap.state in ("connecting", "idle"):
                    self._set(state="idle", message=str(error), message_kind="warning")
        threading.Thread(target=run, name="realtime-warmup", daemon=True).start()

    def ask_text(self, text: str) -> bool:
        text = " ".join((text or "").split())
        if not text or self._snap.state in ("connecting", "capturing", "listening"):
            return False
        if self._snap.state in ("thinking", "speaking"):
            self._interrupt()
        self._start(lambda gen: self._text_flow(gen, text), "capturing")
        return True

    def cancel(self) -> None:
        self._interrupt()
        self._mic_stop.set()
        with self._lock:
            self._gen += 1
        self._set(state="idle", message="Cancelado.", message_kind="warning")

    def shutdown(self) -> None:
        self.cancel()
        self._join(1.0)
        if self._client is not None:
            self._client.close()
        self.player.close()

    def _interrupt(self) -> None:
        """Barge-in: cancela a geração, descarta o áudio pendente e corta o histórico no que foi ouvido."""
        turn = self._turn
        played = self.player.clear()
        if not turn or self._client is None or not self._client.connected:
            return
        try:
            if turn.get("responding"):
                self._client.send({"type": "response.cancel"})
            item = turn.get("audio_item")
            if item and played < self.player.received:
                self._client.send({"type": "conversation.item.truncate", "item_id": item, "content_index": 0,
                                   "audio_end_ms": rt.ms(played)})
        except rt.TransportClosed:
            pass
        turn["responding"] = False
        turn["interrupted"] = True
        self._interrupted_at = time.perf_counter()
        self._drop_images()

    # ---- fluxos (thread de trabalho) ---------------------------------------------------------
    def _new_turn(self, gen, t0):
        self._turn = dict(gen=gen, t0=t0, responding=False, audio_item=None, first_audio=None, done=False,
                          text="", question="", interrupted=False, had_audio=False)
        self._calls = {}

    def _voice_flow(self, gen: int) -> None:
        t0 = time.perf_counter()
        self._ensure_session(gen)
        if not self._alive(gen):
            return
        self._new_turn(gen, t0)
        self._set(gen, state="listening", message="", message_kind="", timings={}, question="", answer="")
        self._send({"type": "input_audio_buffer.clear"})
        self._mic_stop.clear()
        # após interromper, espera a cauda do alto-falante sumir para o microfone não captar a própria voz
        tail = 0.2 - (time.perf_counter() - getattr(self, "_interrupted_at", 0.0))
        if tail > 0:
            time.sleep(tail)
        sent = [0]

        def on_chunk(pcm):
            if self._alive(gen) and self._client is not None and self._client.connected:
                try:
                    self._client.send({"type": "input_audio_buffer.append", "audio": rt.b64_pcm(pcm)})
                    sent[0] += len(pcm)
                except rt.TransportClosed:
                    pass
        try:
            reason = self.mic.run(self._mic_stop, on_chunk)
        except VoiceError:
            try:
                self._send({"type": "input_audio_buffer.clear"})
            except ia.AIError:
                pass
            raise
        if not self._alive(gen):
            return
        if reason == "silence" or sent[0] < rt.RATE // 10:          # < 100 ms: o servidor recusaria o commit
            self._send({"type": "input_audio_buffer.clear"})
            self._set(gen, state="idle", message="Não ouvi nada. Fale mais perto do microfone ou digite a pergunta (Enter).",
                      message_kind="warning")
            return
        t1 = time.perf_counter()
        image = self._capture(gen) if self._snap.vision else None
        self._send({"type": "input_audio_buffer.commit"})
        if image is not None:
            self._send_image(image)
        else:
            self._send({"type": "conversation.item.create", "item": {"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": NO_VISION_NOTE}]}})
        self._respond(gen, t1, dict(escuta=t1 - t0))

    def _text_flow(self, gen: int, question: str) -> None:
        t0 = time.perf_counter()
        self._ensure_session(gen)
        if not self._alive(gen):
            return
        self._new_turn(gen, t0)
        self._turn["question"] = question
        self._set(gen, timings={}, question=question, answer="")
        image = self._capture(gen) if self._snap.vision else None
        text = question if image is not None else question + " " + NO_VISION_NOTE
        self._send({"type": "conversation.item.create", "item": {"type": "message", "role": "user",
                                                                "content": [{"type": "input_text", "text": text}]}})
        if image is not None:
            self._send_image(image)                      # item separado: sai da sessão depois, a pergunta fica
        self._respond(gen, t0, {})

    def _data_url(self, image) -> str:
        return "data:image/jpeg;base64," + base64.b64encode(ia.encode_jpeg(image, max_side=768, quality=80)).decode()

    def _send_image(self, image) -> None:
        item_id = "img_" + uuid.uuid4().hex[:24]
        self._turn.setdefault("image_items", []).append(item_id)
        self._send({"type": "conversation.item.create", "item": {
            "id": item_id, "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": "(Imagem da webcam no momento da pergunta acima.)"},
                        {"type": "input_image", "image_url": self._data_url(image)}]}})

    def _drop_images(self) -> None:
        """Economia: a foto só serve à resposta dela. Depois sai da sessão e não é recobrada nas próximas."""
        ids, self._turn["image_items"] = self._turn.get("image_items", []), []
        for item_id in ids:
            try:
                self._client.send({"type": "conversation.item.delete", "item_id": item_id})
            except (rt.TransportClosed, AttributeError):
                pass

    def _respond(self, gen, t_end, timings) -> None:
        self._turn.update(t_end=t_end, timings=timings, responding=True)
        self._set(gen, state="thinking", answer="", action="", emotion="neutral")
        self.player.begin()
        try:
            self.player.start()
        except VoiceError as error:
            self._turn["no_audio"] = str(error)
        self._send({"type": "response.create"})

    # ---- eventos do servidor (thread de leitura) -------------------------------------------------
    def _on_event(self, event: dict) -> None:
        kind = event.get("type", "")
        if kind != "response.output_audio.delta" and kind != "input_audio_buffer.append":
            self.events_log = (self.events_log + [kind])[-200:]
        turn, gen = self._turn, self._turn.get("gen", -1)
        rid = event.get("response_id") or (event.get("response") or {}).get("id")
        if turn and rid and kind != "response.created" and turn.get("response_id") and rid != turn["response_id"]:
            if kind == "response.done":                      # resposta antiga (cancelada): só registra funções
                return
            return                                           # eventos atrasados de uma resposta interrompida
        if kind == "conversation.item.input_audio_transcription.completed":
            text = (event.get("transcript") or "").strip()
            if text and turn:
                turn["question"] = text
                self._set(gen, question=text)
        elif kind == "response.created":
            if turn and turn.get("responding"):
                turn["response_id"] = rid
        elif kind == "response.output_item.added":
            item = event.get("item") or {}
            if item.get("type") == "message" and turn:
                turn["audio_item"] = item.get("id")
        elif kind == "response.output_audio.delta":
            if not turn or turn.get("interrupted") or not self._alive(gen):
                return
            pcm = np.frombuffer(base64.b64decode(event.get("delta", "")), dtype=np.int16)
            turn["audio_item"] = turn.get("audio_item") or event.get("item_id")
            turn["had_audio"] = True
            self.player.feed(pcm)
            if turn.get("first_audio") is None:
                turn["first_audio"] = time.perf_counter()
                timings = dict(turn.get("timings", {}))
                timings["primeiro_audio"] = turn["first_audio"] - turn.get("t_end", turn["t0"])
                self._set(gen, state="speaking", timings=timings, spoken_offline=bool(turn.get("no_audio")))
        elif kind in ("response.output_audio_transcript.delta", "response.output_text.delta"):
            if turn and not turn.get("interrupted"):
                turn["text"] += event.get("delta", "")
                self._set(gen, answer=turn["text"])
        elif kind == "response.function_call_arguments.done":
            self._handle_call(event.get("call_id"), event.get("name"), event.get("arguments"))
        elif kind == "response.done":
            self._on_response_done(event.get("response") or {})
        elif kind == "error":
            err = event.get("error") or {}
            code, message = err.get("code") or "", err.get("message") or "Erro na Realtime."
            if code in ("response_cancel_not_active", "input_audio_buffer_commit_empty") or "no active response" in message.lower():
                return
            if "delete" in code or ("item" in code and "not_found" in code):
                return                                       # limpeza de imagem antiga: nunca derruba a conversa
            if turn and self._snap.state in ("thinking", "speaking", "capturing", "listening"):
                self._mic_stop.set()
                self.player.clear()
                friendly = ("Chave da OpenAI inválida." if "api key" in message.lower()
                            else "Sem créditos ou limite da API atingido." if "quota" in message.lower() or "rate" in code
                            else f"A Realtime recusou a solicitação: {message}")
                self._fail(gen, friendly)

    def _handle_call(self, call_id, name, arguments) -> None:
        if not call_id or call_id in self._calls:
            return
        try:
            args = json.loads(arguments or "{}")
        except ValueError:
            args = {}
        emotion = str(args.get("emocao", "neutral")).lower()
        action = ia.ACTIONS.get(str(args.get("acao", "nenhuma")).lower()) or ""
        self._calls[call_id] = dict(name=name, emotion=emotion if emotion in ia.EMOTIONS else "neutral", action=action)
        self._set(self._turn.get("gen", -1), emotion=self._calls[call_id]["emotion"], action=action,
                  expr_version=self._snap.expr_version + 1)

    def _on_response_done(self, response: dict) -> None:
        turn, gen = self._turn, self._turn.get("gen", -1)
        status = response.get("status", "completed")
        outputs = response.get("output") or []
        for item in outputs:
            if item.get("type") == "function_call":
                self._handle_call(item.get("call_id"), item.get("name"), item.get("arguments"))
        if not turn or not self._alive(gen):
            return
        if status == "cancelled" or turn.get("interrupted"):
            turn["responding"] = False
            self._drop_images()
            return
        if status == "failed":
            self._drop_images()
            detail = ((response.get("status_details") or {}).get("error") or {}).get("message", "")
            self._fail(gen, f"A Realtime não conseguiu responder. {detail}".strip())
            return
        pending = [cid for cid, c in self._calls.items() if not c.get("answered")]
        for cid in pending:
            self._calls[cid]["answered"] = True
            self._send({"type": "conversation.item.create", "item": {
                "type": "function_call_output", "call_id": cid, "output": json.dumps({"ok": True})}})
        has_message = any(i.get("type") == "message" for i in outputs) or turn.get("had_audio")
        if pending and not has_message:
            self._send({"type": "response.create"})              # só a expressão veio: agora a fala
            return
        turn["responding"] = False
        turn["done"] = True
        self._drop_images()
        if turn.get("text"):
            self.history = (self.history + [(turn.get("question") or self._snap.question, turn["text"])])[-6:]
        self._drain_thread = threading.Thread(target=self._finish_when_played, args=(gen,), daemon=True)
        self._drain_thread.start()

    def _finish_when_played(self, gen) -> None:
        turn = self._turn
        if self._snap.state == "thinking" and not turn.get("had_audio"):
            self._set(gen, state="speaking", spoken_offline=True)    # resposta sem áudio: boca simulada breve
            time.sleep(1.0)
        while self._alive(gen) and self.player.pending() > 0 and not turn.get("interrupted"):
            time.sleep(0.03)
        time.sleep(0.12)                                           # cauda do dispositivo de saída
        if not self._alive(gen) or turn.get("interrupted"):
            return
        timings = dict(self._snap.timings)
        timings["resposta_completa"] = time.perf_counter() - turn.get("t_end", turn["t0"])
        warning = turn.get("no_audio", "")
        self._write_log(dict(pergunta=turn.get("question") or self._snap.question, resposta=turn.get("text", ""),
                             emocao=self._snap.emotion, acao=self._snap.action, modelo=self._session_key[0] if self._session_key else "",
                             tempos={k: round(v, 3) for k, v in timings.items()}, aviso=warning))
        if warning:
            self._set(gen, state="idle", timings=timings, message=warning + " Resposta só em texto.", message_kind="warning")
        else:
            self._set(gen, state="idle", timings=timings, message="", message_kind="done")


def check_connection(cfg, transport_factory=None) -> tuple:
    """Teste rápido da aba API: abre a sessão Realtime e fecha. Devolve (ok, mensagem curta)."""
    if not cfg.configured:
        return False, "sem chave configurada"
    factory = transport_factory or (lambda: rt.WebSocketTransport(cfg.realtime_url.format(model=cfg.realtime_model),
                                                                   cfg.api_key, cfg.timeout))
    client = rt.RealtimeClient(factory, lambda e: None, lambda r: None)
    t0 = time.perf_counter()
    try:
        session = build_session(cfg)
        try:
            client.connect(session, timeout=cfg.timeout)
        except rt.TransportClosed as error:
            if "reasoning" not in str(error).lower():
                raise
            session.pop("reasoning", None)
            client.connect(session, timeout=cfg.timeout)
        return True, f"conectou em {time.perf_counter() - t0:.1f} s · {cfg.realtime_model}".replace(".", ",", 1)
    except rt.TransportClosed as error:
        return False, str(error)[:90]
    finally:
        client.close()
