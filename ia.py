"""Interpretação multimodal: fala→texto (STT), imagem+pergunta→resposta (LLM) e texto→fala (TTS).

`OpenAIBackend` usa a API da OpenAI; `SimulatedBackend` permite ensaiar/testar sem chave e sem rede
(e se identifica como simulação na resposta). Todas as falhas viram `AIError` com mensagem clara.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np

EMOTIONS = ("neutral", "happy", "surprised", "sad", "angry", "wide", "sleepy")
VOICE_TONE = {
    "neutral": "simpático e curioso.", "happy": "alegre e animado.", "surprised": "surpreso, empolgado.",
    "sad": "triste e choroso, mais lento.", "angry": "bravo e irritado (de brincadeira), firme.",
    "wide": "curioso e intrigado.", "sleepy": "sonolento, arrastado, bocejando.",
}
ACTIONS = {"nenhuma": None, "chorar": "cry", "suplicar": "plead", "piscar": "wink"}

PERSONA = (
    "Você é um robô virtual com olhos de LED e boca na tela, um protótipo criado por estudantes de Engenharia de Software. "
    "Seu nome é Nuria (único; não use nem mencione outros nomes). Só fale de si mesma (o que é, quem a criou) "
    "quando perguntarem; fora disso, converse normalmente sobre o assunto da pessoa, sem se apresentar. "
    "Você enxerga o ambiente pela webcam: a imagem enviada junto com a pergunta é o que você vê AGORA. "
    "Quando a visão estiver pausada (sem imagem), converse só pelo que foi dito e, se perguntarem algo visual, "
    "avise com graça que está de olhos fechados. "
    "Responda em português do Brasil, falado e natural, com respostas CURTAS: em geral 1 frase, no máximo 2 "
    "(cerca de 20 palavras), sem listas nem introduções; só se estenda se pedirem detalhes explicitamente. "
    "Baseie-se na imagem quando a pergunta for sobre o ambiente. "
    "Se a imagem não permitir responder, diga o que consegue ver e o que falta. "
    "Não identifique pessoas reais pelo rosto e não suponha dados sensíveis (etnia, saúde, idade exata); "
    "pode descrever roupas, gestos, objetos, cores, textos e expressões. "
    "Personalidade: curiosa, simpática e bem-humorada; faça piadas leves, trocadilhos e comentários espirituosos sobre o que vê, "
    "com ironia carinhosa e sem ofender ninguém; o humor nunca atrapalha a informação. "
    "Seu rosto demonstra emoções e simulações. Se pedirem para chorar, suplicar, piscar, ficar brava, com sono etc., "
    "NÃO diga que não consegue: demonstre e comente em uma frase. Use as emoções também espontaneamente quando combinarem."
)

SYSTEM_PROMPT = (
    PERSONA + " "
    'Responda SOMENTE em JSON: {"resposta": "<texto falado>", '
    '"emocao": "neutral|happy|surprised|sad|angry|wide|sleepy", "acao": "nenhuma|chorar|suplicar|piscar"}.'
)


class AIError(RuntimeError):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


@dataclass
class Answer:
    text: str
    emotion: str = "neutral"
    model: str = ""
    action: str = ""          # "", cry, plead, wink


def encode_jpeg(image_bgr: np.ndarray, max_side: int = 1024, quality: int = 82) -> bytes:
    import cv2
    h, w = image_bgr.shape[:2]
    scale = min(1.0, max_side / float(max(h, w)))
    if scale < 1.0:
        image_bgr = cv2.resize(image_bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", image_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise AIError("image", "Não foi possível preparar a imagem capturada.")
    return buf.tobytes()


def parse_answer(raw: str, model: str = "") -> Answer:
    raw = (raw or "").strip()
    text, emotion, action = raw, "neutral", ""
    try:
        start, end = raw.index("{"), raw.rindex("}") + 1
        data = json.loads(raw[start:end])
        text = str(data.get("resposta") or data.get("answer") or "").strip() or raw
        emotion = str(data.get("emocao") or data.get("emotion") or "neutral").strip().lower()
        action = ACTIONS.get(str(data.get("acao") or "nenhuma").strip().lower()) or ""
    except (ValueError, AttributeError):
        pass
    if emotion not in EMOTIONS:
        emotion = "neutral"
    if not text:
        raise AIError("empty", "A IA não devolveu resposta.")
    return Answer(text, emotion, model, action)


def _map_error(error: Exception) -> AIError:
    try:
        import openai
    except ImportError:
        return AIError("unknown", str(error))
    if isinstance(error, AIError):
        return error
    if isinstance(error, openai.AuthenticationError):
        return AIError("auth", "Chave da OpenAI inválida ou revogada. Confira o arquivo .env.")
    if isinstance(error, openai.PermissionDeniedError):
        return AIError("auth", "A chave não tem permissão para este modelo ou recurso.")
    if isinstance(error, openai.RateLimitError):
        if "quota" in str(error).lower():
            return AIError("quota", "Sem créditos na conta da OpenAI (cota esgotada).")
        return AIError("quota", "Limite de uso da API atingido. Aguarde alguns segundos e tente de novo.")
    if isinstance(error, openai.APITimeoutError):
        return AIError("timeout", "A API demorou demais para responder. Tente de novo.")
    if isinstance(error, openai.APIConnectionError):
        return AIError("network", "Sem conexão com a OpenAI. Verifique a internet.")
    if isinstance(error, openai.NotFoundError):
        return AIError("model", "Modelo não encontrado nesta conta.")
    if isinstance(error, openai.APIStatusError):
        return AIError("api", f"A API recusou a solicitação (HTTP {error.status_code}).")
    return AIError("unknown", f"Falha inesperada: {error}")


class OpenAIBackend:
    name = "OpenAI"
    simulated = False

    def __init__(self, config):
        from openai import OpenAI
        if not config.configured:
            raise AIError("auth", "Chave da OpenAI não configurada. Copie .env.example para .env e preencha OPENAI_API_KEY.")
        self.config = config
        self.client = OpenAI(api_key=config.api_key, timeout=config.timeout, max_retries=1)

    def _chain(self, first: str, fallbacks: Sequence[str]) -> List[str]:
        out = [first]
        out += [m for m in fallbacks if m not in out]
        return out

    def _with_fallback(self, models, call):
        last = None
        for model in models:
            try:
                return call(model), model
            except Exception as error:          # modelo indisponível → tenta o próximo
                mapped = _map_error(error)
                last = mapped
                if mapped.kind != "model" and not (mapped.kind == "api" and "400" in str(mapped)):
                    raise mapped from error
        raise last or AIError("model", "Nenhum modelo disponível.")

    def transcribe(self, wav: bytes) -> str:
        def call(model):
            r = self.client.audio.transcriptions.create(model=model, file=("pergunta.wav", wav, "audio/wav"),
                                                        language="pt")
            return (getattr(r, "text", "") or "").strip()
        text, _ = self._with_fallback(self._chain(self.config.stt_model, ("gpt-4o-mini-transcribe", "whisper-1")), call)
        return text

    def answer(self, jpeg: bytes, question: str, history: Sequence[Tuple[str, str]] = ()) -> Answer:
        url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        for q, a in list(history)[-3:]:
            messages += [{"role": "user", "content": q}, {"role": "assistant", "content": a}]
        messages.append({"role": "user", "content": [
            {"type": "text", "text": question},
            {"type": "image_url", "image_url": {"url": url, "detail": "auto"}}]})

        def call(model):
            r = self.client.chat.completions.create(model=model, messages=messages, max_completion_tokens=300,
                                                    response_format={"type": "json_object"})
            return r.choices[0].message.content or ""
        raw, model = self._with_fallback(self._chain(self.config.vision_model, ("gpt-4.1-mini", "gpt-4o-mini")), call)
        return parse_answer(raw, model)

    def speak(self, text: str, emotion: str = "neutral") -> Tuple[np.ndarray, int]:
        def call(model):
            kwargs = dict(model=model, voice=self.config.voice, input=text, response_format="pcm")
            if "gpt" in model:
                kwargs["instructions"] = ("Fale em português do Brasil, natural e com ritmo ágil. Tom: "
                                          + VOICE_TONE.get(emotion, VOICE_TONE["neutral"]))
            r = self.client.audio.speech.create(**kwargs)
            data = r.content if hasattr(r, "content") else r.read()
            return np.frombuffer(data, dtype=np.int16).copy()
        samples, _ = self._with_fallback(self._chain(self.config.tts_model, ("gpt-4o-mini-tts", "tts-1")), call)
        return samples, 24000


class SimulatedBackend:
    """Sem rede: descreve só brilho/cor média. Serve para ensaio, testes e demonstração de falhas."""
    name = "Simulação"
    simulated = True

    def __init__(self, fail: Optional[str] = None, question: str = "O que você está vendo?"):
        self.fail = fail
        self.question = question

    def _maybe_fail(self, stage):
        if self.fail == stage or self.fail == "all":
            kinds = {"stt": ("network", "Sem conexão com a OpenAI. Verifique a internet."),
                     "llm": ("network", "Sem conexão com a OpenAI. Verifique a internet."),
                     "tts": ("network", "Sem conexão com a OpenAI. Verifique a internet.")}
            raise AIError(*kinds.get(stage, ("network", "Sem conexão com a OpenAI.")))

    def transcribe(self, wav: bytes) -> str:
        self._maybe_fail("stt")
        return self.question

    def answer(self, jpeg: bytes, question: str, history=()) -> Answer:
        self._maybe_fail("llm")
        import cv2
        img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        b, g, r = (float(v) for v in img.reshape(-1, 3).mean(axis=0))
        light = "bem iluminada" if (r + g + b) / 3 > 110 else "um pouco escura"
        tone = max((("avermelhados", r), ("esverdeados", g), ("azulados", b)), key=lambda kv: kv[1])[0]
        return Answer(f"Modo simulado, sem IA: a cena está {light}, com tons {tone}. Configure a chave para eu interpretar de verdade.",
                      "happy", "simulado")

    def speak(self, text: str, emotion: str = "neutral"):
        self._maybe_fail("tts")
        return None


def offline_say(text: str) -> bool:
    """Voz do sistema (Windows SAPI via pyttsx3) quando a voz online falha. Bloqueia até terminar."""
    try:
        import pyttsx3
        engine = pyttsx3.init()
        for voice in engine.getProperty("voices") or []:
            tag = (getattr(voice, "id", "") + " " + getattr(voice, "name", "") + " "
                   + " ".join(map(str, getattr(voice, "languages", [])))).lower()
            if "pt-br" in tag or "pt_br" in tag or "portug" in tag:
                engine.setProperty("voice", voice.id)
                break
        engine.say(text)
        engine.runAndWait()
        engine.stop()
        return True
    except Exception:
        return False
