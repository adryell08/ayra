"""Nomes DirectShow e preferência por câmeras físicas, sem abrir dispositivos."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess


@dataclass(frozen=True)
class CameraDevice:
    index: int
    name: str

    @property
    def excluded(self):
        return bool(re.search(r"\bIR\b|infrared|windows\s*hello|virtual|\bOBS\b|Animaze|Iriun",
                              self.name, re.IGNORECASE))

    @property
    def priority(self):
        name = self.name.casefold()
        return (0 if "integrated" in name else 1 if "webcam" in name else 2 if "camera" in name else 3,
                self.index)


def enumerate_cameras():
    if os.name == "nt":
        try:
            command = Path(__file__).with_name("enumerar_cameras.ps1")
            result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-File", str(command)],
                                    capture_output=True, text=True, encoding="utf-8", timeout=8,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode == 0 and result.stdout.strip():
                names = json.loads(result.stdout)
                if isinstance(names, str):
                    names = [names]
                return [CameraDevice(i, name) for i, name in enumerate(names)]
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    return [CameraDevice(i, f"Câmera {i} (nome indisponível)") for i in range(8)]


def preferred_cameras(devices):
    return sorted((device for device in devices if not device.excluded), key=lambda device: device.priority)
