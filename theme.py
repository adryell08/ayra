"""Tema visual do Robô-Olhos.

Derivado das Apple Human Interface Guidelines (atualizadas em jun/2026):
  * Color  -> cores de sistema (claro, escuro e contraste aumentado);
  * Typography -> tamanhos de texto do macOS, sem pesos leves;
  * Layout -> alvos mínimos de 44 pt e espaçamento de 16 pt;
  * Accessibility -> contraste >= 4,5:1 para texto, >= 3:1 para gráficos,
    respeito às preferências do sistema (aparência e "reduzir movimento").
"""
from __future__ import annotations

import platform
import subprocess
from dataclasses import dataclass
from typing import Dict, Tuple

RGB = Tuple[int, int, int]

# --------------------------------------------------------------------------
# Cores de sistema (HIG > Color > System colors)
# --------------------------------------------------------------------------
LIGHT: Dict[str, RGB] = dict(
    red=(255, 56, 60), orange=(255, 141, 40), yellow=(255, 204, 0), green=(52, 199, 89),
    mint=(0, 200, 179), teal=(0, 195, 208), cyan=(0, 192, 232), blue=(0, 136, 255),
    indigo=(97, 85, 245), purple=(203, 48, 224), pink=(255, 45, 85))
DARK: Dict[str, RGB] = dict(
    red=(255, 66, 69), orange=(255, 146, 48), yellow=(255, 214, 0), green=(48, 209, 88),
    mint=(0, 218, 195), teal=(0, 210, 224), cyan=(60, 211, 254), blue=(0, 145, 255),
    indigo=(109, 124, 255), purple=(219, 52, 242), pink=(255, 55, 95))
LIGHT_HC: Dict[str, RGB] = dict(
    red=(233, 21, 45), orange=(197, 83, 0), yellow=(161, 106, 0), green=(0, 137, 50),
    mint=(0, 133, 117), teal=(0, 129, 152), cyan=(0, 126, 174), blue=(30, 110, 244),
    indigo=(86, 74, 222), purple=(176, 47, 194), pink=(231, 18, 77))
DARK_HC: Dict[str, RGB] = dict(
    red=(255, 97, 101), orange=(255, 160, 86), yellow=(254, 223, 67), green=(74, 217, 104),
    mint=(84, 223, 203), teal=(59, 221, 236), cyan=(109, 217, 255), blue=(92, 184, 255),
    indigo=(167, 170, 255), purple=(234, 141, 255), pink=(255, 138, 196))


def _lin(c: float) -> float:
    c /= 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(c: RGB) -> float:
    return 0.2126 * _lin(c[0]) + 0.7152 * _lin(c[1]) + 0.0722 * _lin(c[2])


def contrast(a: RGB, b: RGB) -> float:
    """Razão de contraste WCAG entre duas cores (1..21)."""
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def mix(a: RGB, b: RGB, t: float) -> RGB:
    t = max(0.0, min(1.0, t))
    return (int(a[0] + (b[0] - a[0]) * t), int(a[1] + (b[1] - a[1]) * t), int(a[2] + (b[2] - a[2]) * t))


# --------------------------------------------------------------------------
# Esquemas (aparências)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Scheme:
    key: str
    label: str
    bg: RGB            # fundo da janela (fora do painel)
    panel: RGB         # fundo do painel de LEDs
    led_off: RGB       # LED apagado
    led_on: RGB        # LED aceso (cor-base dos olhos)
    highlight: RGB     # brilho do olhar (catchlight)
    chip: RGB          # fundo dos elementos do HUD
    chip_alpha: int
    text: RGB          # texto primário
    text_dim: RGB      # texto secundário (>= 4,5:1 sobre o chip)
    separator: RGB
    palette: Dict[str, RGB]
    glow: float        # intensidade do brilho dos LEDs (0 = desligado)


DARK_SCHEME = Scheme(
    key="dark", label="Escuro", bg=(3, 4, 5), panel=(3, 4, 5), led_off=(8, 9, 10),
    led_on=DARK["cyan"], highlight=(255, 255, 255), chip=(28, 28, 30), chip_alpha=215,
    text=(245, 245, 247), text_dim=(184, 184, 192), separator=(60, 60, 67),
    palette=DARK, glow=0.55)

LIGHT_SCHEME = Scheme(
    key="light", label="Claro", bg=(242, 242, 247), panel=(242, 242, 247), led_off=(233, 233, 238),
    led_on=LIGHT_HC["blue"], highlight=(255, 255, 255), chip=(255, 255, 255), chip_alpha=228,
    text=(17, 18, 22), text_dim=(75, 78, 87), separator=(210, 211, 216),
    palette=LIGHT_HC, glow=0.18)

CONTRAST_SCHEME = Scheme(
    key="contrast", label="Alto contraste", bg=(0, 0, 0), panel=(0, 0, 0), led_off=(5, 5, 5),
    led_on=DARK_HC["cyan"], highlight=(255, 255, 255), chip=(0, 0, 0), chip_alpha=240,
    text=(255, 255, 255), text_dim=(235, 235, 240), separator=(255, 255, 255),
    palette=DARK_HC, glow=0.35)

SCHEMES = {s.key: s for s in (DARK_SCHEME, LIGHT_SCHEME, CONTRAST_SCHEME)}
SCHEME_ORDER = ["dark", "light", "contrast"]


def emotion_tint(scheme: Scheme, shown: str) -> RGB:
    """Cor-alvo do olho para cada expressão. A cor é apenas um reforço: a forma
    do olho já carrega a emoção (HIG: não usar só cor para transmitir informação)."""
    p = scheme.palette
    table = {
        "neutral": scheme.led_on, "wide": scheme.led_on,
        "happy": p["mint"], "surprised": p["yellow"],
        "sad": p["indigo"], "angry": p["red"],
        "sleepy": p["indigo"], "sleeping": p["indigo"],
    }
    return table.get(shown, scheme.led_on)


# --------------------------------------------------------------------------
# Tipografia (HIG > Typography, tamanhos do macOS) e medidas (HIG > Layout)
# --------------------------------------------------------------------------
FONT_STACK = ("sf pro text,sf pro display,segoe ui variable text,segoe ui,"
              "inter,roboto,arial,dejavu sans")

# papel -> (pontos, negrito). Nenhum texto abaixo de 13 pt.
TEXT_ROLES: Dict[str, Tuple[int, bool]] = {
    "title": (28, True), "headline": (16, True), "body": (16, False),
    "subheadline": (13, False), "footnote": (13, False), "caption": (13, False), "section": (13, True),
}

CONTROL_MIN = 44    # pt: alvos confortáveis também para a futura versão touch
PADDING = 16        # pt: respiro ao redor de elementos
HINT_SECONDS = 5.0  # a dica inicial sai cedo para devolver espaço aos olhos


def ui_scale(height_px: int) -> float:
    """pt -> pixels. 1 pt = 1 px numa janela de 600 px de altura; cresce com a tela."""
    return max(1.0, min(1.5, height_px / 800.0))


# --------------------------------------------------------------------------
# Preferências do sistema (respeitar o que o usuário já configurou)
# --------------------------------------------------------------------------
def _run(cmd) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=2).stdout.strip()
    except Exception:
        return ""


def system_prefers_dark() -> bool:
    try:
        sysname = platform.system()
        if sysname == "Windows":
            import winreg  # type: ignore
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                               r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
        if sysname == "Darwin":
            return _run(["defaults", "read", "-g", "AppleInterfaceStyle"]).lower() == "dark"
    except Exception:
        pass
    return True


def system_prefers_reduced_motion() -> bool:
    try:
        sysname = platform.system()
        if sysname == "Windows":
            import ctypes
            flag = ctypes.c_int(1)
            SPI_GETCLIENTAREAANIMATION = 0x1042
            ctypes.windll.user32.SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(flag), 0)
            return flag.value == 0
        if sysname == "Darwin":
            return _run(["defaults", "read", "com.apple.universalaccess", "reduceMotion"]) == "1"
    except Exception:
        pass
    return False


def resolve_scheme(name: str) -> Scheme:
    if name == "auto":
        return DARK_SCHEME if system_prefers_dark() else LIGHT_SCHEME
    return SCHEMES.get(name, DARK_SCHEME)
