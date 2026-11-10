"""Voz única ElevenLabs: módulo de streaming, chave no painel e fala do operador (tudo simulado, sem rede).

Uso: python test_eleven.py
"""
from __future__ import annotations

import io
import json
import os
import sys
import urllib.error
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np

import credenciais
import eleven
import test_realtime as T
from test_realtime import Harness, cfg, check

KEY = "sk_" + "a1b2c3d4" * 4


class Resp:
    def __init__(self, data: bytes, fail_after=None):
        self.buf, self.closed, self.fail_after, self.reads = io.BytesIO(data), False, fail_after, 0

    def read(self, n):
        self.reads += 1
        if self.fail_after is not None and self.reads > self.fail_after:
            raise OSError("caiu")
        return self.buf.read(n)

    def close(self):
        self.closed = True


def opener_for(data=b"", code=None, resp=None):
    seen = {}

    def opener(req, timeout=0):
        seen["req"], seen["timeout"] = req, timeout
        if code:
            raise urllib.error.HTTPError(req.full_url, code, "x", {}, None)
        return resp or Resp(data)
    opener.seen = seen
    return opener


def test_modulo():
    print("Módulo ElevenLabs")
    pcm = (np.arange(24000) % 2000 - 1000).astype("<i2").tobytes()          # 1 s
    op = opener_for(pcm)
    tts = eleven.ElevenTTS(KEY, "vozX", "eleven_flash_v2_5", opener=op)
    out = list(tts.stream("Olá,   mundo"))
    req = op.seen["req"]
    body = json.loads(req.data)
    check("pede PCM 24 kHz em streaming para a voz escolhida", "/vozX/stream" in req.full_url and "output_format=pcm_24000" in req.full_url)
    check("chave só no cabeçalho xi-api-key", req.get_header("Xi-api-key") == KEY and KEY not in req.full_url and KEY not in req.data.decode())
    check("texto limpo, modelo flash e idioma pt", body["text"] == "Olá, mundo" and body["model_id"] == "eleven_flash_v2_5" and body["language_code"] == "pt")
    check("blocos de 100 ms, áudio íntegro", all(len(b) == 2400 for b in out[:-1]) and np.concatenate(out).tobytes() == pcm)
    check("modelo sem v2_5 não manda language_code", "language_code" not in json.loads(
        eleven.ElevenTTS(KEY, model="eleven_multilingual_v2", opener=opener_for(b""))._request("oi").data))
    odd = opener_for(b"\x01\x00\x02")
    got = list(eleven.ElevenTTS(KEY, opener=odd).stream("oi"))
    check("byte solto no fim não quebra amostra", sum(len(b) for b in got) == 1)
    r = Resp(pcm)
    n = {"i": 0}

    def cancelled():
        n["i"] += 1
        return n["i"] > 2
    list(eleven.ElevenTTS(KEY, opener=opener_for(resp=r)).stream("oi", cancelled))
    check("cancelamento para de ler e fecha a conexão", r.reads <= 3 and r.closed)
    check("texto vazio não chama a rede", list(eleven.ElevenTTS(KEY, opener=op).stream("   ")) == [])
    for code, frag in ((401, "inválida"), (429, "créditos"), (402, "créditos"), (404, "não encontrada"), (422, "recusou"), (500, "500")):
        try:
            list(eleven.ElevenTTS(KEY, opener=opener_for(code=code)).stream("oi"))
            msg = ""
        except eleven.ElevenError as e:
            msg = str(e)
        check(f"HTTP {code}: mensagem clara, sem vazar a chave", frag in msg and KEY not in msg)
    def boom(req, timeout=0):
        raise urllib.error.URLError("dns")
    try:
        list(eleven.ElevenTTS(KEY, opener=boom).stream("oi"))
        msg = ""
    except eleven.ElevenError as e:
        msg = str(e)
    check("sem internet: mensagem clara", "internet" in msg)
    try:
        list(eleven.ElevenTTS(KEY, opener=opener_for(resp=Resp(pcm, fail_after=1))).stream("oi"))
        msg = ""
    except eleven.ElevenError as e:
        msg = str(e)
    check("queda no meio da frase vira erro tratado", "caiu" in msg)
    check("check() ok com áudio e falha com 401", eleven.ElevenTTS(KEY, opener=opener_for(pcm)).check()[0]
          and not eleven.ElevenTTS(KEY, opener=opener_for(code=401)).check()[0])


def test_config():
    print("Chave e config")
    c = credenciais.load(Path("/nao/existe"), environ={"ELEVENLABS_API_KEY": KEY})
    check("lê ELEVENLABS_API_KEY; ligada por padrão; voz e modelo padrão", c.eleven_configured and c.eleven_on
          and c.eleven_voice == eleven.DEFAULT_VOICE and c.eleven_model == "eleven_flash_v2_5")
    check("sem chave: não configurada", not credenciais.load(Path("/nao/existe"), environ={}).eleven_configured)
    check("NURIA_ELEVEN=off desliga", not credenciais.load(Path("/nao/existe"), environ={"ELEVENLABS_API_KEY": KEY, "NURIA_ELEVEN": "off"}).eleven_on)
    check("chave fora do repr", KEY not in repr(c) and KEY not in str(c))
    check("validação: sk_… e 32 hex valem; espaço ou curta não",
          credenciais.looks_valid(KEY, "eleven") and credenciais.looks_valid("a" * 32, "eleven")
          and not credenciais.looks_valid("sk_ abc" + "a" * 20, "eleven") and not credenciais.looks_valid("sk_curta", "eleven"))
    check("origem da chave reconhecida (ambiente)", credenciais.key_source(environ={"ELEVENLABS_API_KEY": KEY}, name="ELEVENLABS_API_KEY") == "windows")
    import tempfile
    env = Path(tempfile.mkdtemp()) / ".env"
    credenciais.set_env_values({"ELEVENLABS_API_KEY": KEY, "OPENAI_API_KEY": "sk-x" * 10}, env)
    check("grava no .env sem apagar as outras", "ELEVENLABS_API_KEY=" + KEY in env.read_text() and "OPENAI_API_KEY" in env.read_text())
    import entrada_chave
    k = entrada_chave.KeyEntry()
    k.open("eleven")
    check("folha de chave da ElevenLabs", k.kind == "eleven" and k.provider_name == "da ElevenLabs")
    k.text = KEY
    check("folha aceita a chave", k._submit() == ("save", KEY))
    k.open("eleven")
    k.text = "sk-errada com espaco"
    check("folha recusa chave inválida", k._submit() is None and "sk_" in k.error)
    import menu
    class H:
        api = dict(eleven_configured=True, emasked="sk_a…c3d4", eleven_on=True)
    m = menu.OptionsMenu()
    rows = m._items("api", H(), None)
    ids = {r[0] for r in rows}
    check("menu API tem chave (mascarada), voz única e teste", {"api:ekey", "api:evoice", "api:etest"} <= ids
          and any("sk_a…c3d4" in r[2] for r in rows if r[0] == "api:ekey"))
    check("ação da chave cai em CONEXÃO", m.section("api:ekey") == "CONEXÃO" if hasattr(m, "section") else True)


class FakeTTS:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def stream(self, text, cancelled=lambda: False):
        self.calls.append(text)
        if self.fail:
            raise eleven.ElevenError(self.fail)
        for _ in range(5):
            if cancelled():
                return
            yield (np.ones(2400) * 3000).astype(np.int16)


def test_fala():
    print("Fala do operador pela voz única")
    h = Harness(config=cfg(eleven_key=KEY))
    tts = FakeTTS()
    h.a.eleven_factory = lambda c: tts
    h.a.operator_say("  bom   dia ")
    h.idle()
    s = h.a.snapshot()
    check("frase vai para a ElevenLabs (limpa) e termina pronta", tts.calls == ["bom dia"] and s.state == "idle")
    check("áudio da ElevenLabs tocado no robô", h.player.played > 0 and h.player.played == h.player.received)
    check("mesma voz no modo assistido: não pede a fala à Realtime", not h.sent("response.create"))
    h.a.set_mode("teleoperado")
    h.a.operator_say("oi")
    h.idle()
    check("teleoperado também usa a voz única", tts.calls == ["bom dia", "oi"])
    h.close()
    h = Harness(config=cfg(eleven_key=KEY, eleven_on=False))
    tts = FakeTTS()
    h.a.eleven_factory = lambda c: tts
    h.a.set_mode("teleoperado")
    h.a.operator_say("oi")
    h.idle()
    check("desligada nas configurações: não chama a ElevenLabs", tts.calls == [])
    h.close()
    h = Harness(config=cfg(eleven_key=KEY))
    bad = FakeTTS(fail="Sem créditos ou limite da ElevenLabs atingido.")
    h.a.eleven_factory = lambda c: bad
    h.a.set_mode("teleoperado")
    h.a.operator_say("oi")
    h.idle()
    s = h.a.snapshot()
    check("falha da ElevenLabs: avisa e cai na voz local, sem travar", bad.calls == ["oi"] and s.state == "idle"
          and "créditos" in s.message)
    h.close()
    h = Harness(config=cfg(eleven_key=KEY))
    slow = FakeTTS()
    h.a.eleven_factory = lambda c: slow
    h.a.set_mode("teleoperado")
    h.a.operator_say("primeira")
    h.a.operator_say("segunda")
    h.idle()
    check("nova frase interrompe a anterior sem erro", "segunda" in slow.calls and h.a.state == "idle")
    h.close()


if __name__ == "__main__":
    for fn in (test_modulo, test_config, test_fala):
        fn()
    bad = [n for n, ok in T.RESULTS if not ok]
    print(f"\n{len(T.RESULTS) - len(bad)}/{len(T.RESULTS)} verificações OK (simuladas; validar com a chave real)")
    sys.exit(1 if bad else 0)
