"""Voz sintética única (ElevenLabs): a mesma voz para a IA e para qualquer operador.

Fala texto em streaming (PCM 24 kHz, o formato do alto-falante do app): o primeiro som sai antes de a
frase inteira ser gerada. Só texto sai do computador; a chave vem do `.env` e nunca entra em mensagens de erro.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Callable, Iterator, Optional

import numpy as np

BASE = "https://api.elevenlabs.io/v1/text-to-speech/{voice}/stream?output_format=pcm_24000"
DEFAULT_VOICE = "JBFqnCBsd6RMkjVDRZzb"          # voz pronta (premade) da ElevenLabs; troque com NURIA_ELEVEN_VOZ
DEFAULT_MODEL = "eleven_flash_v2_5"            # baixa latência, multilíngue (inclui português)
CHUNK = 4800                                    # 100 ms de áudio (24 kHz, 16 bits)


class ElevenError(RuntimeError):
    pass


class ElevenTTS:
    def __init__(self, key: str, voice: str = DEFAULT_VOICE, model: str = DEFAULT_MODEL, timeout: float = 15.0,
                 opener: Callable = urllib.request.urlopen):
        self._key, self.voice, self.model, self.timeout, self._opener = key, voice or DEFAULT_VOICE, model or DEFAULT_MODEL, timeout, opener

    def _request(self, text: str) -> urllib.request.Request:
        body = {"text": text, "model_id": self.model,
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75, "speed": 1.0}}
        if "v2_5" in self.model:
            body["language_code"] = "pt"
        return urllib.request.Request(BASE.format(voice=self.voice), data=json.dumps(body).encode("utf-8"), method="POST",
                                      headers={"xi-api-key": self._key, "Content-Type": "application/json",
                                               "Accept": "audio/pcm"})

    def stream(self, text: str, cancelled: Callable[[], bool] = lambda: False) -> Iterator[np.ndarray]:
        """Blocos int16 de 100 ms. Interrompe quando `cancelled()` for verdadeiro (barge-in/operador)."""
        text = " ".join((text or "").split())[:600]
        if not text:
            return
        try:
            response = self._opener(self._request(text), timeout=self.timeout)
        except urllib.error.HTTPError as error:
            raise ElevenError(self._friendly(error.code)) from None
        except (urllib.error.URLError, OSError, TimeoutError):
            raise ElevenError("Sem conexão com a ElevenLabs. Verifique a internet.") from None
        leftover = b""
        try:
            while not cancelled():
                try:
                    data = response.read(CHUNK)
                except (OSError, TimeoutError):
                    raise ElevenError("A conexão com a ElevenLabs caiu no meio da frase.") from None
                if not data:
                    break
                data = leftover + data
                usable = len(data) - (len(data) % 2)
                leftover = data[usable:]
                if usable:
                    yield np.frombuffer(data[:usable], dtype="<i2").astype(np.int16)
        finally:
            try:
                response.close()
            except Exception:
                pass

    @staticmethod
    def _friendly(code: int) -> str:
        if code in (401, 403):
            return "Chave da ElevenLabs inválida ou sem permissão."
        if code in (402, 429):
            return "Sem créditos ou limite da ElevenLabs atingido."
        if code == 404:
            return "Voz da ElevenLabs não encontrada (confira NURIA_ELEVEN_VOZ)."
        if code in (400, 422):
            return "A ElevenLabs recusou o pedido (voz ou modelo inválido)."
        return f"A ElevenLabs respondeu com erro {code}."

    def check(self) -> tuple:
        """Teste rápido: gera meia sílaba e confirma que chegou áudio. (ok, mensagem curta)."""
        try:
            for _ in self.stream("Oi."):
                return True, "ElevenLabs respondeu"
            return False, "sem áudio"
        except ElevenError as error:
            return False, str(error)
