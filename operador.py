"""Sala do operador (etapa 2): controle remoto da Ayra pelo navegador, via WebRTC.

O robô serve uma página HTTPS na rede (local agora; internet depois, ex.: Tailscale). Com o PIN, o operador:
  • vê a câmera (5 quadros/s) e ouve o ambiente do robô;
  • fala pelo robô ("Falar"), ou digita para a Ayra falar;
  • alterna o modo (autônomo, assistido, teleoperado), dispara expressões, direciona o olhar,
    pausa a visão e liga a voz robótica.

Tudo roda numa thread com seu próprio asyncio; o laço da interface só troca mensagens por filas.
Privacidade: câmera e microfone do robô só são transmitidos enquanto há operador conectado.
"""
from __future__ import annotations

import asyncio
import fractions
import json
import queue
import secrets
import socket
import ssl
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np

WEB_DIR = Path(__file__).resolve().parent / "operador_web"
CERT_DIR = Path(__file__).resolve().parent / "certs"
VIDEO_FPS = 10                         # padrão (5, 10 ou 15 em Ajustes → Controle remoto → Vídeo)
FPS_CHOICES = (5, 10, 15)
AUDIO_RATE = 48000                      # WebRTC (Opus) trabalha a 48 kHz
COMMANDS = ("mode", "say", "instruction", "expression", "gesture", "body", "look", "vision", "robot_voice", "interrupt", "ptt", "ping")


def lan_ip() -> str:
    """IP desta máquina na rede local (para montar o endereço da sala do operador)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def new_pin() -> str:
    return f"{secrets.randbelow(10 ** 6):06d}"


def ensure_cert(directory: Path = CERT_DIR):
    """Certificado autoassinado (o navegador só libera microfone em HTTPS). Fica fora do Git."""
    cert, key = directory / "operador.crt", directory / "operador.key"
    if cert.exists() and key.exists():
        return cert, key
    from datetime import datetime, timedelta, timezone
    import ipaddress
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    directory.mkdir(exist_ok=True)
    pk = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Ayra (sala do operador)")])
    alt = [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    try:
        alt.append(x509.IPAddress(ipaddress.ip_address(lan_ip())))
    except ValueError:
        pass
    now = datetime.now(timezone.utc)
    crt = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(pk.public_key())
           .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
           .not_valid_after(now + timedelta(days=825)).add_extension(x509.SubjectAlternativeName(alt), False)
           .sign(pk, hashes.SHA256()))
    key.write_bytes(pk.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                     serialization.NoEncryption()))
    cert.write_bytes(crt.public_bytes(serialization.Encoding.PEM))
    return cert, key


class RobotMic:
    """Microfone do robô para o operador (48 kHz, blocos de 20 ms). Silencia enquanto o robô fala (sem eco)."""

    def __init__(self, muted: Callable[[], bool] = lambda: False):
        self.muted = muted
        self._q: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=8)
        self._stream = None
        self.level = 0.0

    def start(self) -> bool:
        try:
            import sounddevice as sd
            self._stream = sd.InputStream(samplerate=AUDIO_RATE, channels=1, dtype="int16", blocksize=960,
                                          callback=self._cb)
            self._stream.start()
            return True
        except Exception:
            self._stream = None
            return False

    def _cb(self, indata, frames, t, status):
        block = indata[:, 0].copy()
        if self.muted():
            block[:] = 0
        self.level = float(np.sqrt(np.mean((block.astype(np.float32) / 32768) ** 2)))
        try:
            self._q.put_nowait(block)
        except queue.Full:
            pass

    def read(self, timeout: float = 0.1) -> np.ndarray:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return np.zeros(960, np.int16)

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None


class HubMic:
    """Microfone do robô para o operador vindo do AudioHub: o mesmo microfone da IA, já sem o eco do alto-falante.

    Assim as duas pontas falam ao mesmo tempo (full-duplex). Blocos de 20 ms a 48 kHz.
    """

    MAX_BACKLOG = 3                                         # blocos de 20 ms

    def __init__(self, hub):
        self.hub = hub
        self.dropped = 0
        self._q: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=8)
        self._acc = np.zeros(0, np.int16)
        self._sid = None
        self.level = 0.0

    def start(self) -> bool:
        try:
            self._sid = self.hub.subscribe(AUDIO_RATE, self._on)
            return True
        except Exception:
            self._sid = None
            return False

    def _on(self, pcm: np.ndarray) -> None:
        self._acc = np.concatenate([self._acc, pcm])
        while len(self._acc) >= 960:
            block, self._acc = self._acc[:960], self._acc[960:]
            self.level = float(np.sqrt(np.mean((block.astype(np.float32) / 32768) ** 2)))
            if self._q.qsize() >= self.MAX_BACKLOG:         # consumidor atrasou: mantém só o mais recente (≤ 60 ms)
                try:
                    while self._q.qsize() > 1:
                        self._q.get_nowait()
                    self.dropped += 1
                except queue.Empty:
                    pass
            try:
                self._q.put_nowait(block)
            except queue.Full:
                pass

    def read_nowait(self):
        try:
            return self._q.get_nowait()
        except queue.Empty:
            return None

    def read(self, timeout: float = 0.03) -> np.ndarray:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return np.zeros(960, np.int16)

    def stop(self) -> None:
        if self._sid is not None:
            self.hub.unsubscribe(self._sid)
            self._sid = None


class OperatorServer:
    """Servidor da sala do operador. Um operador por vez; PIN obrigatório.

    frame_provider() -> BGR | None (chamado ~5×/s só com operador conectado)
    telemetry() -> dict (estado mostrado ao operador, ~4×/s)
    commands: fila de (nome, dados) consumida pelo laço da interface
    on_operator_audio(pcm 24 kHz): voz do operador enquanto segura "Falar"
    """

    def __init__(self, frame_provider: Callable[[], Optional[np.ndarray]], telemetry: Callable[[], dict],
                 on_operator_audio: Callable[[np.ndarray], None], robot_muted: Callable[[], bool] = lambda: False,
                 port: int = 8765, pin: Optional[str] = None, use_tls: bool = True, mic_factory=None,
                 fps: int = VIDEO_FPS):
        self.frame_provider, self.telemetry, self.on_operator_audio = frame_provider, telemetry, on_operator_audio
        self.robot_muted = robot_muted
        self.fps = int(fps) if int(fps) in FPS_CHOICES else VIDEO_FPS
        self.port, self.pin, self.use_tls = port, pin or new_pin(), use_tls
        self.commands: "queue.Queue[tuple]" = queue.Queue()
        self.mic_factory = mic_factory or (lambda: RobotMic(self.robot_muted))
        self.status = "desligado"
        self.operator = ""                       # nome/endereço do operador conectado
        self.rtt_ms: Optional[float] = None
        self.ptt = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._runner = None
        self._pc = None
        self._channel = None
        self._mic: Optional[RobotMic] = None
        self._ready = threading.Event()
        self._error = ""
        self.failed_pins = 0
        self.memory = None                       # memoria.Memory: painel /memoria (operador ou o próprio usuário)
        self._mem_tokens: dict = {}              # token -> validade (s); entra com o mesmo PIN da sala
        self._mem_hits: list = []
        self._db_pool = None

    # ---- ciclo de vida ----------------------------------------------------------------
    @property
    def url(self) -> str:
        return f"{'https' if self.use_tls else 'http'}://{lan_ip()}:{self.port}"

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return True
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="operador", daemon=True)
        self._thread.start()
        self._ready.wait(10)
        return not self._error

    def stop(self) -> None:
        if self._loop is None:
            return
        fut = asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)
        try:
            fut.result(5)
        except Exception:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(3)
        self._loop = None
        self.status = "desligado"
        self.operator = ""

    def disconnect_operator(self) -> None:
        if self._loop:
            asyncio.run_coroutine_threadsafe(self._close_pc(), self._loop)

    def regenerate_pin(self) -> str:
        self.pin = new_pin()
        self.disconnect_operator()
        return self.pin

    def send(self, payload: dict) -> None:
        """Envia uma mensagem ao operador (thread-safe)."""
        if self._loop and self._channel is not None:
            self._loop.call_soon_threadsafe(self._send_now, payload)

    def _send_now(self, payload):
        try:
            if self._channel is not None and self._channel.readyState == "open":
                self._channel.send(json.dumps(payload))
        except Exception:
            pass

    # ---- servidor -------------------------------------------------------------------------
    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._startup())
        except Exception as error:
            self._error = f"Não foi possível abrir a sala do operador: {error}"
            self.status = self._error
            self._ready.set()
            return
        self._ready.set()
        self._loop.run_forever()

    async def _startup(self) -> None:
        from aiohttp import web
        app = web.Application(client_max_size=4 * 1024 * 1024)
        app.router.add_get("/", self._index)
        app.router.add_static("/static/", WEB_DIR)
        app.router.add_post("/offer", self._offer)
        app.router.add_get("/memoria", self._memory_page)
        app.router.add_post("/api/memoria/entrar", self._memory_login)
        app.router.add_get("/api/memoria", self._memory_get)
        app.router.add_post("/api/memoria/acao", self._memory_action)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        ctx = None
        if self.use_tls:
            cert, key = ensure_cert()
            ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            ctx.load_cert_chain(str(cert), str(key))
        site = web.TCPSite(self._runner, "0.0.0.0", self.port, ssl_context=ctx)
        await site.start()
        self.status = "aguardando operador"
        self._telemetry_task = self._loop.create_task(self._telemetry_loop())

    async def _shutdown(self) -> None:
        task = getattr(self, "_telemetry_task", None)
        if task is not None:
            task.cancel()
        await self._close_pc()
        if self._runner is not None:
            await self._runner.cleanup()

    async def _index(self, request):
        from aiohttp import web
        return web.FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-store"})

    # ---- painel de memória: leitura sob demanda, banco fora do laço de eventos (WebRTC continua fluido) ----
    async def _memory_page(self, request):
        from aiohttp import web
        return web.FileResponse(WEB_DIR / "memoria.html", headers={"Cache-Control": "no-store"})

    def _db(self, fn, *args):
        """Roda a consulta numa thread de prioridade baixa: nunca no laço do WebRTC nem no do desenho."""
        if self._db_pool is None:
            from concurrent.futures import ThreadPoolExecutor

            def low():
                try:
                    from tracker import lower_thread_priority
                    lower_thread_priority()
                except Exception:
                    pass
            self._db_pool = ThreadPoolExecutor(1, thread_name_prefix="memoria-painel", initializer=low)
        return asyncio.get_running_loop().run_in_executor(self._db_pool, fn, *args)

    def _mem_guard(self, request):
        """None se ok; senão a resposta de erro (sem login, memória desligada ou excesso de pedidos)."""
        from aiohttp import web
        now = time.monotonic()
        self._mem_hits = [h for h in self._mem_hits if now - h < 1.0] + [now]
        if len(self._mem_hits) > 20:                            # aba esquecida aberta não martela o robô
            return web.json_response({"erro": "Muitos pedidos; aguarde."}, status=429)
        token = request.headers.get("X-Token", "")
        if not token or self._mem_tokens.get(token, 0) < now:
            return web.json_response({"erro": "Entre com o PIN."}, status=401)
        if self.memory is None or not self.memory.active:
            return web.json_response({"erro": "Memória desligada no robô (Ajustes → Conversa e IA → Memória)."}, status=409)
        return None

    async def _memory_login(self, request):
        from aiohttp import web
        try:
            params = await request.json()
        except Exception:
            return web.json_response({"erro": "Requisição inválida."}, status=400)
        if self.failed_pins >= 10:
            return web.json_response({"erro": "Muitas tentativas. Gere um novo PIN no robô."}, status=429)
        if not secrets.compare_digest(str(params.get("pin", "")), self.pin):
            self.failed_pins += 1
            await asyncio.sleep(1.0)
            return web.json_response({"erro": "PIN incorreto."}, status=403)
        self.failed_pins = 0
        token = secrets.token_urlsafe(24)
        now = time.monotonic()
        self._mem_tokens = {k: v for k, v in self._mem_tokens.items() if v > now}
        self._mem_tokens[token] = now + 8 * 3600
        return web.json_response({"token": token})

    async def _memory_get(self, request):
        from aiohttp import web
        err = self._mem_guard(request)
        if err is not None:
            return err
        q = request.query.get("q", "")[:80]
        if not q and request.query.get("v") == str(self.memory.version):
            return web.json_response({"version": self.memory.version, "same": True})   # nada mudou: resposta mínima
        data = await self._db(self.memory.overview, q)
        return web.json_response(data)

    async def _memory_action(self, request):
        from aiohttp import web
        err = self._mem_guard(request)
        if err is not None:
            return err
        try:
            p = await request.json()
        except Exception:
            return web.json_response({"erro": "Requisição inválida."}, status=400)
        mem, act = self.memory, str(p.get("acao", ""))
        text = " ".join(str(p.get("texto", "")).split())[:300]
        try:
            ident = int(p.get("id", 0) or 0)
        except (TypeError, ValueError):
            ident = 0

        def run():
            old = next((i["instruction"] for i in mem.list_active_instructions(limit=200) if i["id"] == ident), "")
            if act == "revogar" and ident:
                return mem.revoke_by_id(ident), f"a instrução \"{old}\" foi revogada; não a siga mais" if old else ""
            if act == "editar" and ident and text:
                return mem.edit_instruction(ident, text), f"a instrução \"{old}\" agora é: \"{text}\""
            if act == "nova" and text:
                return mem.upsert_instruction(text, actor="painel", priority=5), f"nova instrução vigente: \"{text}\""
            if act in ("validar", "rejeitar") and ident:
                return mem.validate_memory(ident, reviewer=self.operator or "painel", ok=act == "validar"), ""
            if act == "apagar" and ident:
                return mem.delete_memory(ident), ""
            if act == "esquecer_tudo":
                return mem.forget_all(), "toda a memória guardada foi apagada; esqueça instruções e preferências anteriores"
            return False, ""
        ok, note = await self._db(run)
        if ok and note:
            self.commands.put(("memory_note", {"texto": note}))    # a IA fica sabendo na hora
        if ok:
            await self._db(mem.flush, 0.5)
        return web.json_response({"ok": bool(ok), "version": mem.version}, status=200 if ok else 400)

    async def _offer(self, request):
        from aiohttp import web
        from aiortc import RTCPeerConnection, RTCSessionDescription
        try:
            params = await request.json()
        except Exception:
            return web.json_response({"erro": "Requisição inválida."}, status=400)
        if self.failed_pins >= 10:
            return web.json_response({"erro": "Muitas tentativas. Gere um novo PIN no robô."}, status=429)
        if not secrets.compare_digest(str(params.get("pin", "")), self.pin):
            self.failed_pins += 1
            await asyncio.sleep(1.0)                            # freia tentativas de adivinhar o PIN
            return web.json_response({"erro": "PIN incorreto."}, status=403)
        self.failed_pins = 0
        await self._close_pc()                                  # um operador por vez: o novo assume
        pc = RTCPeerConnection()
        self._pc = pc
        name = (str(params.get("nome", "")).strip() or request.remote or "operador")[:40]

        @pc.on("datachannel")
        def on_datachannel(channel):
            self._channel = channel

            @channel.on("message")
            def on_message(message):
                self._handle_message(message)

        @pc.on("track")
        def on_track(track):
            if track.kind == "audio":
                self._loop.create_task(self._consume_operator_audio(track))

        @pc.on("connectionstatechange")
        async def on_state():
            if pc.connectionState == "connected":
                self.operator = name
                self.status = f"operador conectado · {name}"
                self.commands.put(("operator", name))
            elif pc.connectionState in ("failed", "closed", "disconnected"):
                if self._pc is pc:
                    await self._close_pc()

        self._mic = self.mic_factory()
        mic_ok = self._mic.start()
        pc.addTrack(CameraTrack(self.frame_provider, lambda: self.fps))
        if mic_ok:
            pc.addTrack(MicTrack(self._mic))
        await pc.setRemoteDescription(RTCSessionDescription(sdp=params["sdp"], type=params["type"]))
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        return web.json_response({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type,
                                  "microfone": mic_ok})

    async def _close_pc(self) -> None:
        pc, self._pc = self._pc, None
        self._channel = None
        if self._mic is not None:
            self._mic.stop()
            self._mic = None
        self.ptt = False
        if pc is not None:
            try:
                await pc.close()
            except Exception:
                pass
        if self.operator:
            self.operator = ""
            self.commands.put(("operator", ""))
        if self._runner is not None:
            self.status = "aguardando operador"

    async def _consume_operator_audio(self, track) -> None:
        from audio_duplex import StreamResampler
        rs, rs_rate = None, 0
        self._ensure_voice_worker()
        while True:
            try:
                frame = await track.recv()
            except Exception:
                return
            if not self.ptt:
                continue
            pcm = frame.to_ndarray()
            if pcm.ndim > 1:                                    # (canais, amostras) intercalado → mono
                ch = len(frame.layout.channels)
                pcm = pcm.reshape(-1, ch).mean(axis=1) if pcm.shape[0] == 1 else pcm.mean(axis=0)
            pcm = np.asarray(pcm, dtype=np.float32)
            if frame.format.name in ("flt", "fltp"):
                pcm = pcm * 32767.0
            if rs is None or rs_rate != frame.sample_rate:          # reamostragem contínua (sem cliques entre blocos)
                rs, rs_rate = StreamResampler(frame.sample_rate, 24000), frame.sample_rate
            mono = np.clip(rs.process(pcm), -32768, 32767).astype(np.int16)
            try:
                self._voice_q.put_nowait(mono)                     # vocoder/alto-falante rodam em outra thread
            except queue.Full:
                pass

    def _ensure_voice_worker(self) -> None:
        if getattr(self, "_voice_thread", None) is not None and self._voice_thread.is_alive():
            return
        self._voice_q = queue.Queue(maxsize=100)

        def work(q=self._voice_q):
            while True:
                pcm = q.get()
                if pcm is None:
                    return
                try:
                    self.on_operator_audio(pcm)
                except Exception:
                    pass

        self._voice_thread = threading.Thread(target=work, name="operador-voz", daemon=True)
        self._voice_thread.start()

    def _handle_message(self, message) -> None:
        try:
            data = json.loads(message)
        except (TypeError, ValueError):
            return
        kind = data.get("tipo")
        if kind not in COMMANDS:
            return
        if kind == "ping":
            self._send_now({"tipo": "pong", "t": data.get("t")})
            if isinstance(data.get("rtt"), (int, float)):
                self.rtt_ms = float(data["rtt"])
            return
        if kind == "ptt":
            self.ptt = bool(data.get("ligado"))
        self.commands.put((kind, data))

    async def _telemetry_loop(self) -> None:
        while True:
            await asyncio.sleep(0.25)
            if self._channel is not None:
                try:
                    payload = dict(self.telemetry())
                    payload["tipo"] = "estado"
                    self._send_now(payload)
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Trilhas de mídia (robô → operador)
# ---------------------------------------------------------------------------
def _track_classes():
    from aiortc import MediaStreamTrack
    import av

    class _CameraTrack(MediaStreamTrack):
        kind = "video"

        def __init__(self, provider, fps=lambda: VIDEO_FPS):
            super().__init__()
            self.provider = provider
            self.fps = fps
            self._t0 = None
            self._n = 0
            self._last = None

        async def recv(self):
            if self._t0 is None:
                self._t0 = time.perf_counter()
            self._n += 1
            fps = max(1, int(self.fps()))
            if self._n == 1 or fps != getattr(self, "_fps", fps):              # mudou a taxa: recomeça o relógio
                self._t0, self._base, self._fps = time.perf_counter(), self._n - 1, fps
            wait = self._t0 + (self._n - self._base) / fps - time.perf_counter()
            if wait > 0:
                await asyncio.sleep(wait)
            loop = asyncio.get_running_loop()
            img = await loop.run_in_executor(None, self.provider)
            if img is not None:
                import cv2
                h, w = img.shape[:2]
                scale = min(1.0, 640 / float(w))
                if scale < 1.0:
                    img = cv2.resize(img, (int(w * scale) // 2 * 2, int(h * scale) // 2 * 2), interpolation=cv2.INTER_AREA)
                img = np.ascontiguousarray(img[:img.shape[0] // 2 * 2, :img.shape[1] // 2 * 2])   # codec exige par
                self._last = img
            elif self._last is None:
                self._last = np.zeros((360, 640, 3), np.uint8)       # sem câmera: quadro preto
            frame = av.VideoFrame.from_ndarray(self._last, format="bgr24")
            self._pts = getattr(self, "_pts", 0) + 90000 // fps
            frame.pts = self._pts
            frame.time_base = fractions.Fraction(1, 90000)
            return frame

    class _MicTrack(MediaStreamTrack):
        kind = "audio"

        def __init__(self, mic):
            super().__init__()
            self.mic = mic
            self._pts = 0

        async def recv(self):
            if hasattr(self.mic, "read_nowait"):               # sem salto de thread por quadro: menos jitter
                block = self.mic.read_nowait()
                waited = 0.0
                while block is None and waited < 0.03:
                    await asyncio.sleep(0.004)
                    waited += 0.004
                    block = self.mic.read_nowait()
                if block is None:
                    block = np.zeros(960, np.int16)
            else:
                loop = asyncio.get_running_loop()
                block = await loop.run_in_executor(None, self.mic.read, 0.03)
            frame = av.AudioFrame.from_ndarray(block.reshape(1, -1), format="s16", layout="mono")
            frame.sample_rate = AUDIO_RATE
            frame.pts = self._pts
            frame.time_base = fractions.Fraction(1, AUDIO_RATE)
            self._pts += len(block)
            return frame

    return _CameraTrack, _MicTrack


def CameraTrack(provider, fps=lambda: VIDEO_FPS):
    return _track_classes()[0](provider, fps)


def MicTrack(mic):
    return _track_classes()[1](mic)
