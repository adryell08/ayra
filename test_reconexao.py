"""Queda e volta do operador na rede local (WebRTC real em 127.0.0.1, câmera/microfone/IA simulados).
Garante: a queda NÃO devolve o robô ao autônomo na hora; se o operador volta dentro da tolerância nada muda no robô;
se não volta, o robô volta ao autônomo depois da tolerância; o estado fica "operador reconectando"; PIN errado continua barrado.

Uso: python test_reconexao.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import operador
from test_operador import FakeMic, scene, wait

RESULTS = []
PORT = 18790


def check(name, cond):
    RESULTS.append((name, bool(cond)))
    print(f"  [{'OK' if cond else 'FALHOU'}] {name}")


def drain(srv):
    out = []
    while True:
        try:
            out.append(srv.commands.get_nowait())
        except Exception:
            return out


async def dial(http, pin="123456"):
    from aiortc import RTCPeerConnection, RTCSessionDescription
    pc = RTCPeerConnection()
    dc = pc.createDataChannel("controle")
    msgs = []
    dc.on("message", lambda m: msgs.append(json.loads(m)))
    pc.addTransceiver("video", direction="recvonly")
    pc.addTransceiver("audio", direction="recvonly")
    await pc.setLocalDescription(await pc.createOffer())
    body = dict(sdp=pc.localDescription.sdp, type=pc.localDescription.type, nome="Op", pin=pin)
    async with http.post(f"http://127.0.0.1:{PORT}/offer", json=body) as r:
        if r.status != 200:
            await pc.close()
            return None, None, None, r.status
        ans = await r.json()
    await pc.setRemoteDescription(RTCSessionDescription(sdp=ans["sdp"], type=ans["type"]))
    for _ in range(100):
        if dc.readyState == "open":
            break
        await asyncio.sleep(0.05)
    return pc, dc, msgs, 200


def run():
    import aiohttp
    srv = operador.OperatorServer(scene, lambda: dict(estado="idle", modo="teleoperado", visao=True), lambda pcm: None,
                                  port=PORT, pin="123456", use_tls=False, mic_factory=FakeMic)
    srv.drop_grace = 1.5
    srv.ice_grace = 1.0
    check("servidor sobe", srv.start())
    seen = {}

    async def main():
        async with aiohttp.ClientSession() as http:
            pc, dc, msgs, st = await dial(http)
            seen["ok1"] = st == 200 and dc.readyState == "open"
            await asyncio.sleep(0.8)
            seen["telemetria"] = any(m.get("tipo") == "estado" for m in msgs)
            first = drain(srv)
            seen["conectou"] = ("operator", "Op") in first
            # 1) queda de rede simulada: cliente some (canal fecha); volta ANTES da tolerância
            await pc.close()
            await asyncio.sleep(0.5)
            seen["status_reconectando"] = srv.status.startswith("operador reconectando")
            seen["sem_limpar_logo"] = ("operator", "") not in drain(srv)
            t0 = time.perf_counter()
            pc2, dc2, msgs2, st = await dial(http)
            seen["volta_rapida"] = st == 200 and dc2.readyState == "open"
            await asyncio.sleep(0.6)
            cmds = drain(srv)
            seen["volta_sem_autonomo"] = ("operator", "") not in cmds and ("operator", "Op") in cmds
            await asyncio.sleep(max(0, srv.drop_grace + 0.5 - (time.perf_counter() - t0)))
            seen["segue_depois_da_tolerancia"] = ("operator", "") not in drain(srv) and srv.operator == "Op"
            dc2.send(json.dumps({"tipo": "say", "texto": "voltei"}))
            seen["comando_apos_volta"] = await asyncio.get_running_loop().run_in_executor(
                None, lambda: wait(lambda: any(c[0] == "say" for c in drain_keep(srv)), 3))
            # 2) queda definitiva: não volta -> depois da tolerância o robô volta ao autônomo
            drain(srv)
            await pc2.close()
            await asyncio.sleep(0.6)
            seen["ainda_nao_limpou"] = ("operator", "") not in drain(srv)
            await asyncio.sleep(srv.drop_grace + 0.5)
            seen["limpou_depois"] = ("operator", "") in drain(srv) and srv.status == "aguardando operador"
            # 3) PIN errado continua barrado
            _, _, _, bad = await dial(http, pin="000000")
            seen["pin_errado"] = bad == 403

    kept = []

    def drain_keep(s):
        got = drain(s)
        kept.extend(got)
        return kept
    try:
        asyncio.run(main())
    finally:
        srv.stop()
    check("conecta, recebe telemetria e anuncia o operador", seen.get("ok1") and seen.get("telemetria") and seen.get("conectou"))
    check("queda: status 'operador reconectando' e o robô NÃO volta ao autônomo na hora",
          seen.get("status_reconectando") and seen.get("sem_limpar_logo"))
    check("volta dentro da tolerância: nada muda no robô (sem autônomo)", seen.get("volta_rapida") and seen.get("volta_sem_autonomo"))
    check("passada a tolerância o operador segue conectado", seen.get("segue_depois_da_tolerancia"))
    check("comando depois da volta chega ao robô", seen.get("comando_apos_volta"))
    check("queda definitiva: espera a tolerância e só então volta ao autônomo", seen.get("ainda_nao_limpou") and seen.get("limpou_depois"))
    check("PIN errado continua barrado (403)", seen.get("pin_errado"))
    ok = sum(1 for _, c in RESULTS if c)
    print(f"\n{ok}/{len(RESULTS)} verificações OK")
    return ok == len(RESULTS)


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
