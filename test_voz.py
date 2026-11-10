"""Perfis de voz (Original / Robótica / Mistura), falha do filtro sem plano B no áudio original, rótulos públicos neutros,
regras de resposta no prompt e relatório de tokens. Tudo simulado: sem voz real, sem rede, sem API paga.
Uso: python test_voz.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import numpy as np

from filtro_voz import RobotVoice, VoiceMask

RESULTS = []


def check(name, cond):
    RESULTS.append((name, bool(cond)))
    print(f"  [{'OK' if cond else 'FALHOU'}] {name}")


def tone(n=960, f=180.0, amp=9000):
    t = np.arange(n) / 24000.0
    return (np.sin(2 * np.pi * f * t) * amp + np.sin(2 * np.pi * 3 * f * t) * amp / 3).astype(np.int16)


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def corr(a, b):
    a, b = a.astype(np.float64), b.astype(np.float64)
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


def test_profiles():
    print("Perfis")
    calls = []

    class Spy(RobotVoice):
        def process(self, pcm, level=1.0, dry=None):
            calls.append((level, dry))
            return super().process(pcm, level=level, dry=dry)

    x = tone(960)
    m = VoiceMask(0.0, factory=Spy)
    y = m.process(x)
    check("Original: devolve o áudio como chega, sem criar nem chamar o filtro (sem carregar o scipy)",
          np.array_equal(y, x) and not calls and m._filter is None and m.profile == "original" and not m.active)
    m.set_level(1.0)
    r1 = m.process(x)
    check("Robótica: 100 % filtro (level 1, parte original 0) e perfil claro", m.profile == "robotica" and calls[-1] == (1.0, 0.0)
          and not m.mixed and r1 is not None and not np.array_equal(r1, x))
    m.set_level(0.6)
    check("Mistura: parcial é identificada como mistura (aviso no painel)", m.profile == "mistura" and m.mixed)
    m.set_level(0.999)
    check("quase 100 % vira Robótica de verdade (sem sobra de voz original)", m.profile == "robotica" and m.level == 1.0)
    m.set_level(-3)
    check("valores absurdos são limitados", m.level == 0.0 and (m.set_level(9) or m.level) == 1.0)
    # mesma identidade para as duas fontes (instâncias novas: o RobotVoice mantém fase/ruído entre blocos)
    ma, mb = VoiceMask(1.0), VoiceMask(1.0)
    a = ma.process(x, "ai")
    b = mb.process(x, "operator")
    check("IA e operador passam pelo mesmo filtro e dão exatamente a mesma voz", np.array_equal(a, b)
          and ma.processed["ai"] == 1 and mb.processed["operator"] == 1)
    ref = RobotVoice().process(x, level=1.0)
    check("Robótica = RobotVoice puro em 100 % (nenhuma voz original misturada)", np.array_equal(a, ref))
    long = np.concatenate([x] * 4)

    def run(level):
        mk = VoiceMask(level)
        return np.concatenate([mk.process(long[i:i + 960]) for i in range(0, len(long), 960)])

    def aligned(y):                                         # correlação com o original, descontado o atraso do filtro
        best = 0.0
        for lag in range(0, 200):
            best = max(best, abs(corr(y[lag:], long[:len(long) - lag])))
        return best
    r100, r50 = aligned(run(1.0)), aligned(run(0.5))
    check(f"Mistura deixa a voz original audível (correlação {r50:.2f}); a Robótica não (correlação {r100:.2f})", r50 > r100 + 0.4 and r100 < 0.3)


def test_failure():
    print("Falha do filtro: sem plano B no áudio original")
    x = tone(960)
    clock = Clock()

    class Broken:
        def __init__(self):
            raise ImportError("No module named 'scipy'")

    m = VoiceMask(1.0, factory=Broken, clock=clock)
    y = m.process(x, "ai")
    check("Robótica com filtro quebrado: devolve None (silêncio), NUNCA o áudio original", y is None)
    d = m.diag()
    check("diagnóstico privado guarda o motivo e a contagem", d["falhas"] == 1 and "ImportError" in d["ultimo_erro"]
          and d["ok"] is False and d["descartados"]["ai"] == 1)
    check("durante a pausa não tenta de novo (não trava o áudio) e segue sem original",
          m.process(x, "operator") is None and m.failures == 1 and m.suppressed["operator"] == 1)
    clock.t += VoiceMask.RETRY_S + .1
    check("depois da pausa tenta de novo e conta nova falha", m.process(x) is None and m.failures == 2)

    state = {"fail": True}

    class Flaky(RobotVoice):
        def process(self, pcm, level=1.0, dry=None):
            if state["fail"]:
                raise RuntimeError("falha injetada")
            return super().process(pcm, level=level, dry=dry)
    m = VoiceMask(1.0, factory=Flaky, clock=clock)
    check("erro no meio do processamento: None", m.process(x) is None)
    state["fail"] = False
    check("ainda na pausa: continua None (sem original)", m.process(x) is None)
    clock.t += VoiceMask.RETRY_S + .1
    out = m.process(x)
    check("recupera sozinho depois da pausa e volta a filtrar", out is not None and not np.array_equal(out, x) and m.diag()["ok"])

    class BadShape(RobotVoice):
        def process(self, pcm, level=1.0, dry=None):
            return np.zeros(len(pcm) // 2, np.int16)
    check("saída com tamanho errado conta como falha, não toca o original",
          VoiceMask(1.0, factory=BadShape, clock=clock).process(x) is None)

    class BadType(RobotVoice):
        def process(self, pcm, level=1.0, dry=None):
            return np.asarray(pcm, np.float32)
    check("saída com tipo errado também", VoiceMask(1.0, factory=BadType, clock=clock).process(x) is None)
    mx = VoiceMask(0.5, factory=Broken, clock=clock)
    check("Mistura com filtro quebrado: também silencia (máscara ativa nunca cai para o original)", mx.process(x) is None)
    orig = VoiceMask(0.0, factory=Broken, clock=clock)
    check("Original com o filtro quebrado: o robô NÃO fica mudo (o filtro nem é usado)",
          np.array_equal(orig.process(x), x) and orig.failures == 0 and orig.diag()["ok"])
    check("trocar para Original depois da falha libera a voz na hora", (m.set_level(0.0) or True) and np.array_equal(m.process(x), x))


def test_assistant():
    print("Assistente: voz da IA, voz do operador e frase local sob falha")
    import test_realtime as T
    import assistente_realtime as ar
    notice = ar.VOICE_DOWN_NOTICE
    h = T.Harness()
    a = h.a
    fed = []
    real_feed = a.player.feed
    a.player.feed = lambda pcm: (fed.append(np.array(pcm, copy=True)), real_feed(pcm))[1]
    a.ask_text("olá, tudo bem")
    check("Original: IA fala normalmente (áudio chega ao alto-falante)", h.idle() and len(fed) > 0 and a.voice_profile == "original")
    base_frames = len(fed)
    # máscara ativa e quebrada
    class Broken:
        def __init__(self):
            raise ImportError("scipy ausente")
    a.voice_mask._factory = Broken
    a.robot_level = 1.0
    fed.clear()
    states = h.states
    a.ask_text("outra pergunta")
    done = h.idle()
    check("Robótica quebrada: nenhuma amostra (nem original) chega ao alto-falante", done and len(fed) == 0 and a.voice_diag()["falhas"] >= 1)
    snap = a.snapshot()
    check("público: aviso neutro e a resposta em texto continua (robô não trava)", notice in snap.message and snap.state == "idle"
          and snap.answer != "" and snap.message_kind == "warning")
    low = snap.message.lower()
    check("público: o aviso não cita operador, teleoperação, máscara, filtro, scipy nem o erro",
          not any(w in low for w in ("operador", "teleop", "máscara", "mascara", "filtro", "scipy", "import", "erro")))
    check("privado: o motivo está no diagnóstico", "ImportError" in a.voice_diag()["ultimo_erro"] and a.voice_diag()["ok"] is False)
    # voz ao vivo do operador
    a.voice_mask._retry_at = 0.0
    a._snap.mode = "teleoperado"
    fed.clear()
    a._op_voice = 0.0
    a._op_floor = 0.0001
    t = np.arange(960) / 24000.0
    loud = (np.sin(2 * np.pi * 200 * t) * 9000).astype(np.int16)
    for _ in range(12):
        a.operator_audio(loud)
        a.voice_mask._retry_at = 0.0
    check("operador ao vivo com a máscara quebrada: silêncio, nunca a voz original", len(fed) == 0 and a.voice_diag()["descartados"]["operator"] > 0)
    check("aviso público do operador é o mesmo texto neutro", a.snapshot().message in ("", notice))
    # recuperação: volta ao original e o robô fala
    a._snap.mode = "autonomo"
    a._op_live = False
    a.robot_level = 0.0
    fed.clear()
    a.ask_text("de novo")
    check("voltar a Original devolve a voz na hora", h.idle() and len(fed) > 0 and a.voice_profile == "original")
    # robótica saudável: toca filtrado (≠ original) e sem aviso
    a.voice_mask._factory = RobotVoice
    a.voice_mask._retry_at = 0.0
    a.robot_level = 1.0
    fed.clear()
    a.ask_text("mais uma")
    h.idle()
    check("Robótica saudável: toca com filtro e sem aviso", len(fed) > 0 and a.snapshot().message == "" and a.voice_diag()["ok"]
          and a.voice_diag()["processados"]["ai"] > 0)
    check("properties antigas continuam: robot_voice / robot_level", a.robot_voice is True and a.robot_level == 1.0
          and (setattr(a, "robot_voice", False) or a.robot_level == 0.0))
    h.close()


def test_labels_and_panel():
    print("Rótulos públicos e painel privado")
    psrc = (HERE / "painel_assistente.py").read_text(encoding="utf-8")
    check("o botão do microfone não diz 'Em ligação' (revelaria o teleoperador)", "Em ligação" not in psrc)
    import re
    block = psrc.split("def _draw_mic")[1].split("def ")[0]
    check("rótulo da conversa contínua não depende do modo (mesmo texto com IA ou operador)",
          "teleoperado" not in block.split("locked = ")[1].split("self._paint_mic")[0])
    msrc = (HERE / "main.py").read_text(encoding="utf-8")
    check("mudar o perfil pelo painel não mostra aviso na tela do robô (quiet)", "quiet=True" in msrc and "def set_robot_voice(percent, save=True, quiet=False)" in msrc)
    check("telemetria privada leva perfil e diagnóstico", "voz_perfil=" in msrc and "voz_diag=" in msrc)
    html = (HERE / "operador_web" / "index.html").read_text(encoding="utf-8")
    check("painel: barra única de voz artificial 0–100 % (sem perfis) e avisos de mistura/falha",
          'id="robotLevel"' in html and 'min="0" max="100"' in html and 'data-p="' not in html
          and "não padroniza a identidade" in html and "nunca volta ao original" in html)
    check("painel: o motivo da falha é mostrado só ali (diagnóstico)", "voiceDiag" in html and "d.ultimo_erro" in html)
    rsrc = (HERE / "renderer.py").read_text(encoding="utf-8") + psrc
    check("tela pública nunca lê o diagnóstico de voz", "voice_diag" not in rsrc and "voz_diag" not in rsrc and "voice_mask" not in rsrc)
    import operador
    check("comando robot_voice com perfil continua válido no servidor", "robot_voice" in operador.COMMANDS)


def test_prompt_and_report():
    print("Regras de resposta e relatório de tokens")
    import ia
    p = ia.PERSONA
    rules = {"fato simples": "1 frase de até 15 palavras para fato simples", "explicação": "2 a 3 frases só quando a explicação exigir",
             "uma pergunta": "UMA pergunta curta", "confirmação": "Só confirme uma ação depois do resultado"}
    check("PERSONA tem as quatro regras de resposta, em texto compacto", all(v in p for v in rules.values()))
    import assistente_realtime as ar
    check("a mesma persona vale na Realtime e no modo legado (JSON)", ia.PERSONA in ar.INSTRUCTIONS and ia.PERSONA in ia.SYSTEM_PROMPT)
    base_chars, base_words = 1655, 267          # PERSONA antes desta mudança (git HEAD)
    check(f"prompt cresce pouco: +{len(p) - base_chars} caracteres, +{len(p.split()) - base_words} palavras (limite 120 / 40)",
          len(p) - base_chars <= 120 and len(p.split()) - base_words <= 40)
    check("sem alegar economia no próprio prompt", "economizam" not in p)
    import relatorio_uso as ru
    rows = [dict(status="completed", input_tokens=1000 + i, output_tokens=60 + i, total_tokens=1060 + 2 * i,
                 input_token_details=dict(cached_tokens=800, image_tokens=250, text_tokens=700, audio_tokens=50),
                 output_token_details=dict(audio_tokens=50 + i, text_tokens=10)) for i in range(12)]
    rows.append(dict(status="cancelled", input_tokens=5, output_tokens=5))
    s = ru.summarize(ru.load(Path("inexistente.jsonl")))
    check("relatório sem arquivo: vazio, sem exceção", s["respostas"] == 0)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        f1, f2 = Path(d) / "a.jsonl", Path(d) / "b.jsonl"
        f1.write_text("\n".join(json.dumps(r) for r in rows) + "\nlixo{\n", encoding="utf-8")
        f2.write_text("\n".join(json.dumps(dict(r, output_tokens=r["output_tokens"] - 10)) for r in rows[:5]), encoding="utf-8")
        a = ru.load(f1)
        sm = ru.summarize(a)
        check("resumo: só respostas completas, ignora linha inválida, calcula médias e cache",
              sm["respostas"] == 12 and 1000 < sm["entrada_media"] < 1012 and abs(sm["cache_fracao"] - 800 / 1005.5) < .01 and sm["imagem_media"] == 250)
        cmp = ru.compare(a, ru.load(f2))
        check("comparação com amostra pequena avisa que não prova economia", cmp["amostra_suficiente"] is False
              and cmp["delta"]["saida_media"] < 0)
    src = (HERE / "relatorio_uso.py").read_text(encoding="utf-8")
    check("relatório não faz rede nem lê chaves", "http" not in src.replace("https://", "") and ".env" not in src and "OPENAI" not in src)


def test_measure():
    print("Medidas de custo (CPU) do áudio")
    x = tone(960)
    n = 600
    out = {}
    for name, lvl in (("original", 0.0), ("robótica", 1.0), ("mistura 60 %", 0.6)):
        m = VoiceMask(lvl)
        m.process(x)
        t0 = time.perf_counter()
        for _ in range(n):
            m.process(x)
        out[name] = (time.perf_counter() - t0) / n * 1000
    raw = RobotVoice()
    raw.process(x, level=1.0, dry=0.0)
    t0 = time.perf_counter()
    for _ in range(n):
        raw.process(x, level=1.0, dry=0.0)
    out["RobotVoice puro"] = (time.perf_counter() - t0) / n * 1000
    print("    bloco de 40 ms: " + " · ".join(f"{k} {v:.3f} ms" for k, v in out.items()))
    check(f"Original custa ~0 e a máscara fica dentro do orçamento (robótica {out['robótica']:.2f} ms ≪ 40 ms por bloco de 40 ms)",
          out["original"] < 0.05 and out["robótica"] < 10.0)
    check(f"a camada de segurança quase não acrescenta custo ao filtro puro ({out['robótica']:.3f} vs {out['RobotVoice puro']:.3f} ms)",
          out["robótica"] < out["RobotVoice puro"] * 1.25 + 0.05)


if __name__ == "__main__":
    test_profiles()
    test_failure()
    test_assistant()
    test_labels_and_panel()
    test_prompt_and_report()
    test_measure()
    ok = sum(1 for _, v in RESULTS if v)
    print(f"\n{ok}/{len(RESULTS)} verificações OK (simuladas; sem voz real, rede nem API paga)")
    sys.exit(0 if ok == len(RESULTS) else 1)
