"""Ayra em tempo real: uma sessão persistente da OpenAI Realtime API (áudio + imagem → áudio).

Fluxo de uma pergunta por voz:
  Espaço → OUVINDO: o microfone envia blocos de 40 ms (`input_audio_buffer.append`) enquanto você fala
  fim da fala (silêncio local ou Espaço) → foto da webcam → `input_audio_buffer.commit`
  → `conversation.item.create` (imagem) → `response.create`  (fila única: a imagem entra antes da resposta)
  → terminada a resposta, `conversation.item.delete` da imagem (cada foto é cobrada uma vez só)
  → PENSANDO → primeiros blocos de áudio já tocam → FALANDO (boca no volume do som reproduzido) → PRONTA

Interrupção (Espaço/Esc durante a resposta): `response.cancel`, áudio pendente descartado e
`conversation.item.truncate` no trecho de fato ouvido. Eco: o microfone só fica aberto enquanto
a Ayra NÃO fala (meia-duplex), então ela não ouve a própria voz.
"""
from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Callable, Optional

import numpy as np

import credenciais
import ia
import realtime as rt
from contexto import CONFIG_PATH as RAG_CONFIG_PATH, RESULTADO_LIMITE, TOOL_LEMBRAR, ContextAssembler, RagConfig, executar_lembrar
from assistente import Assistant
from gestos import GESTURES
from voz import MicStream, StreamPlayer, VoiceError

COSMETIC_TOOLS = {"expressar"}            # ferramentas só visuais: não carregam informação para a fala
MAX_COSMETIC_FOLLOWUPS = 1                # no máximo UMA continuação dedicada à fala por pergunta
NO_SPEECH_NOTICE = "Não consegui responder agora. Pode repetir?"   # neutro: sem citar ferramenta, função ou operador
MAX_STALE_RIDS = 8
VOICE_DOWN_NOTICE = "Minha voz falhou agora; a resposta está em texto."   # neutro: igual para IA e operador, sem causa técnica
MAX_DEFERRED = 8                          # avisos adiados em teleoperado (zero tráfego ao modelo)

TOOLS = [{
    "type": "function",
    "name": "expressar",
    "description": ("Sua única ferramenta: muda a expressão do rosto de LED e, se quiser, faz uma simulação e um gesto "
                    "com os braços enquanto você fala. Chame no início da resposta quando a emoção não for neutra, quando "
                    "um gesto combinar com a fala (tchau ao se despedir, joia ao concordar...) ou quando pedirem uma "
                    "demonstração. Nunca fale sobre esta função nem leia seus argumentos."),
    "parameters": {
        "type": "object",
        "properties": {
            "emocao": {"type": "string", "enum": list(ia.EMOTIONS),
                       "description": "Rosto: neutral=normal, happy=feliz, surprised=surpresa, sad=triste, "
                                      "angry=brava, wide=curiosa (olhos arregalados), sleepy=com sono."},
            "acao": {"type": "string", "enum": list(ia.ACTIONS),
                     "description": "Simulação no rosto: chorar=lágrimas, suplicar=olhos pidões, piscar=piscadela."},
            "gesto": {"type": "string", "enum": ["nenhum"] + list(GESTURES),
                      "description": "Gesto do corpo 3D: " + ", ".join(f"{k}={v.lower()}" for k, v in GESTURES.items())
                                     + ". sim/nao mexem só a cabeça."},
            "lado": {"type": "string", "enum": ["direito", "esquerdo"],
                     "description": "Braço do gesto (só para gestos de um braço). Omitido = direito."},
        },
        "required": ["emocao"],
    },
}]

TOOL_LOOK = {
    "type": "function", "name": "olhar",
    "description": ("Tira uma foto da webcam agora e a entrega a você. Chame SOMENTE quando a resposta depender do que "
                    "está na frente da câmera (objeto na mão, roupa, ambiente, quem está aqui). Não chame em conversa comum. "
                    "Nunca fale sobre esta função."),
    "parameters": {"type": "object", "properties": {}, "required": []},
}
TOOL_FACES = [
    {"type": "function", "name": "lembrar_rosto",
     "description": ("Cadastra o rosto de quem está na câmera com um nome, para reconhecê-lo depois. Primeiro chame com "
                     "confirmado=false (ou omita) para iniciar; então PERGUNTE à pessoa se autoriza e espere o sim; só "
                     "então chame com confirmado=true. Nunca cadastre sem o sim. Nunca fale sobre esta função."),
     "parameters": {"type": "object", "properties": {
         "nome": {"type": "string", "description": "Como a pessoa quer ser chamada."},
         "confirmado": {"type": "boolean", "description": "true somente após a pessoa dizer sim ao pedido de autorização."}},
         "required": ["nome"]}},
    {"type": "function", "name": "esquecer_rosto",
     "description": "Apaga o rosto cadastrado de uma pessoa (ou de todos, com nome 'todos'). Nunca fale sobre esta função.",
     "parameters": {"type": "object", "properties": {"nome": {"type": "string"}}, "required": ["nome"]}},
]

MODES = ("autonomo", "assistido", "teleoperado")

def vision_mode() -> str:
    """"demanda" (padrão): sem foto por pergunta, a IA chama olhar quando precisa. "sempre": foto em toda pergunta."""
    return "sempre" if os.environ.get("NURIA_VISAO", "demanda").strip().lower() in ("sempre", "always", "toda") else "demanda"


def _instructions(mode: str) -> str:
    base = ia._PERSONA_TMPL.replace("{VISAO}", ia.VISAO_DEMANDA if mode == "demanda" else ia.VISAO_SEMPRE)
    cam = ("webcam sem foto automática (você só vê quando chama olhar)" if mode == "demanda"
           else "webcam com 1 foto por pergunta (a visão pode estar pausada)")
    return (base + " O que você tem: rosto de LED na tela (e num painel físico de LEDs); corpo 3D com braços e "
            "mãos, quando ligado (nunca diga que não tem braços); " + cam + "; seus olhos seguem rostos sozinhos e "
            "você responde sozinha, sem ferramenta, a acenos, joinha, paz e amor, OK e dedo do meio que vê na câmera; "
            "um operador humano pode assumir sua voz ou mandar frases. "
            "Para emoção, simulação ou gesto, chame a função expressar e, na mesma resposta, fale normalmente; "
            "nunca leia JSON, nomes de funções ou comandos em voz alta. "
            "Rostos: você só sabe o nome de quem foi cadastrado, por notas de contexto ('reconheci fulano'). "
            "Se alguém pedir para você lembrar dele, pergunte em uma frase se autoriza guardar o rosto (só números, "
            "sem foto, com prazo) e SÓ DEPOIS do sim chame lembrar_rosto com confirmado=true. "
            "Se pedirem para esquecer o rosto, chame esquecer_rosto.")


INSTRUCTIONS = _instructions("sempre")      # texto de referência (= persona legada); a sessão usa _instructions(vision_mode())


NO_VISION_NOTE = "(Visão pausada pelo usuário: nenhuma imagem nesta pergunta.)"


def build_session(cfg, faces: bool = True, extra=()) -> dict:
    """`faces`: oferece lembrar_rosto/esquecer_rosto (o assistente só as oferece com o reconhecimento disponível);
    `extra`: ferramentas a mais (ex.: `lembrar`, só com o RAG ligado na voz)."""
    mode = vision_mode()
    audio_in = {"format": {"type": "audio/pcm", "rate": rt.RATE}, "turn_detection": None}
    if cfg.transcription and cfg.transcription.lower() not in ("desligada", "off", "none"):
        audio_in["transcription"] = {"model": cfg.transcription, "language": "pt"}
    if cfg.noise and cfg.noise.lower() not in ("desligado", "off", "none"):
        audio_in["noise_reduction"] = {"type": cfg.noise}
    session = {
        "type": "realtime",
        "model": cfg.realtime_model,
        "output_modalities": ["audio"],
        "instructions": _instructions(mode),
        "audio": {"input": audio_in,
                  "output": {"format": {"type": "audio/pcm", "rate": rt.RATE}, "voice": cfg.realtime_voice}},
        "tools": TOOLS + ([TOOL_LOOK] if mode == "demanda" else []) + (TOOL_FACES if faces else []) + list(extra),
        "tool_choice": "auto",
    }
    if cfg.reasoning:
        session["reasoning"] = {"effort": cfg.reasoning}
    tokens = int(getattr(cfg, "max_tokens", 0) or 0)
    session["max_output_tokens"] = tokens if tokens else "inf"     # teto por resposta (economia; áudio conta)
    return session


_END_RE = re.compile(r"^(?:(?:ei|oi|ola|pode|por favor)\s+)*(?:(?:nuria|ayra)\s+)?(?:(?:pode|por favor)\s+)*"
                     r"(?:encerrar|encerra|encerre|terminar|termina|finalizar|finaliza)\s+(?:a\s+|essa\s+|esta\s+)?"
                     r"(?:conversa|escuta|ligacao)(?:\s+(?:por favor|agora|nuria|ayra))*$")


def is_end_command(text: str) -> bool:
    """Frase inequívoca para encerrar a escuta ("Ayra, encerrar conversa"); palavras soltas como "para" não valem."""
    plain = unicodedata.normalize("NFD", (text or "").lower())
    plain = re.sub(r"[^a-z ]+", " ", "".join(c for c in plain if unicodedata.category(c) != "Mn"))
    plain = " ".join(plain.split())
    return len(plain.split()) <= 7 and bool(_END_RE.match(plain))


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
        self._fixed_label = label != "OpenAI Realtime"           # "simulada" etc.: rótulo fixo
        self._image_side = 512
        self.vision_mode = vision_mode()
        self.faces = None                           # rostos.FaceRecognizer (opcional, ligado no main)
        self.rag: Optional[ContextAssembler] = None     # montagem do contexto de memória (criada na primeira vez que a memória é usada)
        self.rag_config_path = RAG_CONFIG_PATH          # rag.local.json (relido quando muda)
        self._rag_sig = None
        self.eleven_factory = None                  # testes: (cfg) -> objeto com .stream(texto, cancelled)
        self._eleven_cache = (None, None)
        self.connection = "desconectado"          # desconectado | conectando | conectado | falha
        self._turn: dict = {}
        self._calls: dict = {}
        self._drain_thread: Optional[threading.Thread] = None
        self._deferred: list = []                   # avisos dados em teleoperado (≤ MAX_DEFERRED): chegam ao modelo na volta
        self._handoff = False                       # operador agiu desde a última resposta da IA: injeta resumo
        self._op_t0: Optional[float] = None
        self._last_audio: list = []                 # áudio da última resposta concluída (botão Repetir)
        self._mic_stop = threading.Event()
        self.events_log: list = []                # últimos eventos (diagnóstico/testes), sem áudio
        self._connect_lock = threading.Lock()
        self._op_live = False
        self._op_last = 0.0
        self._op_voice = 0.0                       # último instante com voz do operador (não só ruído)
        self._op_floor = None
        self._op_pre: list = []                    # 150 ms antes da voz do operador (não corta a 1ª sílaba)
        self.continuous = False
        self._stale_rids: list = []                # ids de respostas canceladas (limitado): eventos atrasados são ignorados
        self._conv_stop = threading.Event()
        self._conv_thread: Optional[threading.Thread] = None
        from filtro_voz import VoiceMask
        cfg_level = int(getattr(config_loader(), "robot_level", 0) or 0)
        self.voice_mask = VoiceMask(max(0, min(100, cfg_level)) / 100.0)   # 0 = voz natural … 1 = totalmente robótica
        if self.voice_mask.active:
            self.voice_mask.warm()

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
        if getattr(cfg, "provider", "openai") == "gemini":
            if not cfg.gemini_configured:
                raise ia.AIError("auth", "Chave do Gemini não configurada. Cole em Ajustes → API (ou GEMINI_API_KEY no .env).")
            import gemini_live
            return gemini_live.connect(cfg)
        if not cfg.configured:
            raise ia.AIError("auth", "Chave da OpenAI não configurada. Copie .env.example para .env e preencha OPENAI_API_KEY.")
        return rt.WebSocketTransport(cfg.realtime_url.format(model=cfg.realtime_model), cfg.api_key, cfg.timeout)

    def _ensure_session(self, gen) -> None:
        with self._connect_lock:                  # aquecimento e pergunta nunca abrem duas conexões
            self._ensure_session_locked(gen)

    def _ensure_session_locked(self, gen) -> None:
        cfg = self._config_loader()
        session = build_session(cfg, faces=self.faces is not None, extra=self._extra_tools())
        self._image_side = int(getattr(cfg, "image_side", 768) or 768)
        provider = getattr(cfg, "provider", "openai")
        if not self._fixed_label:
            self.label = getattr(cfg, "live_label", self.label)
        key = ((cfg.realtime_model, cfg.realtime_voice, cfg.realtime_url, cfg.api_key) if provider != "gemini" else
               ("gemini", cfg.gemini_model, cfg.gemini_voice, cfg.gemini_url, cfg.gemini_key, cfg.max_tokens,
                tuple(t["name"] for t in session["tools"])))      # o conjunto de ferramentas entra na sessão do Gemini
        if self._client is not None and self._client.connected and key == self._session_key:
            if session != self._session_cfg and provider != "gemini":   # mudanças seguras: session.update
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
                transport = getattr(client, "_transport", None)
                if transport is not None and hasattr(transport, "tool_handler"):
                    transport.tool_handler = self._lembrar_gemini      # Gemini responde `lembrar` dentro da vez
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

    def new_conversation(self) -> None:
        super().new_conversation()
        self._last_audio = []
        self._session_key = None                    # força sessão nova no provedor (sem o contexto antigo)

    def resume_session(self, session_id: str) -> int:
        n = super().resume_session(session_id)
        if n:
            self._last_audio = []
            self._session_key = None                # sessão nova; _restore_context leva as últimas trocas em texto
        return n

    def can_repeat(self) -> bool:
        return bool(self._last_audio) and self._snap.state == "idle" and self._snap.mode != "teleoperado"

    def repeat_last(self) -> bool:
        """Toca de novo o áudio da última resposta, sem nova análise nem nova chamada à IA."""
        if not self.can_repeat():
            return False
        chunks = list(self._last_audio)
        self._start(lambda gen: self._repeat_flow(gen, chunks), "speaking")
        return True

    def _repeat_flow(self, gen, chunks) -> None:
        self.player.begin()
        try:
            self.player.start()
        except VoiceError as error:
            self._set(gen, state="idle", message=str(error), message_kind="warning")
            return
        for chunk in chunks:
            self.player.feed(chunk)
        self._set(gen, state="speaking", spoken_offline=False)
        while self._alive(gen) and self.player.pending() > 0:
            time.sleep(0.03)
        time.sleep(0.12)
        if self._alive(gen):
            self._set(gen, state="idle", message="", message_kind="done")

    def _end_by_voice(self, turn: dict) -> None:
        """"Encerrar conversa": cancela a resposta em andamento e solta o microfone travado."""
        turn["ended"] = True
        self._interrupt()
        if self.continuous:
            self.set_continuous(False)
        with self._lock:
            self._gen += 1
        self._set(state="idle", question="", answer="", message="Conversa encerrada.", message_kind="done")

    def _restore_context(self) -> None:
        """Sessão nova (queda ou troca de modelo): leva as últimas trocas e a memória (instruções vigentes) como texto."""
        if self.history:
            recent = " | ".join(f"Usuário: {q} → Você: {a}" for q, a in self.history[-3:])
            self._client.send({"type": "conversation.item.create", "item": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": f"(Contexto da conversa anterior, não responda: {recent})"}]}})
        self._send_memory_context(handoff=False, client=self._client)

    # ---- RAG: contexto de memória e ferramenta `lembrar` ------------------------------------------------
    def rag_assembler(self) -> Optional[ContextAssembler]:
        """Montador de contexto da sessão (None sem memória). Relê `rag.local.json` quando ele muda; ausente = RAG desligado."""
        m = self._mem()
        if m is None:
            return None
        try:
            st = Path(self.rag_config_path).stat()
            sig = (st.st_mtime_ns, st.st_size)
        except OSError:
            sig = None
        if self.rag is None or self.rag.mem is not m:
            if self.rag is not None:
                self.rag.fechar()
            self.rag = ContextAssembler(m, RagConfig.carregar(self.rag_config_path))
            self._rag_sig = sig
        elif sig != self._rag_sig:
            self.rag.atualizar_config(RagConfig.carregar(self.rag_config_path))
            self._rag_sig = sig
        return self.rag

    def _extra_tools(self) -> list:
        """`lembrar` só entra na sessão de voz com RAG ligado E `rag_voz` (chave separada: ligar o RAG não muda a fala sozinho)."""
        asm = self.rag_assembler()
        return [TOOL_LEMBRAR] if asm is not None and asm.cfg.rag_ligado and asm.cfg.rag_voz else []

    def _rag_item(self, gen: int, question: str) -> Optional[str]:
        """Texto recuperado da memória para a pergunta digitada (rotulado como DADO), ou None. A voz não faz isso: ela usa `lembrar`."""
        asm = self.rag_assembler()
        m = self._mem()
        if asm is None or m is None or not asm.cfg.rag_ligado:
            return None
        pkt = asm.montar(m.user_id, self.session_id, question, cancelado=lambda: not self._alive(gen))
        if not self._alive(gen) or not pkt.fontes:
            return None
        return pkt.itens_texto[-1]

    def _send_memory_context(self, handoff: bool, client=None) -> None:
        m = self._mem()
        client = client or self._client
        if m is None or client is None:
            return
        try:
            m.flush(0.25 if not handoff else 0.05)
            asm = self.rag_assembler()
            texts = asm.montar(m.user_id, self.session_id, None, handoff=handoff).itens_texto if asm is not None else []
            for text in texts:
                client.send({"type": "conversation.item.create", "item": {
                    "type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}})
        except Exception:
            pass                                          # falha da memória nunca interrompe a conversa

    def memory_note(self, text: str) -> None:
        """Mudança feita no painel de memória (revogar, editar…): avisa a sessão em andamento, sem pedir resposta."""
        text = " ".join((text or "").split())[:400]
        if text:
            self._notify_model(f"(Atualização da memória, não responda a este texto: {text}.)")

    def operator_instruction(self, text: str) -> bool:
        """Instrução do operador: vale na hora para a IA (item na sessão) e fica gravada até ser revogada."""
        text = " ".join((text or "").split())[:300]
        if not text:
            return False
        m = self._mem()
        if m is not None:
            m.upsert_instruction(text, actor="operator", session_id=self.session_id)
            self.mem_event("operator", "instruction", text)
        self._notify_model(f"(Nova instrução do operador, vale até ser revogada; siga-a e não responda a este texto: {text})")
        return True

    def _notify_model(self, text: str) -> None:
        """Aviso de contexto ao modelo. Em teleoperado NADA sai (custo zero); fica adiado e chega na volta."""
        if self._snap.mode == "teleoperado":
            self._deferred.append(text)
            del self._deferred[:-MAX_DEFERRED]
            return
        if self._client is not None and self._client.connected:
            self._send({"type": "conversation.item.create", "item": {"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": text}]}})

    def _flush_deferred(self) -> None:
        pending, self._deferred = self._deferred, []
        for text in pending:
            try:
                self._notify_model(text)
            except ia.AIError:
                pass

    def _on_closed(self, reason: str) -> None:
        self.connection = "desconectado"
        st = self._snap.state
        if st in ("listening", "capturing", "thinking", "speaking"):
            self._mic_stop.set()
            self.player.clear()
            self._fail(self._gen, f"A conexão com o {self.label} caiu. Vou reconectar na próxima pergunta.")

    def _send(self, event: dict) -> None:
        if self._client is None:
            raise ia.AIError("network", "Sem conexão com a Realtime.")
        try:
            self._client.send(event)
        except rt.TransportClosed as error:
            raise ia.AIError("network", f"A conexão com o {self.label} caiu. Tente de novo.") from error

    # ---- comandos -----------------------------------------------------------------------
    # ---- conversa contínua (microfone travado) ----------------------------------------------------
    def set_continuous(self, on: bool) -> None:
        """Microfone sempre aberto: a pessoa fala quando quiser e pode interromper a IA com a voz."""
        on = bool(on)
        if on == self.continuous:
            return
        self.continuous = on
        self._set(continuous=on)
        if on:
            self._mic_stop.set()                          # encerra uma escuta manual em andamento
            self._conv_stop = threading.Event()
            self._conv_thread = threading.Thread(target=self._conversation_loop, args=(self._conv_stop,),
                                                 name="conversa-continua", daemon=True)
            self._conv_thread.start()
        else:
            self._conv_stop.set()
            if self._snap.state == "listening":
                with self._lock:
                    self._gen += 1
                self._set(state="idle", message="", message_kind="")

    def _conversation_loop(self, stop: threading.Event) -> None:
        while not stop.is_set():
            if self._snap.mode == "teleoperado" or self._op_live:
                stop.wait(0.1)                            # o operador está com a palavra
                continue
            try:
                self._ensure_session(self._gen)
                if self._snap.state == "connecting":
                    self._set(state="idle")
            except Exception as error:
                self._set(state="idle", message=str(error), message_kind="warning")
                stop.wait(2.0)
                continue
            turn = {"gen": None, "t0": None}

            def on_start():
                if self._snap.state in ("thinking", "speaking", "capturing"):
                    self._interrupt()                     # a pessoa falou por cima: a IA para na hora
                with self._lock:
                    self._gen += 1
                    g = self._gen
                turn["gen"], turn["t0"] = g, time.perf_counter()
                self._new_turn(g, turn["t0"])
                self._set(g, state="listening", question="", answer="", message="", message_kind="", timings={})
                try:
                    self._client.send({"type": "input_audio_buffer.clear"})
                except (rt.TransportClosed, AttributeError):
                    pass

            def on_chunk(pcm):
                g = turn["gen"]
                if g is not None and self._alive(g) and self._client is not None and self._client.connected:
                    try:
                        self._client.send({"type": "input_audio_buffer.append", "audio": rt.b64_pcm(pcm)})
                    except rt.TransportClosed:
                        pass
            try:
                listen = getattr(self.mic, "listen", None)
                reason = (listen(stop, on_chunk, on_speech_start=on_start, wait_for_speech=True) if listen
                          else self.mic.run(stop, on_chunk))
            except VoiceError as error:
                self._set(state="idle", message=str(error), message_kind="warning")
                stop.wait(2.0)
                continue
            g = turn["gen"]
            if g is None or not self._alive(g):
                continue
            if reason != "speech" or stop.is_set():
                try:
                    self._client.send({"type": "input_audio_buffer.clear"})
                except (rt.TransportClosed, AttributeError):
                    pass
                self._set(g, state="idle")
                continue
            t1 = time.perf_counter()
            image, note = self._attach_vision(g, safe=True)
            try:
                self._send({"type": "input_audio_buffer.commit"})
                if image is not None:
                    self._send_image(image)
                elif note:
                    self._send({"type": "conversation.item.create", "item": {"type": "message", "role": "user", "content": [
                        {"type": "input_text", "text": NO_VISION_NOTE}]}})
                self._respond(g, t1, dict(escuta=t1 - turn["t0"]))
            except ia.AIError as error:
                self._fail(g, str(error))
            # volta direto a ouvir: se a pessoa falar durante a resposta, interrompe (barge-in)

    def press_voice(self) -> str:
        if self.continuous:
            self.set_continuous(False)                     # toque/Espaço com o microfone travado: encerra
            return "unlock"
        if self._snap.mode == "teleoperado":
            return "teleop"                                  # em silêncio: quem está na frente não vê que há operador
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
        if self._snap.mode == "teleoperado":
            return False
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
        self._conv_stop.set()
        self.continuous = False
        self.cancel()
        self._join(1.0)
        if self._client is not None:
            self._client.close()
        self.player.close()

    def _mark_stale(self, rid) -> None:
        if rid and rid not in self._stale_rids:
            self._stale_rids.append(rid)
            del self._stale_rids[:-MAX_STALE_RIDS]

    def _interrupt(self) -> None:
        """Barge-in: cancela a geração, descarta o áudio pendente e corta o histórico no que foi ouvido."""
        turn = self._turn
        played = self.player.clear()
        self.drop_ai_gestures()                              # gestos da IA ainda não lidos somem; o orquestrador é avisado
        if not turn:
            return
        was_responding = turn.get("responding")
        turn["responding"] = False
        turn["interrupted"] = True
        self._mark_stale(turn.get("response_id"))
        self._interrupted_at = time.perf_counter()
        if self._client is not None and self._client.connected:
            try:
                if was_responding:
                    self._client.send({"type": "response.cancel"})
                item = turn.get("audio_item")
                if item and played < self.player.received:
                    self._client.send({"type": "conversation.item.truncate", "item_id": item, "content_index": 0,
                                       "audio_end_ms": rt.ms(played)})
            except rt.TransportClosed:
                pass
        self._drop_images()

    # ---- fluxos (thread de trabalho) ---------------------------------------------------------
    def _new_turn(self, gen, t0):
        self._turn = dict(gen=gen, t0=t0, responding=False, audio_item=None, first_audio=None, done=False,
                          text="", question="", interrupted=False, had_audio=False, id=uuid.uuid4().hex[:10])
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
        image, note = self._attach_vision(gen)
        self._send({"type": "input_audio_buffer.commit"})
        if image is not None:
            self._send_image(image)
        elif note:
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
        self.mem_event("user", "text", question, key=self._turn["id"] + ":q")
        self._set(gen, timings={}, question=question, answer="")
        recovered = self._rag_item(gen, question)       # busca com prazo; cancelar durante a busca descarta tudo
        if not self._alive(gen):
            return
        if recovered:
            rid = "rag_" + uuid.uuid4().hex[:24]
            self._turn.setdefault("rag_items", []).append(rid)
            self._send({"type": "conversation.item.create", "item": {"id": rid, "type": "message", "role": "user",
                                                                    "content": [{"type": "input_text", "text": recovered}]}})
        image, note = self._attach_vision(gen)
        text = question + (" " + NO_VISION_NOTE if note else "")
        self._send({"type": "conversation.item.create", "item": {"type": "message", "role": "user",
                                                                "content": [{"type": "input_text", "text": text}]}})
        if image is not None:
            self._send_image(image)                      # item separado: sai da sessão depois, a pergunta fica
        self._respond(gen, t0, {})

    def _attach_vision(self, gen, safe: bool = False):
        """(imagem, avisar_pausa). Modo "demanda": nada vai com a pergunta (a IA chama olhar se precisar, e só
        então a foto é enviada: sem gasto de tokens com imagem em conversa comum). Modo "sempre": foto por pergunta."""
        if self.vision_mode == "demanda":
            return None, False
        if not self._snap.vision:
            return None, True
        try:
            return self._capture(gen), False
        except ia.AIError:
            if not safe:
                raise
            return None, True

    def identity_note(self, name: Optional[str]) -> None:
        """Rosto reconhecido: uma linha de texto (poucos tokens) no contexto, sem pedir resposta."""
        name = " ".join((name or "").split())[:40]
        if name and self._client is not None and self._client.connected:
            try:
                self._send({"type": "conversation.item.create", "item": {"type": "message", "role": "user", "content": [
                    {"type": "input_text", "text": f"(Contexto, não responda a este texto: reconheci {name} na frente da câmera.)"}]}})
            except ia.AIError:
                pass

    def _data_url(self, image) -> str:
        return "data:image/jpeg;base64," + base64.b64encode(ia.encode_jpeg(image, max_side=self._image_side,
                                                                           quality=78)).decode()

    def _send_image(self, image) -> None:
        item_id = "img_" + uuid.uuid4().hex[:24]
        self._turn.setdefault("image_items", []).append(item_id)
        self._send({"type": "conversation.item.create", "item": {
            "id": item_id, "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": "(Imagem da webcam no momento da pergunta acima.)"},
                        {"type": "input_image", "image_url": self._data_url(image)}]}})

    def _drop_images(self) -> None:
        """Economia: a foto só serve à resposta dela. Depois sai da sessão e não é recobrada nas próximas."""
        ids = self._turn.get("image_items", []) + self._turn.get("rag_items", [])      # a foto e o dado recuperado só servem a esta resposta
        self._turn["image_items"], self._turn["rag_items"] = [], []
        for item_id in ids:
            try:
                self._client.send({"type": "conversation.item.delete", "item_id": item_id})
            except (rt.TransportClosed, AttributeError):
                pass

    def _respond(self, gen, t_end, timings, response: Optional[dict] = None) -> None:
        if self._turn.get("ended"):
            return
        self._turn.update(t_end=t_end, timings=timings, responding=True)
        self._set(gen, state="thinking", answer="", action="", emotion="neutral")
        self.player.begin()
        self.voice_mask.reset()
        try:
            self.player.start()
        except VoiceError as error:
            self._turn["no_audio"] = str(error)
        if not self._alive(gen):                          # cancelada (ex.: operador assumiu) antes de pedir a resposta
            return
        if self._handoff:                                 # o operador agiu desde a última resposta: resumo antes de responder
            self._handoff = False
            self._send_memory_context(handoff=True)
        self._send({"type": "response.create", **({"response": response} if response else {})})

    # ---- eventos do servidor (thread de leitura) -------------------------------------------------
    def _on_event(self, event: dict) -> None:
        kind = event.get("type", "")
        if kind != "response.output_audio.delta" and kind != "input_audio_buffer.append":
            self.events_log = (self.events_log + [kind])[-200:]
        turn, gen = self._turn, self._turn.get("gen", -1)
        rid = event.get("response_id") or (event.get("response") or {}).get("id")
        if rid and rid in self._stale_rids and kind != "response.created":
            return                                           # resposta cancelada/interrompida: nada dela vale mais
        if turn and rid and kind != "response.created" and turn.get("response_id") and rid != turn["response_id"]:
            if kind == "response.done":                      # resposta antiga (cancelada): só registra funções
                return
            return                                           # eventos atrasados de uma resposta interrompida
        if kind == "conversation.item.input_audio_transcription.completed":
            text = (event.get("transcript") or "").strip()
            if text and turn:
                turn["question"] = text
                if is_end_command(text) and self._snap.mode != "teleoperado":
                    threading.Thread(target=self._end_by_voice, args=(turn,), daemon=True).start()
                    return
                self.mem_event("user", "utterance", text, key=turn.get("id", "") + ":q")
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
            pcm = self._mask(pcm, "ai")                        # mesma "garganta" da voz do operador; None = máscara falhou
            turn["audio_item"] = turn.get("audio_item") or event.get("item_id")
            turn["had_audio"] = True
            if pcm is None:
                turn["voice_down"] = True                      # nunca toca o original: descarta o bloco
            else:
                turn.setdefault("pcm", []).append(pcm)
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
        if self._turn.get("interrupted") or not self._alive(self._turn.get("gen", -1)):
            return                                           # resposta cancelada/superada: a chamada tardia não vale
        try:
            args = json.loads(arguments or "{}")
        except ValueError:
            args = {}
        if name == "olhar":
            return self._call_look(call_id)
        if name in ("lembrar_rosto", "esquecer_rosto"):
            return self._call_face(call_id, name, args)
        if name == "lembrar":
            return self._call_remember(call_id, args)
        emotion = str(args.get("emocao", "neutral")).lower()
        action = ia.ACTIONS.get(str(args.get("acao", "nenhuma")).lower()) or ""
        self._calls[call_id] = dict(name=name, emotion=emotion if emotion in ia.EMOTIONS else "neutral", action=action)
        self._set(self._turn.get("gen", -1), emotion=self._calls[call_id]["emotion"], action=action,
                  expr_version=self._snap.expr_version + 1)
        gesture = str(args.get("gesto", "nenhum")).lower()
        if gesture in GESTURES and self._alive(self._turn.get("gen", -1)):
            self.request_gesture(gesture, side=args.get("lado"))

    def _call_look(self, call_id) -> None:
        """A IA pediu para olhar: foto na hora (única), enviada junto da resposta da ferramenta."""
        gen = self._turn.get("gen", -1)
        call = self._calls[call_id] = dict(name="olhar", needs_response=True)
        if not self._snap.vision:
            call["result"] = dict(ok=False, motivo="A visão está pausada pelo usuário. Diga com graça que está de olhos fechados.")
            return
        try:
            call["image"] = self._capture(gen)
            self._set(gen, state="thinking")
            call["result"] = dict(ok=True, nota="A foto da webcam deste instante vem na próxima mensagem.")
        except ia.AIError:
            call["result"] = dict(ok=False, motivo="A câmera está indisponível agora.")

    def _call_remember(self, call_id, args) -> None:
        """A IA pediu `lembrar`: a busca roda numa thread própria (a de leitura da Realtime nunca espera) com prazo e plano B."""
        turn = self._turn
        gen = turn.get("gen", -1)
        call = self._calls[call_id] = dict(name="lembrar", needs_response=True)
        m = self._mem()
        asm = self.rag_assembler()
        consulta = " ".join(str(args.get("consulta", "")).split())[:200]
        usuario = m.user_id if m is not None else None            # identidade SÓ da sessão: o que o modelo passar nos argumentos é ignorado
        excluir = (turn.get("question"),)

        def work():
            if asm is None:
                call["result"] = dict(itens=[], motivo_fallback="memoria_indisponivel", nada_encontrado=True)
                return
            try:
                call["result"] = executar_lembrar(asm, usuario, consulta, lambda: (not self._alive(gen)) or bool(turn.get("interrupted")), excluir)
            except Exception:
                call["result"] = dict(itens=[], motivo_fallback="erro_recuperacao", nada_encontrado=True)
        call["thread"] = threading.Thread(target=work, name="lembrar", daemon=True)
        call["thread"].start()

    def _lembrar_gemini(self, name, args) -> dict:
        """Chamado pela thread leitora do ADAPTADOR do Gemini (não pela interface nem pelo áudio): o resultado real volta na mesma vez."""
        turn = self._turn
        gen = turn.get("gen", -1)
        m = self._mem()
        asm = self.rag_assembler()
        if m is None or asm is None:
            return dict(itens=[], motivo_fallback="memoria_indisponivel", nada_encontrado=True)
        n = turn["lembrar_n"] = turn.get("lembrar_n", 0) + 1
        if n > asm.cfg.max_continuacoes_lembrar:
            return dict(RESULTADO_LIMITE)
        consulta = " ".join(str((args or {}).get("consulta", "")).split())[:200]
        return executar_lembrar(asm, m.user_id, consulta, lambda: (not self._alive(gen)) or bool(turn.get("interrupted")), (turn.get("question"),))

    def _call_face(self, call_id, name, args) -> None:
        call = self._calls[call_id] = dict(name=name, needs_response=True)
        faces = self.faces
        person = " ".join(str(args.get("nome", "")).split())
        if faces is None or faces.error:
            call["result"] = dict(ok=False, motivo="O reconhecimento de rostos não está disponível neste computador.")
        elif name == "esquecer_rosto":
            if person.lower() in ("todos", "todas", "tudo"):
                call["result"] = dict(ok=True, apagados=faces.book.forget_all())
            else:
                call["result"] = faces.forget(person)
        elif args.get("confirmado") is True:
            def work():
                call["result"] = faces.confirm(person, self._turn.get("id", ""))
                if call["result"].get("ok"):
                    self._set(self._turn.get("gen", -1), message=f"Rosto de {call['result']['nome']} guardado.",
                              message_kind="info")
            call["thread"] = threading.Thread(target=work, name="rostos-cadastro", daemon=True)
            call["thread"].start()
        else:
            call["result"] = faces.ask(person, self._turn.get("id", ""))

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
        calls = self._calls
        pending = [cid for cid, c in calls.items() if not c.get("answered")]
        if any(calls[cid].get("name") == "lembrar" for cid in pending):
            # `lembrar` espera a busca (com prazo): nunca na thread de leitura da Realtime
            threading.Thread(target=self._answer_calls_thread, args=(gen, turn, calls, pending, outputs), name="lembrar-resposta", daemon=True).start()
            return
        self._answer_calls(gen, turn, calls, pending, outputs)

    def _answer_calls_thread(self, gen, turn, calls, pending, outputs) -> None:
        try:
            self._answer_calls(gen, turn, calls, pending, outputs)
        except ia.AIError as error:
            if self._alive(gen):
                self._fail(gen, str(error))
        except Exception:
            pass

    def _answer_calls(self, gen, turn, calls, pending, outputs) -> None:
        """Responde às ferramentas da resposta que acabou (resultado ao modelo) e decide se há continuação ou se o turno terminou."""
        try:
            lembrar = [cid for cid in pending if calls[cid].get("name") == "lembrar"]
            for cid in pending:
                call = calls[cid]
                call["answered"] = True
                if call.get("thread") is not None:
                    call["thread"].join(3.0)               # cadastro de rosto ~0,5 s; `lembrar` até o prazo da busca
            if not self._alive(gen) or turn.get("interrupted") or turn is not self._turn:
                return                                     # cancelado (Pare, troca de modo, nova sessão) durante a espera: nada é enviado
            if lembrar:                                    # teto de continuações por pergunta (padrão 2): estourou = resposta honesta, sem novo pedido
                asm = self.rag
                limit = asm.cfg.max_continuacoes_lembrar if asm is not None else 2
                if turn.get("lembrar_cont", 0) >= limit:
                    for cid in lembrar:
                        calls[cid]["result"] = dict(RESULTADO_LIMITE)
                        calls[cid]["needs_response"] = False
                        calls[cid]["limite"] = True
                else:
                    turn["lembrar_cont"] = turn.get("lembrar_cont", 0) + 1
            for cid in pending:
                call = calls[cid]
                self._send({"type": "conversation.item.create", "item": {
                    "type": "function_call_output", "call_id": cid,
                    "output": json.dumps(call.get("result", {"ok": True}), ensure_ascii=False)}})
                if call.get("image") is not None:
                    self._send_image(call.pop("image"))
            has_message = any(i.get("type") == "message" for i in outputs) or turn.get("had_audio")
            needs_more = any(calls[cid].get("needs_response") for cid in pending)
            cosmetic_only = bool(pending) and all(calls[cid].get("name") in COSMETIC_TOOLS for cid in pending)
            if pending and not has_message and not needs_more and any(calls[cid].get("limite") for cid in pending):
                self._end_without_speech(gen)
                return
            if pending and not has_message and not needs_more and cosmetic_only:
                # só a expressão veio: no máximo UMA continuação dedicada à fala (e nunca com novas ferramentas)
                used = turn.get("cosmetic_followups", 0)
                if not getattr(getattr(self._client, "_transport", None), "continues_after_tools", False) and used < MAX_COSMETIC_FOLLOWUPS:
                    turn["cosmetic_followups"] = used + 1
                    self._send({"type": "response.create", "response": {"tool_choice": "none"}})
                    return
                self._end_without_speech(gen)
                return
            if pending and (needs_more or not has_message):
                self._send({"type": "response.create"})          # ferramenta funcional: o resultado pede nova resposta
                return
        except ia.AIError:
            if not self._alive(gen):
                return                                       # conexão já foi encerrada junto com a pergunta
            raise
        turn["responding"] = False
        turn["done"] = True
        self._drop_images()
        if turn.get("text"):
            self.history = (self.history + [(turn.get("question") or self._snap.question, turn["text"])])[-6:]
            self.mem_event("ai", "response", turn["text"], key=turn.get("id", "") + ":a")
        self._drain_thread = threading.Thread(target=self._finish_when_played, args=(gen,), daemon=True)
        self._drain_thread.start()

    def _end_without_speech(self, gen) -> None:
        """A IA só mexeu o rosto e não falou: estado terminal recuperável, com aviso neutro."""
        turn = self._turn
        turn["responding"] = False
        turn["done"] = True
        self._drop_images()
        self._set(gen, state="idle", message=NO_SPEECH_NOTICE, message_kind="warning")

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
        pcm = turn.get("pcm") or []
        if pcm and sum(len(c) for c in pcm) <= 24000 * 90:       # até 90 s (≈4 MB); só respostas completas
            self._last_audio = list(pcm)
        timings = dict(self._snap.timings)
        timings["resposta_completa"] = time.perf_counter() - turn.get("t_end", turn["t0"])
        warning = turn.get("no_audio", "")
        if turn.get("voice_down"):
            self._set(gen, state="idle", timings=timings, message=VOICE_DOWN_NOTICE, message_kind="warning")
            self._write_log(dict(pergunta=turn.get("question") or self._snap.question, resposta=turn.get("text", ""),
                                 emocao=self._snap.emotion, acao=self._snap.action, aviso="voz indisponível"))
            return
        self._write_log(dict(pergunta=turn.get("question") or self._snap.question, resposta=turn.get("text", ""),
                             emocao=self._snap.emotion, acao=self._snap.action, modelo=self._session_key[0] if self._session_key else "",
                             tempos={k: round(v, 3) for k, v in timings.items()}, aviso=warning))
        if warning:
            self._set(gen, state="idle", timings=timings, message=warning + " Resposta só em texto.", message_kind="warning")
        else:
            self._set(gen, state="idle", timings=timings, message="", message_kind="done")


    # ---- controle remoto (etapa 2): modos, fala do operador ----------------------------------
    def set_mode(self, mode: str) -> bool:
        """Autônomo (IA conversa), assistido (IA conversa; operador intervém) ou teleoperado (só o operador fala)."""
        if mode not in MODES:
            return False
        if mode == "teleoperado" and self._snap.state in ("listening", "capturing", "thinking", "speaking") \
                and not self._op_live:
            self.cancel()                                     # o operador assume: a IA para na hora
        previous = self._snap.mode
        self._set(mode=mode, message="", message_kind="")
        if mode == "teleoperado" and previous != "teleoperado":
            self.drop_ai_gestures()                       # nada da IA (nem atrasado) chega ao corpo em teleoperado
        if mode != previous:
            self.mem_event("system", "mode_change", mode, previous=previous)
            m = self._mem()
            if m is not None:
                m.set_session_mode(self.session_id, mode)
            if previous == "teleoperado":
                self._handoff = True                      # a IA retoma: recebe resumo antes da próxima resposta
                self._flush_deferred()
        return True

    def set_operator(self, name: str) -> None:
        self._set(operator=name or "")
        if not name:
            self._op_live = False
            self._op_ended()
            if self._snap.mode == "teleoperado":
                self._set(mode="autonomo")                    # operador saiu: a Ayra volta a conversar sozinha

    def operator_say(self, text: str) -> bool:
        """Texto do operador: no teleoperado, voz local gratuita (Windows); senão, a IA fala a frase."""
        text = " ".join((text or "").split())[:400]
        if not text:
            return False
        self.mem_event("operator", "say", text)
        self._handoff = True
        if self._snap.state in ("thinking", "speaking", "listening", "capturing"):
            self._interrupt()
            self._mic_stop.set()
        cfg = self._config_loader()
        if (self._snap.mode == "teleoperado" or not getattr(cfg, "live_configured", cfg.configured)
                or self._eleven(cfg) is not None):
            self._start(lambda gen: self._local_say(gen, text), "thinking")
        else:
            self._start(lambda gen: self._ai_say(gen, text), "thinking")
        return True

    def _eleven(self, cfg=None):
        """Voz única (ElevenLabs) se houver chave e estiver ligada; senão None."""
        cfg = cfg or self._config_loader()
        if not (getattr(cfg, "eleven_configured", False) and getattr(cfg, "eleven_on", False)):
            return None
        ident = (cfg.eleven_key, cfg.eleven_voice, cfg.eleven_model)
        if self._eleven_cache[0] != ident:
            if self.eleven_factory is not None:
                tts = self.eleven_factory(cfg)
            else:
                import eleven
                tts = eleven.ElevenTTS(*ident)
            self._eleven_cache = (ident, tts)
        return self._eleven_cache[1]

    def _eleven_say(self, gen, text) -> bool:
        """Fala `text` pela voz única, em streaming. False = não deu (cai na voz local)."""
        tts = self._eleven()
        if tts is None:
            return False
        import eleven
        self.player.begin()
        try:
            self.player.start()
        except VoiceError:
            return False
        fed = False
        try:
            for pcm in tts.stream(text, lambda: not self._alive(gen)):
                if not self._alive(gen):
                    break
                pcm = self._mask(pcm, "operator")
                if pcm is None:
                    self._voice_down = True
                    continue
                self.player.feed(pcm)
                if not fed:
                    fed = True
                    self._set(gen, state="speaking", spoken_offline=False)
        except eleven.ElevenError as error:
            self._set(gen, message=str(error), message_kind="warning")
            if not fed:
                return False
        while self._alive(gen) and self.player.pending() > 0:
            time.sleep(0.03)
        return fed

    def _local_say(self, gen, text) -> None:
        from filtro_voz import synthesize_local
        self._new_turn(gen, time.perf_counter())
        self._set(gen, question="", answer=text, emotion="neutral", action="", timings={})
        self.voice_mask.reset()
        if self._eleven_say(gen, text):
            if self._alive(gen):
                self._set(gen, state="idle", message_kind="done")
            return
        pcm = synthesize_local(text)
        if not self._alive(gen):
            return
        if pcm is None:
            self._set(gen, state="speaking", spoken_offline=True)
            time.sleep(min(8.0, 0.35 + 0.065 * len(text)))       # boca simulada pelo tempo da frase
        else:
            if self.voice_mask.active:
                self.voice_mask.reset()
                parts = [self._mask(pcm[i:i + 960], "operator") for i in range(0, len(pcm), 960)]
                if any(part is None for part in parts):
                    self._voice_down = True
                    self._set(gen, state="idle", message=VOICE_DOWN_NOTICE, message_kind="warning")
                    return
                pcm = np.concatenate(parts)
            self.player.begin()
            try:
                self.player.start()
            except VoiceError as error:
                self._set(gen, state="idle", message=str(error), message_kind="warning")
                return
            self.player.feed(pcm)
            self._set(gen, state="speaking", spoken_offline=False)
            while self._alive(gen) and self.player.pending() > 0:
                time.sleep(0.03)
        if self._alive(gen):
            self._set(gen, state="idle", message_kind="done")

    def _ai_say(self, gen, text) -> None:
        t0 = time.perf_counter()
        self._ensure_session(gen)
        if not self._alive(gen):
            return
        self._new_turn(gen, t0)
        self._turn["question"] = ""
        self._set(gen, question="", timings={})
        self._respond(gen, t0, {}, response={"instructions": _instructions(vision_mode()) + " Agora diga exatamente esta frase, "
                                             "com naturalidade e sem acrescentar nada: «" + text + "»"})

    _voice_down = False

    def _mask(self, pcm, source: str):
        """Áudio mascarado, o original (perfil Original) ou None (máscara ativa e falhou: descartar)."""
        return self.voice_mask.process(pcm, source)

    @property
    def robot_level(self) -> float:
        return self.voice_mask.level

    @robot_level.setter
    def robot_level(self, level: float) -> None:
        self.voice_mask.set_level(level)
        if self.voice_mask.active:
            self.voice_mask.warm_async()                # carrega o filtro em segundo plano (uma vez)

    @property
    def robot_voice(self) -> bool:
        return self.voice_mask.active

    @robot_voice.setter
    def robot_voice(self, on: bool) -> None:
        self.robot_level = (self.robot_level or 0.6) if on else 0.0

    @property
    def voice_profile(self) -> str:
        return self.voice_mask.profile

    def voice_diag(self) -> dict:
        """Diagnóstico da voz (só para o painel privado do operador)."""
        return self.voice_mask.diag()

    def operator_audio(self, pcm: np.ndarray) -> None:
        """Voz ao vivo do operador (24 kHz), enquanto ele fala ("Falar" segurado ou travado).

        Detecta voz (não só ruído): com o microfone travado, o silêncio do operador não ocupa o alto-falante,
        não trava a IA e não mexe a boca. A voz interrompe a IA e sai pelo robô, com 150 ms de pré-roll.
        """
        now = time.perf_counter()
        pcm = np.asarray(pcm, dtype=np.int16)
        rms = float(np.sqrt(np.mean((pcm.astype(np.float32) / 32768.0) ** 2))) if len(pcm) else 0.0
        if self._op_floor is None:
            self._op_floor = min(rms, 0.003)
        elif rms < 2.5 * self._op_floor:
            self._op_floor = 0.97 * self._op_floor + 0.03 * rms        # ruído de fundo do operador
        else:
            self._op_floor *= 1.002
        voice = rms > max(0.012, 3.0 * self._op_floor)
        if voice:
            self._op_voice = now
        if not self._op_live:
            self._op_pre = (self._op_pre + [pcm])[-8:]                  # ~160 ms antes da voz (não corta o início da frase)
            if not voice:
                return
            if self._snap.state in ("listening", "capturing", "thinking", "speaking"):
                self._interrupt()
                self._mic_stop.set()
            with self._lock:
                self._gen += 1
            self._op_live = True
            self._op_t0 = time.time()
            self.player.begin()
            self.voice_mask.reset()
            try:
                self.player.start()
            except VoiceError:
                pass
            self._set(state="speaking", question="", answer="", emotion="neutral", action="",
                      spoken_offline=False, timings={}, message="", message_kind="")
            chunks, self._op_pre = self._op_pre[:-1], []
        else:
            chunks = []
            if now - self._op_voice > 0.7:                              # pausa: devolve a palavra
                self._op_live = False
                self._op_ended()
                self._set(state="idle", message_kind="done")
                return
        for chunk in chunks + [pcm]:
            chunk = self._mask(chunk, "operator")
            if chunk is None:
                self._voice_down = True                         # máscara falhou: silêncio, nunca a voz original
                continue
            self.player.feed(chunk)
        self._op_last = now

    def _op_ended(self) -> None:
        """Fim de um trecho de fala do operador (sem transcrição): registra a duração e marca o resumo."""
        started, self._op_t0 = self._op_t0, None
        if started is not None:
            self._handoff = True
            self.mem_event("operator", "voice", "", segundos=round(time.time() - started, 1))

    def drive_robot(self, robot, now: float, prev_state: str) -> str:
        if self._op_live and time.perf_counter() - self._op_last > 0.6 and self.player.pending() == 0:
            self._op_live = False                              # operador soltou "Falar"
            self._op_ended()
            self._set(state="idle", message_kind="done")
        return super().drive_robot(robot, now, prev_state)


def check_connection(cfg, transport_factory=None) -> tuple:
    """Teste rápido da aba API: abre a sessão do provedor escolhido e fecha. Devolve (ok, mensagem curta)."""
    gemini = getattr(cfg, "provider", "openai") == "gemini"
    if not (cfg.gemini_configured if gemini else cfg.configured):
        return False, "sem chave configurada"
    if transport_factory is None:
        if gemini:
            import gemini_live
            transport_factory = lambda: gemini_live.connect(cfg)
        else:
            transport_factory = lambda: rt.WebSocketTransport(cfg.realtime_url.format(model=cfg.realtime_model),
                                                              cfg.api_key, cfg.timeout)
    factory = transport_factory
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
        model = cfg.gemini_model if gemini else cfg.realtime_model
        return True, f"conectou em {time.perf_counter() - t0:.1f} s · {model}".replace(".", ",", 1)
    except rt.TransportClosed as error:
        return False, str(error)[:90]
    finally:
        client.close()
