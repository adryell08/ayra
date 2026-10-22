"""Sonda local: índices 0–3, um quadro descartado por backend, sem imagens salvas."""
import json
import os
from pathlib import Path
import subprocess
import sys

os.environ["OPENCV_LOG_LEVEL"] = "SILENT"


def probe(index, backend):
    import cv2
    cap = None
    frame = None
    result = {"index": index, "backend": backend, "opened": False, "read": False}
    try:
        code = {"DSHOW": cv2.CAP_DSHOW, "MSMF": cv2.CAP_MSMF, "PADRAO": cv2.CAP_ANY}[backend]
        cap = cv2.VideoCapture(index, code)
        result["opened"] = cap.isOpened()
        if result["opened"]:
            ok, frame = cap.read()
            result["read"] = bool(ok and frame is not None)
            if result["read"]:
                result["size"] = [int(frame.shape[1]), int(frame.shape[0])]
    except Exception as error:
        result["error"] = str(error)
    finally:
        del frame
        if cap is not None:
            cap.release()
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    if len(sys.argv) == 3:
        probe(int(sys.argv[1]), sys.argv[2])
    else:
        for index in range(4):
            for backend in ("DSHOW", "MSMF", "PADRAO"):
                try:
                    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), str(index), backend],
                                            capture_output=True, text=True, encoding="utf-8", timeout=6)
                    print(result.stdout.strip() or json.dumps({"index": index, "backend": backend,
                          "error": result.stderr.strip(), "exit_code": result.returncode}), flush=True)
                except subprocess.TimeoutExpired:
                    print(json.dumps({"index": index, "backend": backend, "error": "timeout 6s"}), flush=True)
