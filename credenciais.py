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
    "NURIA_VOZ_ROBOTICA": "0",                          # grau do filtro robótico, 0–100 (%), na voz da IA e do operador
    # Provedor da conversa em tempo real: openai (Realtime) ou gemini (Gemini Live)
    "NURIA_PROVEDOR": "openai",
    "NURIA_MODELO_GEMINI": "gemini-3.8-live",
    "NURIA_VOZ_GEMINI": "Kore",
    "NURIA_GEMINI_URL": "wss://generativelanguage.googleapis.com/ws/"
                        "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent",
    # Economia: teto de tokens por resposta (áudio incluso; ~20–25 tokens por segundo de fala) e lado da foto
    "NURIA_MAX_TOKENS": "350",                          # 0 = sem limite
    "NURIA_IMAGEM_LADO": "512",                         # px do maior lado da foto enviada (768 = mais detalhe)
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
        if " #" in value or "\t#" in value:                      # comentário no fim da linha (como no .env.example)
            value = value.split("#", 1)[0].strip() if not value.startswith(("'", '"')) else value
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
    robot_voice: bool = False
    provider: str = "openai"                             # openai | gemini
    gemini_key: Optional[str] = None
    gemini_model: str = DEFAULTS["NURIA_MODELO_GEMINI"]
    gemini_voice: str = DEFAULTS["NURIA_VOZ_GEMINI"]
    gemini_url: str = DEFAULTS["NURIA_GEMINI_URL"]
    max_tokens: int = 350                                # 0 = sem limite
    image_side: int = 512
    robot_level: int = 0                                 # grau da voz robótica, 0–100 (%)

    @property
    def configured(self) -> bool:
        """Chave da OpenAI (Realtime e modo legado)."""
        return bool(self.api_key) and not self.api_key.startswith("sk-COLOQUE")

    @property
    def gemini_configured(self) -> bool:
        return bool(self.gemini_key) and not self.gemini_key.startswith("COLOQUE")

    @property
    def live_configured(self) -> bool:
        """Chave do provedor escolhido para a conversa em tempo real."""
        return self.gemini_configured if self.provider == "gemini" else self.configured

    @property
    def live_model(self) -> str:
        return self.gemini_model if self.provider == "gemini" else self.realtime_model

    @property
    def live_label(self) -> str:
        return "Gemini Live" if self.provider == "gemini" else "OpenAI Realtime"

    def __repr__(self) -> str:                      # nunca vaza a chave em logs
        return (f"AIConfig(provedor={self.provider}, chave={'definida' if self.configured else 'ausente'}, "
                f"gemini={'definida' if self.gemini_configured else 'ausente'}, realtime={self.realtime_model}, "
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
    gkey = (env.get("GEMINI_API_KEY") or env.get("GOOGLE_API_KEY") or "").strip() or None

    def integer(name, low, high):
        try:
            return max(low, min(high, int(float(get(name)))))
        except ValueError:
            return int(DEFAULTS[name])
    provider = get("NURIA_PROVEDOR").strip().lower()
    return AIConfig(key, get("NURIA_MODELO_VISAO"), get("NURIA_MODELO_STT"), get("NURIA_MODELO_TTS"),
                    get("NURIA_VOZ"), timeout, get("NURIA_MODELO_REALTIME"), get("NURIA_VOZ_REALTIME"),
                    raw("NURIA_TRANSCRICAO"), raw("NURIA_RUIDO"), raw("NURIA_RACIOCINIO"), get("NURIA_REALTIME_URL"),
                    robot_percent(get("NURIA_VOZ_ROBOTICA")) > 0,
                    provider if provider in ("openai", "gemini") else "openai", gkey, get("NURIA_MODELO_GEMINI"),
                    get("NURIA_VOZ_GEMINI"), get("NURIA_GEMINI_URL"), integer("NURIA_MAX_TOKENS", 0, 4096),
                    integer("NURIA_IMAGEM_LADO", 256, 1536), robot_level=robot_percent(get("NURIA_VOZ_ROBOTICA")))


# ---------------------------------------------------------------------------
# Edição pela interface (aba Ajustes → API)
# ---------------------------------------------------------------------------
REALTIME_MODELS = ("gpt-realtime-2.1", "gpt-realtime-2.1-mini")
REALTIME_VOICES = ("marin", "cedar", "coral", "sage", "alloy", "ash", "ballad", "echo", "shimmer", "verse")
GEMINI_MODELS = ("gemini-3.8-live", "gemini-3.1-flash-live-preview")
GEMINI_VOICES = ("Kore", "Aoede", "Leda", "Zephyr", "Puck", "Charon", "Fenrir", "Orus")
TOKEN_LIMITS = (("Curtíssimas", 220), ("Curtas", 350), ("Normais", 700), ("Sem limite", 0))


def robot_percent(raw) -> int:
    """NURIA_VOZ_ROBOTICA: 0–100 (%). Valores antigos: sim/true/on/1 = 60 %; não/off/0 = 0 %."""
    text = str(raw or "").strip().lower().rstrip("%")
    if text in ("", "0", "nao", "não", "false", "off"):
        return 0
    if text in ("1", "sim", "true", "on"):
        return 60
    try:
        return max(0, min(100, int(float(text))))
    except ValueError:
        return 0


def mask(key: Optional[str]) -> str:
    """Nunca mostra a chave: só o prefixo e os 4 últimos caracteres."""
    if not key:
        return "não configurada"
    prefix = key[:3] if key.startswith("sk-") else (key[:4] if key.startswith("AIza") else "")
    return prefix + "…" + key[-4:]


def looks_valid(key: str, kind: str = "openai") -> bool:
    if any(c.isspace() for c in key):
        return False
    if kind == "gemini":
        return key.startswith("AIza") and len(key) >= 30
    return key.startswith("sk-") and len(key) >= 20


def set_env_values(updates: Dict[str, Optional[str]], path: Path = ENV_FILE) -> None:
    """Grava/atualiza/remove (valor None) chaves no .env, preservando as demais linhas e comentários."""
    path = Path(path)
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        lines = ["# Configuração local da Ayra. Este arquivo NUNCA vai para o Git."]
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


def key_source(environ=None, name: str = "OPENAI_API_KEY") -> str:
    """De onde vem a chave em uso: 'windows' (variável de ambiente, tem prioridade), 'env' ou ''."""
    names = (name, "GOOGLE_API_KEY") if name == "GEMINI_API_KEY" else (name,)
    if any((os.environ if environ is None else environ).get(n) for n in names):
        return "windows"
    return "env" if any(read_env_file().get(n) for n in names) else ""
