"""Mede o vídeo da sala do operador (robô → navegador) a 15 e a 30 quadros/s: fps real recebido, idade do quadro, CPU e memória
do servidor. Câmera simulada (640×480, 30 quadros/s, imagem em movimento), cliente WebRTC em outro processo, sem API paga.

Uso:  python medir_video_operador.py            → compara 15, 30 e 30 adaptativo sob carga e grava avaliacao/video_operador.json
      python medir_video_operador.py --cenario 30 sim 12 0   (fps, adaptativo sim/nao, segundos, threads de carga)
O resultado depende do computador: os números do container NÃO valem para o PC do robô (meça lá)."""
import asyncio, json, os, subprocess, sys, threading, time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
PORT, PIN = 18771, "424242"


class CameraFalsa:
    """Imita tracker.CameraSource: thread a 30 quadros/s; guarda o quadro por referência só na taxa que a sala pede."""
    def __init__(self, cam_fps=30):
        self.cam_fps, self.operator_fps = cam_fps, 16.0
        self._full, self._seq, self._last, self._lock, self._wanted = None, 0, 0.0, threading.Lock(), 0.0
        yy, xx = np.mgrid[0:480, 0:640]
        self.base = ((xx * 255 // 640 + yy * 255 // 480) // 2).astype(np.uint8)
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        t, n = time.perf_counter(), 0
        rng = np.random.default_rng(1)
        while True:
            n += 1
            f = np.empty((480, 640, 3), np.uint8)
            f[..., 0] = np.roll(self.base, n * 3, axis=1)
            f[..., 1] = np.roll(self.base, -n * 2, axis=0)
            f[..., 2] = 255 - f[..., 0]
            f[(n * 4) % 400:(n * 4) % 400 + 60, (n * 7) % 560:(n * 7) % 560 + 60] = 255       # objeto em movimento
            f += rng.integers(0, 6, f.shape, dtype=np.uint8)                                   # ruído de sensor
            now = time.perf_counter()
            if now - self._wanted < 1.0 and now - self._last >= 0.9 / self.operator_fps:
                with self._lock:
                    self._full, self._last, self._seq = f, now, self._seq + 1
            t += 1.0 / self.cam_fps
            time.sleep(max(0.0, t - time.perf_counter()))

    def info(self):
        with self._lock:
            self._wanted = time.perf_counter()
            return self._full, self._seq, self._last


CLIENTE = r'''
import asyncio, json, sys, time, aiohttp
from aiortc import RTCPeerConnection, RTCSessionDescription
async def main(dur):
    got = []
    async with aiohttp.ClientSession() as http:
        pc = RTCPeerConnection(); pc.createDataChannel("cmd"); pc.addTransceiver("video", direction="recvonly")
        @pc.on("track")
        def on_track(track):
            async def pull():
                while True:
                    try: f = await track.recv()
                    except Exception: return
                    got.append(time.perf_counter())
            asyncio.ensure_future(pull())
        await pc.setLocalDescription(await pc.createOffer())
        async with http.post("http://127.0.0.1:%d/offer", json=dict(sdp=pc.localDescription.sdp, type=pc.localDescription.type, nome="Medida", pin="%s")) as r:
            ans = await r.json()
        await pc.setRemoteDescription(RTCSessionDescription(sdp=ans["sdp"], type=ans["type"]))
        await asyncio.sleep(2.0); got.clear(); t0 = time.perf_counter()
        await asyncio.sleep(dur)
        n = len(got); span = (got[-1] - got[0]) if n > 1 else 0
        print(json.dumps(dict(fps_cliente=round((n - 1) / span, 1) if span else 0)))
        await pc.close()
asyncio.run(main(float(sys.argv[1])))
''' % (PORT, PIN)


def cenario(fps, adaptativo, dur, carga):
    from operador import OperatorServer
    cam = CameraFalsa()
    class Mic:
        def start(self): return False
        def stop(self): pass
    server = OperatorServer(lambda: None, lambda: dict(estado="ok"), lambda pcm: None, port=PORT, pin=PIN, use_tls=False,
                            mic_factory=Mic, fps=fps, frame_info_provider=cam.info,
                            on_fps=lambda f: setattr(cam, "operator_fps", float(f)), adaptive_fps=adaptativo)
    assert server.start()
    stop = threading.Event()
    # carga = processos que ocupam um núcleo cada (o PC com outros programas, o desenho 3D, o navegador): disputa de CPU, não de GIL
    queimadores = [subprocess.Popen([sys.executable, "-c", "while True: sum(i*i for i in range(20000))"]) for _ in range(carga)]
    cli = subprocess.Popen([sys.executable, "-c", CLIENTE, str(dur)], stdout=subprocess.PIPE, text=True)
    snaps, t_end = [], time.time() + 2.0 + dur + 3
    while cli.poll() is None and time.time() < t_end:
        time.sleep(1.0)
        snaps.append(dict(server._video_snap, efetivo=server.fps))
    out, _ = cli.communicate(timeout=20)
    stop.set()
    for q in queimadores:
        q.kill()
    server.stop()
    cl = json.loads(out.strip().splitlines()[-1]) if out.strip() else {}
    ult = [s for s in snaps if s.get("fps_real")][-max(1, int(dur / 2)):]
    med = lambda k: round(sum(s[k] for s in ult) / len(ult), 1) if ult else 0
    return dict(alvo=fps, adaptativo=adaptativo, carga_processos=carga, fps_cliente=cl.get("fps_cliente", 0), fps_servidor=med("fps_real"),
                idade_ms=med("idade_ms"), idade_p95_ms=med("idade_p95_ms"), cpu_pct=med("cpu_pct"), mem_mb=med("mem_mb"),
                descartados=ult[-1]["descartados"] if ult else 0, taxa_efetiva_final=snaps[-1]["efetivo"] if snaps else fps)


if __name__ == "__main__":
    if "--cenario" in sys.argv:
        i = sys.argv.index("--cenario")
        r = cenario(int(sys.argv[i + 1]), sys.argv[i + 2] == "sim", float(sys.argv[i + 3]), int(sys.argv[i + 4]))
        print("RESULTADO " + json.dumps(r))
        sys.exit(0)
    plano = [(15, False, 0), (30, False, 0), (30, True, 0), (30, False, 2), (30, True, 2)]
    res = []
    for fps, ad, carga in plano:
        p = subprocess.run([sys.executable, __file__, "--cenario", str(fps), "sim" if ad else "nao", "14", str(carga)],
                           capture_output=True, text=True, timeout=120)
        line = [l for l in p.stdout.splitlines() if l.startswith("RESULTADO ")]
        r = json.loads(line[0][10:]) if line else dict(alvo=fps, erro=(p.stderr or p.stdout)[-300:])
        res.append(r)
        print(r, flush=True)
    (HERE / "avaliacao").mkdir(exist_ok=True)
    (HERE / "avaliacao" / "video_operador.json").write_text(json.dumps(dict(cpus=os.cpu_count(), resultados=res), indent=2) + "\n", encoding="utf-8")
