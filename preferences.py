"""Preferências locais de reações; nenhum dado da câmera é persistido."""
import json
from pathlib import Path

DEFAULT_FEATURES = dict(hands=True, tongue=False, smile=True, objects=True, reflex=True, auto_blink=True,
                    round_eyes=False, outline=False, mouth=True)
LABELS = dict(hands="Mãos", tongue="Dar língua", smile="Sorriso", objects="Objetos/animais",
              reflex="Reflexo", auto_blink="Piscadas automáticas",
              round_eyes="Olhos redondos", outline="Contorno dos olhos", mouth="Boca")
FACE_KEYS = ("round_eyes", "outline", "mouth")
REMOVED_FEATURES = ("tongue",)   # "dar língua" removida: heurística instável (confundia fala)
CONFIG_PATH = Path(__file__).resolve().parent / "config.local.json"


def load(path=CONFIG_PATH):
    result = dict(DEFAULT_FEATURES)
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for key in result:
            if key in REMOVED_FEATURES:
                continue
            if isinstance(data.get(key), bool):
                result[key] = data[key]
    except (OSError, ValueError, AttributeError):
        pass
    return result


def save(features, path=CONFIG_PATH):
    path = Path(path)
    pending = path.with_suffix(path.suffix + ".tmp")
    pending.write_text(json.dumps({key: bool(features[key]) for key in DEFAULT_FEATURES},
                                  ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pending.replace(path)
