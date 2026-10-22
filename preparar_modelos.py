r"""Baixa somente pesos públicos, verifica SHA-256 e instala localmente.

Execute uma vez em um terminal com rede: .venv\Scripts\python.exe preparar_modelos.py
Nenhuma imagem é lida ou enviada. A execução normal não baixa modelos.
"""
from __future__ import annotations
import argparse
import hashlib
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parent / "models" / "perception"
MODELS = {
    "hands": ("palm_detection_mediapipe/palm_detection_mediapipe_2023feb.onnx",
              "78ff51c38496b7fc8b8ebdb6cc8c1abb02fa6c38427c6848254cdaba57fcce7c"),
    "objects": ("object_detection_nanodet/object_detection_nanodet_2022nov.onnx",
                "4b82da9944b88577175ee23a459dce2e26e6e4be573def65b1055dc2d9720186"),
    "smile": ("facial_expression_recognition/facial_expression_recognition_mobilefacenet_2022july.onnx",
              "4f61307602fc089ce20488a31d4e4614e3c9753a7d6c41578c854858b183e1a9"),
}
BASE = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/"


def model_path(kind):
    return ROOT / Path(MODELS[kind][0]).name


def valid_model(kind):
    path = model_path(kind)
    return path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == MODELS[kind][1]


def install(kind, opener=urllib.request.urlopen):
    if valid_model(kind):
        return
    remote, digest = MODELS[kind]
    with opener(BASE + remote, timeout=30) as response:
        data = response.read(20_000_001)
    if len(data) > 20_000_000 or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError(f"Modelo {kind}: hash inválido; arquivo não instalado.")
    ROOT.mkdir(parents=True, exist_ok=True)
    partial = model_path(kind).with_suffix(".part")
    partial.write_bytes(data)
    partial.replace(model_path(kind))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="somente verificar, sem rede")
    parser.add_argument("--only", choices=list(MODELS))
    parser.add_argument("--startup", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--sim", action="store_true", help=argparse.SUPPRESS)
    args, unknown = parser.parse_known_args(argv)
    if unknown and not args.startup:
        parser.error("Argumentos desconhecidos: " + " ".join(unknown))
    if args.startup and args.sim:
        return 0
    failed = []
    for kind in ([args.only] if args.only else MODELS):
        try:
            if not args.check:
                install(kind)
            ok = valid_model(kind)
            print(f"{kind}: {'OK' if ok else 'AUSENTE/INVÁLIDO'}")
            if not ok:
                failed.append(kind)
        except Exception as error:
            print(f"{kind}: não instalado ({error}). Execute em um terminal com rede.")
            failed.append(kind)
    return int(bool(failed))


if __name__ == "__main__":
    raise SystemExit(main())
