"""Painel do operador (`operador_web/index.html`): estrutura, acessibilidade, contraste, privacidade, uma tela só (sem rolagem) e
reconexão no navegador de verdade (Chrome headless, câmera/microfone falsos, robô SIMULADO; sem API paga).

Uso: python test_painel.py     (a parte do navegador é pulada, e dito, se Chrome ou Node não existirem)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

RESULTS = []


def check(name, cond):
    RESULTS.append((name, bool(cond)))
    print(f"  [{'OK' if cond else 'FALHOU'}] {name}")


def lum(hexcolor):
    h = hexcolor.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def static_checks():
    html = (HERE / "operador_web" / "index.html").read_text(encoding="utf-8")
    print("Estrutura e requisitos do desenho")
    check("título 'Ayra - Sala do Operador'", "<title>Ayra - Sala do Operador</title>" in html and "<h1>Ayra - Sala do Operador</h1>" in html)
    check("esquerda: card 'Gestos (3D)' com chave; direita: 'Expressões / Olhos (2D)' com chave",
          "Gestos (3D)" in html and 'id="body3d"' in html and "Expressões / Olhos (2D)" in html and 'id="eyes2d"' in html)
    check("centro: vídeo ao vivo, faixa de ícones (modo, visão, microfone) e conversa 'Ayra diz: Digite uma frase...'",
          'id="video"' in html and 'id="modeToggle"' in html and 'id="vision"' in html and 'id="talk"' in html
          and "Ayra diz:" in html and "Digite uma frase..." in html and 'id="say"' in html)
    check("medidores de onda 'Teleoperador' (com ícone de mic on/off) e 'Usuário'",
          'id="micWave"' in html and 'id="robotWave"' in html and "Teleoperador" in html and 'id="micInd"' in html and "Usuário" in html)
    check("barra de voz artificial 0–100 % com ícone de robô (única, sem perfis)",
          re.search(r'<input type="range" id="robotLevel" min="0" max="100"', html) and 'data-p="' not in html and 'id="robotVal"' in html)
    seg = html.split('$("eyes2d").onclick')[1].split("};")[0]
    check("Gestos (3D) e Expressões/Olhos (2D) independentes: a chave dos olhos não envia 'body' nem mexe no 3D, e o 3D não mexe nos olhos",
          'tipo: "body"' not in seg and "setBody" not in seg and '"eyes2d"' not in html.split("function setBody")[1].split(chr(10) + "}")[0])
    check("faixa ESTADO no rodapé", 'id="stateBar"' in html and ">ESTADO<" in html)
    check("alerta de erro/conexão no canto da conversa", 'id="alert"' in html and 'role="status"' in html)
    check("indicador neutro de conexão (Online / Reconectando… / Sem conexão) e 'Tentar agora'",
          'id="conn"' in html and "Reconectando…" in html and "Sem conexão" in html and 'id="retry"' in html)
    check("uma tela só: sem rolagem da página", "overflow: hidden" in html and "100dvh" in html)
    check("nada de rede externa (CDN/fontes): funciona offline na rede local", not re.search(r'(src|href)="https?://', html) and "@import" not in html)

    print("Acessibilidade")
    buttons = re.findall(r"<button\b[^>]*>(.*?)</button>", html, re.S)
    icon_only = [m for m in re.finditer(r"<button\b([^>]*)>(.*?)</button>", html, re.S) if not re.sub(r"<[^>]+>", "", m.group(2)).strip()]
    check("todo botão só de ícone tem nome acessível (aria-label)", all("aria-label" in m.group(1) for m in icon_only))
    check("interruptores usam role=switch + aria-checked; modos/microfone usam aria-pressed",
          html.count('role="switch"') >= 3 and 'aria-pressed="false"' in html)
    check("estado por forma: SVG de visão/microfone e joystick/robô; chaves com posição e nome acessível",
          all(x in html for x in ('eyeOff:', 'micOff:', 'joystick:', 'robot:', 'translateX(22px)', 'function switchLabel', 'function controlIcon'))
          and 'id="visionTxt"' not in html and 'id="modeName"' not in html and 'id="body3dTxt"' not in html)
    check("foco visível por teclado e movimento reduzido respeitado", ":focus-visible" in html and "prefers-reduced-motion" in html)
    check("campo e barra com rótulo associado", 'for="sayText"' in html and 'for="robotLevel"' in html and "aria-valuetext" in html)
    check("alvos de toque ≥ 44 px (campos, botões e barra)", "min-height: 44px" in html and "min-height: 52px" in html)
    check("teclado: Enter envia a frase, Espaço segura o microfone, T trava, Esc fecha painéis",
          'e.key === "Enter"' in html and 'e.code === "Space"' in html and 'e.code === "KeyT"' in html and '"Escape"' in html)
    check("celular: viewport com áreas seguras; gestos/voz em painel que abre por cima",
          "viewport-fit=cover" in html and "safe-area-inset-bottom" in html and 'id="openLeft"' in html)

    print("Contraste (WCAG AA ≥ 4,5:1 no texto)")
    light = dict(re.findall(r"--([a-z]+):\s*(#[0-9a-fA-F]{6})", html.split("@media (prefers-color-scheme: dark)")[0]))
    dark_block = html.split("@media (prefers-color-scheme: dark)")[1].split("}")[0]
    dark = dict(light, **dict(re.findall(r"--([a-z]+):\s*(#[0-9a-fA-F]{6})", dark_block)))
    for nome, p in (("claro", light), ("escuro", dark)):
        pares = [("texto/cartão", p["text"], p["card"]), ("texto/fundo", p["text"], p["bg"]), ("secundário/cartão", p["dim"], p["card"]),
                 ("secundário/chip", p["dim"], p["chip"]), ("verde/cartão", p["green"], p["card"]), ("vermelho/cartão", p["red"], p["card"])]
        ruins = [(n, round(contrast(a, b), 2)) for n, a, b in pares if contrast(a, b) < 4.5]
        check(f"tema {nome}: contraste do texto ok" + (f" (falhas: {ruins})" if ruins else ""), not ruins)
    branco = "#ffffff"
    check("texto branco sobre azul/vermelho/verde (claro) ≥ 4,5:1",
          all(contrast(branco, light[c]) >= 4.5 for c in ("blue", "red", "green")))

    print("Privacidade")
    check("nenhum texto de conversa fixo na página: pergunta/resposta só aparecem de forma dinâmica e na faixa ESTADO, truncadas",
          "texto do usuário" not in html and 'id="q">—<' in html and 'id="a">—<' in html and "text-overflow: ellipsis" in html)
    check("a página não grava nada no navegador (sem localStorage/cookies; o PIN fica só na memória da aba)",
          "localStorage" not in html and "sessionStorage" not in html and "document.cookie" not in html)
    check("página de memória/RAG continuam separadas (nada de memória embutida no painel)", "/memoria" in html and "api/memoria" not in html)


def browser_checks():
    print("Navegador de verdade (Chrome headless + robô simulado): login, uma tela só, queda e volta")
    chrome = os.environ.get("CHROME") or next((p for p in ("C:/Program Files/Google/Chrome/Application/chrome.exe",
                                                           "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
                                                           "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe") if Path(p).exists()), None)
    node = shutil.which("node")
    if not chrome or not node:
        print("  [PULADO] Chrome/Edge ou Node não encontrados: parte do navegador não executada")
        return
    import numpy as np
    import operador
    from test_operador import FakeMic

    def scene():
        return np.full((480, 640, 3), (60, 110, 160), np.uint8)
    state = dict(estado="idle", modo="autonomo", visao=True, pergunta="", resposta="", expressao="Neutro", rostos=0, fps=10,
                 corpo3d=False, voz_robotica_nivel=0, voz_perfil="original")
    srv = operador.OperatorServer(scene, lambda: dict(state), lambda pcm: None, port=18796, pin="654321", use_tls=False, mic_factory=FakeMic)
    srv.drop_grace = 2.0
    srv.start()
    out = Path(tempfile.mkdtemp(prefix="painel_"))
    try:
        env = dict(os.environ, CHROME=chrome)
        p = subprocess.run([node, str(HERE / "captura_painel.js"), str(out), "http://127.0.0.1:18796/", "9344", "654321"],
                           capture_output=True, text=True, timeout=180, env=env, encoding="utf-8", errors="replace")
        res = json.loads((out / "resultado.json").read_text(encoding="utf-8")) if (out / "resultado.json").exists() else {}
        check("entra com o PIN e fica Online", res.get("estado") == "Online")
        sizes = [k for k in res if k.startswith("scroll_")]
        check("sem rolagem em 1280×720, 1024×768, 390×844, 844×390 e 360×640",
              len(sizes) == 5 and all(res[k] == "sem rolagem" for k in sizes))
        check("queda simulada: indicador vira 'Reconectando…' e depois volta a 'Online' sozinho",
              str(res.get("apos_queda", "")).startswith("Reconectando") and res.get("apos_volta") == "Online")
        check("prints gerados (login, desktop, tablet, celular, reconectando)", len(list(out.glob("*.png"))) >= 9)
        if p.returncode != 0:
            print(p.stderr[-400:])
    finally:
        srv.stop()
        shutil.rmtree(out, ignore_errors=True)


if __name__ == "__main__":
    static_checks()
    browser_checks()
    ok = sum(1 for _, c in RESULTS if c)
    print(f"\n{ok}/{len(RESULTS)} verificações OK")
    sys.exit(0 if ok == len(RESULTS) else 1)
