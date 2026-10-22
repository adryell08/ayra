"""Configuração da IA sem chaves no código (RNF01).

Ordem: variáveis de ambiente do sistema > arquivo `.env` ao lado do app (fora do Git).
A chave nunca é impressa, registrada ou exibida; a interface mostra só se está configurada.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

APP_DIR = Path(__file__).resolve().parent
ENV_FILE = APP_DIR / ".env"

DEFAULTS = {
    "NURIA_MODELO_VISAO": "gpt-4.1-mini",
    "NURIA_MODELO_STT": "gpt-4o-mini-transcribe",
    "NURIA_MODELO_TTS": "gpt-4o-mini-tts",
    "NURIA_VOZ": "coral",
    "NURIA_TIMEOUT": "30",
    # Realtime (caminho principal)
    "NURIA_MODELO_REALTIME": "gpt-realtime-2.1",
    "NURIA_VOZ_REALTIME": "marin",
    "NURIA_TRANSCRICAO": "gpt-4o-mini-transcribe",     # só para mostrar a pergunta; "desligada" desativa
    "NURIA_RUIDO": "far_field",                         # near_field (fone) | far_field (notebook) | desligado
    "NURIA_RACIOCINIO": "low",                          # minimal|low|medium|high; vazio = padrão do modelo
    "NURIA_REALTIME_URL": "wss://api.openai.com/v1/realtime?model={model}",
}


def read_env_file(path: Path = ENV_FILE) -> Dict[str, str]:
    values: Dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return values
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


@dataclass(frozen=True)
class AIConfig:
    api_key: Optional[str]
    vision_model: str
    stt_model: str
    tts_model: str
    voice: str
    timeout: float
    realtime_model: str = DEFAULTS["NURIA_MODELO_REALTIME"]
    realtime_voice: str = DEFAULTS["NURIA_VOZ_REALTIME"]
    transcription: str = DEFAULTS["NURIA_TRANSCRICAO"]
    noise: str = DEFAULTS["NURIA_RUIDO"]
    reasoning: str = DEFAULTS["NURIA_RACIOCINIO"]
    realtime_url: str = DEFAULTS["NURIA_REALTIME_URL"]

    @property
    def configured(self) -> bool:
        return bool(self.api_key) and not self.api_key.startswith("sk-COLOQUE")

    def __repr__(self) -> str:                      # nunca vaza a chave em logs
        return (f"AIConfig(chave={'definida' if self.configured else 'ausente'}, realtime={self.realtime_model}, "
                f"voz_realtime={self.realtime_voice}, visao={self.vision_model}, stt={self.stt_model}, "
                f"tts={self.tts_model}, voz={self.voice})")


def load(path: Path = ENV_FILE, environ=None) -> AIConfig:
    env = dict(read_env_file(path))
    env.update({k: v for k, v in (os.environ if environ is None else environ).items() if v})
    get = lambda k: env.get(k) or DEFAULTS[k]
    raw = lambda k: env[k].strip() if k in env else DEFAULTS[k]      # permite valor vazio explícito
    try:
        timeout = max(5.0, min(120.0, float(get("NURIA_TIMEOUT"))))
    except ValueError:
        timeout = 30.0
    key = (env.get("OPENAI_API_KEY") or "").strip() or None
    return AIConfig(key, get("NURIA_MODELO_VISAO"), get("NURIA_MODELO_STT"), get("NURIA_MODELO_TTS"),
                    get("NURIA_VOZ"), timeout, get("NURIA_MODELO_REALTIME"), get("NURIA_VOZ_REALTIME"),
                    raw("NURIA_TRANSCRICAO"), raw("NURIA_RUIDO"), raw("NURIA_RACIOCINIO"), get("NURIA_REALTIME_URL"))


# ---------------------------------------------------------------------------
# Edição pela interface (aba Ajustes → API)
# ---------------------------------------------------------------------------
REALTIME_MODELS = ("gpt-realtime-2.1", "gpt-realtime-2.1-mini")
REALTIME_VOICES = ("marin", "cedar", "coral", "sage", "alloy", "ash", "ballad", "echo", "shimmer", "verse")


def mask(key: Optional[str]) -> str:
    """Nunca mostra a chave: só o prefixo e os 4 últimos caracteres."""
    if not key:
        return "não configurada"
    return (key[:3] if key.startswith("sk-") else "") + "…" + key[-4:]


def looks_valid(key: str) -> bool:
    return key.startswith("sk-") and len(key) >= 20 and not any(c.isspace() for c in key)


def set_env_values(updates: Dict[str, Optional[str]], path: Path = ENV_FILE) -> None:
    """Grava/atualiza/remove (valor None) chaves no .env, preservando as demais linhas e comentários."""
    path = Path(path)
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        lines = ["# Configuração local da Nuria. Este arquivo NUNCA vai para o Git."]
    pending = dict(updates)
    out = []
    for line in lines:
        stripped = line.strip()
        key = stripped.split("=", 1)[0].strip().removeprefix("export ").strip() if "=" in stripped else ""
        if key and not stripped.startswith("#") and key in pending:
            value = pending.pop(key)
            if value is not None:
                out.append(f"{key}={value}")
            continue
        out.append(line)
    for key, value in pending.items():
        if value is not None:
            out.append(f"{key}={value}")
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    tmp.replace(path)


def key_source(environ=None) -> str:
    """De onde vem a chave em uso: 'windows' (variável de ambiente, tem prioridade), 'env' ou ''."""
    if (os.environ if environ is None else environ).get("OPENAI_API_KEY"):
        return "windows"
    return "env" if read_env_file().get("OPENAI_API_KEY") else ""
