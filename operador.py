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
import collections
import fractions
import json
import os
import queue
import re
import secrets
import socket
import ssl
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np


class VideoStats:
    """Medidas do vídeo enviado ao operador (janela deslizante): fps real, idade do quadro no envio, descartados, CPU e memória.
    Só números, sem imagem. Chamado pela trilha (laço asyncio) e lido pela telemetria (mesma thread)."""

    WINDOW = 3.0

    def __init__(self):
        self.sent = collections.deque()            # (instante do envio, idade em s)
        self.dropped = 0                            # quadros da câmera que ninguém enviou (substituídos antes)
        self.repeated = 0                           # quadros repetidos só para manter o fluxo vivo
        self.reanchored = 0                         # vezes que o relógio foi refeito por atraso (sem rajada de recuperação)
        self._cpu0 = (time.perf_counter(), sum(os.times()[:2]))

    def record(self, now: float, age: float, dropped: int = 0, repeated: bool = False) -> None:
        self.sent.append((now, max(0.0, age)))
        self.dropped += max(0, dropped)
        self.repeated += 1 if repeated else 0
        while self.sent and now - self.sent[0][0] > self.WINDOW:
            self.sent.popleft()

    def snapshot(self, now: Optional[float] = None) -> dict:
        now = time.perf_counter() if now is None else now
        while self.sent and now - self.sent[0][0] > self.WINDOW:
            self.sent.popleft()
        n = len(self.sent)
        span = (self.sent[-1][0] - self.sent[0][0]) if n > 1 else 0.0
        fps = (n - 1) / span if span > 0.2 else 0.0
        ages = sorted(a for _, a in self.sent)
        t, cpu = self._cpu0
        cpu_now = sum(os.times()[:2])
        pct = 100.0 * (cpu_now - cpu) / max(1e-3, now - t)
        self._cpu0 = (now, cpu_now)
        return dict(fps_real=round(fps, 1), idade_ms=round(1000 * ages[n // 2]) if n else 0,
                    idade_p95_ms=round(1000 * ages[min(n - 1, int(n * 0.95))]) if n else 0, descartados=self.dropped,
                    repetidos=self.repeated, relogio_refeito=self.reanchored, cpu_pct=round(pct), mem_mb=_rss_mb())


def _rss_mb() -> int:
    try:
        import psutil
        return int(psutil.Process().memory_info().rss / 1048576)
    except Exception:
        try:
            import resource
            return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)     # pico, em MB (Linux)
        except Exception:
            return 0


class AdaptiveFps:
    """Escolhe a taxa efetiva abaixo do teto do usuário quando o envio não acompanha, e volta a subir com folga (histerese).
    Degrada se o fps real ficar < 75 % do alvo ou o quadro chegar velho (> 400 ms) em 2 avaliações seguidas; sobe um degrau depois de
    6 avaliações boas (≥ 95 % do alvo e idade < 150 ms)."""

    def __init__(self, cap: int, choices=(5, 10, 15, 30)):
        self.choices = sorted(choices)
        self.cap = cap
        self.eff = cap
        self.bad = self.good = 0

    def set_cap(self, cap: int) -> None:
        self.cap = cap
        self.eff = min(self.eff, cap) if self.eff != cap else cap
        self.bad = self.good = 0

    def _step(self, direction: int) -> int:
        lower = [c for c in self.choices if c <= self.cap]
        i = lower.index(self.eff) if self.eff in lower else len(lower) - 1
        return lower[max(0, min(len(lower) - 1, i + direction))]

    def update(self, fps_real: float, age_ms: float, has_video: bool) -> int:
        if not has_video:
            return self.eff
        if fps_real < 0.75 * self.eff or age_ms > 400:
            self.bad, self.good = self.bad + 1, 0
            if self.bad >= 2:
                self.eff, self.bad = self._step(-1), 0
        elif fps_real >= 0.95 * self.eff and age_ms < 150:
            self.good, self.bad = self.good + 1, 0
            if self.good >= 6:
                self.eff, self.good = self._step(+1), 0
        else:
            self.bad = self.good = 0
        return self.eff

WEB_DIR = Path(__file__).resolve().parent / "operador_web"
CERT_DIR = Path(__file__).resolve().parent / "certs"
VIDEO_FPS = 10                         # padrão (5, 10, 15 ou 30 em Ajustes → Controle remoto → Vídeo)
FPS_CHOICES = (5, 10, 15, 30)
VIDEO_MAX_WIDTH = 640                  # largura máxima enviada (mais que isso só gasta CPU na codificação)
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

    MAX_COMMANDS = 256           # fila de comandos do navegador (consumida a cada quadro pelo app)

    def __init__(self, frame_provider: Callable[[], Optional[np.ndarray]], telemetry: Callable[[], dict],
                 on_operator_audio: Callable[[np.ndarray], None], robot_muted: Callable[[], bool] = lambda: False,
                 port: int = 8765, pin: Optional[str] = None, use_tls: bool = True, mic_factory=None,
                 fps: int = VIDEO_FPS, frame_info_provider=None, on_fps=None, adaptive_fps: bool = True):
        self.frame_provider, self.telemetry, self.on_operator_audio = frame_provider, telemetry, on_operator_audio
        self.frame_info_provider = frame_info_provider      # () -> (quadro, sequência, captura): permite descartar repetidos e medir idade
        self.on_fps = on_fps                                 # avisa a captura da taxa efetiva (a câmera só entrega o que a sala pede)
        self.video_stats = VideoStats()
        self.adaptive_fps = bool(adaptive_fps)
        self._fps_cap = int(fps) if int(fps) in FPS_CHOICES else VIDEO_FPS
        self._adaptive = AdaptiveFps(self._fps_cap)
        self._fps = self._fps_cap
        self._video_snap: dict = {}
        self.port, self.pin, self.use_tls = port, pin or new_pin(), use_tls
        self.commands: "queue.Queue[tuple]" = queue.Queue(maxsize=self.MAX_COMMANDS)
        self.commands_dropped = 0      # comandos descartados por fila cheia (o app esvazia a fila a cada quadro)
        self.dropped_by_kind: dict = {}
        self.commands_lost_important = 0   # perdas de fala/instrução/conexão/estado/interrupção que não eram só repetição
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
        self.drop_grace = 20.0                   # s: queda de rede não devolve o robô ao autônomo na hora; dá tempo de o painel voltar
        self.ice_grace = 6.0                     # s: 'disconnected' do ICE costuma se recuperar sozinho (Wi-Fi/troca de rede)
        self.reconnections = 0                   # operador que voltou dentro da tolerância
        self._clear_handle = None
        self._ice_handle = None
        self.memory = None                       # memoria.Memory: painel /memoria (operador ou o próprio usuário)
        self.rag_provider = None                 # () -> ContextAssembler | None: métricas do RAG no painel privado /rag
        self._mem_tokens: dict = {}              # token -> validade (s); entra com o mesmo PIN da sala
        self._mem_hits: list = []
        self._db_pool = None

    # ---- taxa de vídeo ------------------------------------------------------------------
    @property
    def fps(self) -> int:
        """Taxa efetiva (a trilha lê isto a cada quadro). Com o ajuste adaptativo pode ficar abaixo do teto escolhido."""
        return self._fps

    @fps.setter
    def fps(self, value: int) -> None:
        """Teto escolhido pelo usuário (Ajustes). Vale na hora, sem reconectar."""
        value = int(value) if int(value) in FPS_CHOICES else VIDEO_FPS
        self._fps_cap = value
        self._adaptive.set_cap(value)
        self._set_effective(value)

    @property
    def fps_cap(self) -> int:
        return self._fps_cap

    def _set_effective(self, value: int) -> None:
        self._fps = value
        if self.on_fps is not None:
            try:
                self.on_fps(value)
            except Exception:
                pass

    def _adapt_video(self) -> None:
        """Chamado ~1×/s pela telemetria: ajusta a taxa efetiva sem passar do teto do usuário."""
        snap = self.video_stats.snapshot()
        self._video_snap = snap
        if not self.adaptive_fps or self._pc is None:
            return
        eff = self._adaptive.update(snap["fps_real"], snap["idade_ms"], snap["fps_real"] > 0)
        if eff != self._fps:
            self._set_effective(eff)

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
        app.router.add_get("/ayrabrain", self._brain_page)
        app.router.add_get("/rag", self._rag_page)
        app.router.add_get("/api/rag", self._rag_get)
        app.router.add_post("/api/rag/acao", self._rag_action)
        app.router.add_get("/api/ayrabrain", self._brain_get)
        app.router.add_post("/api/ayrabrain/acao", self._brain_action)
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
            self._push(("memory_note", {"texto": note}))    # a IA fica sabendo na hora
        if ok:
            await self._db(mem.flush, 0.5)
        return web.json_response({"ok": bool(ok), "version": mem.version}, status=200 if ok else 400)

    # ---- Ayrabrain: mapa das instruções e memórias (mesmo PIN/token do painel de memória; só lê quando a página pede) ----
    BRAIN_ERRORS = {"ref_invalida": "Nó inválido.", "mesmo_no": "Escolha dois nós diferentes.",
                    "relacao_invalida": "Tipo de ligação inválido.", "no_inexistente": "Esse nó não existe mais.",
                    "ja_existe": "Essa ligação já existe.", "limite_ligacoes": "Limite de ligações atingido.",
                    "limite_no": "Esse nó já tem ligações demais.", "nao_encontrada": "Ligação não encontrada.",
                    "fila_cheia": "Memória ocupada; tente de novo.", "indisponivel": "Memória indisponível."}

    async def _brain_page(self, request):
        from aiohttp import web
        return web.FileResponse(WEB_DIR / "ayrabrain.html", headers={"Cache-Control": "no-store"})

    @staticmethod
    def _int_arg(value, default: int, low: int, high: int) -> int:
        try:
            return max(low, min(int(value), high))
        except (TypeError, ValueError):
            return default

    async def _brain_get(self, request):
        from aiohttp import web
        err = self._mem_guard(request)
        if err is not None:
            return err
        qs = request.query
        focus = qs.get("focus", "")[:14]
        if focus and not re.fullmatch(r"[mi]:\d{1,9}", focus):
            return web.json_response({"erro": self.BRAIN_ERRORS["ref_invalida"]}, status=400)
        if not qs.get("q") and not focus and qs.get("v") == str(self.memory.version) and qs.get("offset", "0") == "0":
            return web.json_response({"version": self.memory.version, "same": True})   # nada mudou: resposta mínima
        data = await self._db(lambda: self.memory.brain_graph(
            q=qs.get("q", "")[:80], focus=focus, limit=self._int_arg(qs.get("limit"), 80, 1, 150),
            offset=self._int_arg(qs.get("offset"), 0, 0, 5000)))
        if data.get("error"):
            return web.json_response({"erro": self.BRAIN_ERRORS.get(data["error"], "Erro.")}, status=404 if data["error"] == "no_inexistente" else 503)
        return web.json_response(data)

    async def _brain_action(self, request):
        from aiohttp import web
        err = self._mem_guard(request)
        if err is not None:
            return err
        try:
            p = await request.json()
        except Exception:
            return web.json_response({"erro": "Requisição inválida."}, status=400)
        if not isinstance(p, dict):
            return web.json_response({"erro": "Requisição inválida."}, status=400)
        mem, act = self.memory, str(p.get("acao", ""))
        if act == "ligar":
            ok, why = await self._db(lambda: mem.link(str(p.get("a", ""))[:14], str(p.get("b", ""))[:14],
                                                      str(p.get("relacao", "relacionado"))[:20], actor=self.operator or "painel"))
        elif act == "desligar":
            ok, why = await self._db(lambda: mem.unlink(p.get("id")))
        else:
            return web.json_response({"erro": "Ação desconhecida."}, status=400)
        if ok:
            await self._db(mem.flush, 0.5)
        return web.json_response({"ok": bool(ok), "motivo": why, "erro": "" if ok else self.BRAIN_ERRORS.get(why, "Não foi possível."),
                                  "version": mem.version}, status=200 if ok else 400)

    # ---- painel privado do RAG: só números (uso, prazos estourados, bloqueados, tempos) e a chave de desligar; nunca texto de conversa ----
    async def _rag_page(self, request):
        from aiohttp import web
        return web.FileResponse(WEB_DIR / "rag.html", headers={"Cache-Control": "no-store"})

    def _rag(self):
        try:
            return self.rag_provider() if self.rag_provider else None
        except Exception:
            return None

    async def _rag_get(self, request):
        from aiohttp import web
        err = self._mem_guard(request)
        if err is not None:
            return err
        asm = self._rag()
        if asm is None:
            return web.json_response(dict(disponivel=False, ligado=False, motivo="Memória desligada ou RAG indisponível."))
        data = await self._db(asm.metricas)
        data["disponivel"] = True
        return web.json_response(data)

    async def _rag_action(self, request):
        from aiohttp import web
        err = self._mem_guard(request)
        if err is not None:
            return err
        try:
            p = await request.json()
        except Exception:
            return web.json_response({"erro": "Requisição inválida."}, status=400)
        asm = self._rag()
        act = str(p.get("acao", "")) if isinstance(p, dict) else ""
        if asm is None:
            return web.json_response({"erro": "RAG indisponível."}, status=409)
        if act == "desligar":
            asm.desligar()
            return web.json_response({"ok": True, "ligado": False})
        if act == "ligar":
            ok = asm.ligar()
            return web.json_response({"ok": ok, "ligado": bool(asm.cfg.rag_ligado),
                                      "erro": "" if ok else "O arquivo de configuração (rag.local.json) não autoriza ligar."}, status=200 if ok else 403)
        return web.json_response({"erro": "Ação desconhecida."}, status=400)

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
        await self._close_pc(delay_clear=True)                  # um operador por vez: o novo assume (sem devolver ao autônomo)
        pc = RTCPeerConnection()
        self._pc = pc
        name = (str(params.get("nome", "")).strip() or request.remote or "operador")[:40]

        @pc.on("datachannel")
        def on_datachannel(channel):
            self._channel = channel

            @channel.on("message")
            def on_message(message):
                if self._pc is pc and self._channel is channel:     # conexão antiga/fechada não comanda o robô
                    self._handle_message(message)

        @pc.on("track")
        def on_track(track):
            if track.kind == "audio":
                self._loop.create_task(self._consume_operator_audio(track))

        @pc.on("connectionstatechange")
        async def on_state():
            state = pc.connectionState
            if self._pc is not pc:
                return
            if state == "connected":
                self._cancel_timers()
                if self.operator == name:
                    self.reconnections += 1 if self.status.startswith("operador reconectando") else 0
                self.operator = name
                self.status = f"operador conectado · {name}"
                self._push(("operator", name))
            elif state == "disconnected":                       # pode voltar sozinho: espera antes de derrubar
                self.status = f"operador reconectando · {name}"
                if self._ice_handle is None:
                    self._ice_handle = self._loop.call_later(self.ice_grace, lambda: self._loop.create_task(self._ice_expired(pc)))
            elif state in ("failed", "closed"):
                await self._close_pc(delay_clear=True)

        self._mic = self.mic_factory()
        mic_ok = self._mic.start()
        self.video_stats = VideoStats()                         # medidas novas a cada conexão
        self._adaptive = AdaptiveFps(self._fps_cap)
        self._set_effective(self._fps_cap)
        pc.addTrack(CameraTrack(self.frame_provider, lambda: self.fps, self.frame_info_provider, self.video_stats))
        if mic_ok:
            pc.addTrack(MicTrack(self._mic))
        await pc.setRemoteDescription(RTCSessionDescription(sdp=params["sdp"], type=params["type"]))
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        return web.json_response({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type,
                                  "microfone": mic_ok})

    # Importância de cada comando quando a fila enche (0 = descarta primeiro). Os de "último vale" (estado) só perdem entradas
    # que um comando mais novo do mesmo tipo já substituiu: o estado final fica correto e `interrupt`/`mode` nunca somem.
    _RANK = {"look": 0, "expression": 1, "gesture": 2, "say": 3, "instruction": 3, "memory_note": 3, "operator": 4,
             "mode": 5, "vision": 5, "body": 5, "robot_voice": 5, "ptt": 5, "interrupt": 6}
    _LAST_WINS = ("mode", "vision", "body", "robot_voice", "ptt", "interrupt")

    def _pick_victim(self, q, incoming):
        """Quem sai da fila cheia para o `incoming` entrar: (item | None, motivo). None = o próprio `incoming` é descartado."""
        kind = incoming[0]
        items = list(q)
        # 1) substituídos: comando de estado com outro mais novo do mesmo tipo (nada se perde de verdade)
        last = {c[0]: i for i, c in enumerate(items)}
        for i, c in enumerate(items):
            if c[0] in self._LAST_WINS and (c[0] == kind or last[c[0]] != i):
                return c, "substituido"
        # 2) o menos importante (o mais antigo entre iguais), mas nunca à custa de algo mais importante que o novo
        rank_in = self._RANK.get(kind, 3)
        best = min(items, key=lambda c: self._RANK.get(c[0], 3), default=None)
        if best is not None and self._RANK.get(best[0], 3) <= rank_in:
            return best, "descartado"
        return None, "novo_descartado"

    def _push(self, item) -> None:
        """Enfileira sem nunca bloquear a thread de rede. Fila cheia: primeiro saem comandos de estado já substituídos
        (mode/vision/body/ptt/robot_voice/interrupt repetidos); depois o menos importante (olhar < expressão < gesto <
        fala/instrução < conexão < estado < interrupção), o mais antigo entre iguais; um comando pouco importante não expulsa
        um mais importante (ele mesmo é descartado). A ordem dos que ficam é preservada. Tudo é contado:
        `commands_dropped` (total), `dropped_by_kind` e `commands_lost_important` (perdas que NÃO foram só substituição:
        fala/instrução/conexão/estado/interrupção)."""
        while True:
            try:
                self.commands.put_nowait(item)
                return
            except queue.Full:
                with self.commands.mutex:
                    q = self.commands.queue
                    victim, why = self._pick_victim(q, item)
                    if victim is None:
                        self._count_drop(item, why)
                        return
                    q.remove(victim)
                self._count_drop(victim, why)

    def _count_drop(self, cmd, why: str) -> None:
        self.commands_dropped += 1
        kind = cmd[0]
        self.dropped_by_kind[kind] = self.dropped_by_kind.get(kind, 0) + 1
        if why != "substituido" and self._RANK.get(kind, 3) >= 3:
            self.commands_lost_important += 1

    def _cancel_timers(self) -> None:
        for attr in ("_clear_handle", "_ice_handle"):
            h = getattr(self, attr)
            if h is not None:
                h.cancel()
                setattr(self, attr, None)

    async def _ice_expired(self, pc) -> None:
        self._ice_handle = None
        if self._pc is pc and pc.connectionState != "connected":
            await self._close_pc(delay_clear=True)

    def _clear_operator(self) -> None:
        self._clear_handle = None
        if self._pc is None and self.operator:
            self.operator = ""
            self.status = "aguardando operador"
            self._push(("operator", ""))                         # só agora o robô volta ao autônomo

    async def _close_pc(self, delay_clear: bool = False) -> None:
        """delay_clear=True (queda de rede): o operador some do painel só depois de `drop_grace` s; se ele voltar antes, nada muda no robô."""
        self._cancel_timers()
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
            if delay_clear and self._loop is not None and self.drop_grace > 0:
                self.status = f"operador reconectando · {self.operator}"
                self._clear_handle = self._loop.call_later(self.drop_grace, self._clear_operator)
                return
            self.operator = ""
            self._push(("operator", ""))
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
        self._push((kind, data))

    async def _telemetry_loop(self) -> None:
        n = 0
        while True:
            await asyncio.sleep(0.25)
            n += 1
            if n % 4 == 0:
                try:
                    self._adapt_video()
                except Exception:
                    pass
            if self._channel is not None:
                try:
                    payload = dict(self.telemetry())
                    payload["tipo"] = "estado"
                    payload["fps"] = self._fps
                    payload["fps_teto"] = self._fps_cap
                    payload["video"] = getattr(self, "_video_snap", {})
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
        """Vídeo robô → operador. Relógio fixo por taxa; quadro atrasado NÃO gera rajada de recuperação (refaz o relógio);
        quadro repetido da câmera não é recodificado (espera o novo, com repetição só para manter o fluxo); pts vem do tempo real."""
        kind = "video"

        def __init__(self, provider, fps=lambda: VIDEO_FPS, info_provider=None, stats: Optional[VideoStats] = None):
            super().__init__()
            self.provider = provider
            self.info_provider = info_provider          # () -> (quadro, sequência, instante da captura), sem bloquear
            self.fps = fps
            self.stats = stats if stats is not None else VideoStats()
            self._t0 = None
            self._n = 0
            self._base = 0
            self._fps = None
            self._last = None
            self._last_seq = None
            self._pts0 = None
            self._legacy_seq = 0

        def _fetch(self):
            if self.info_provider is not None:
                return self.info_provider()
            return None

        async def recv(self):
            now = time.perf_counter()
            fps = max(1, int(self.fps()))
            period = 1.0 / fps
            if self._t0 is None or fps != self._fps:                      # primeira vez ou mudou a taxa: relógio novo
                self._t0, self._base, self._fps, self._n = now, 0, fps, 0
            self._n += 1
            wait = self._t0 + (self._n - self._base) * period - now
            if wait > 0:
                await asyncio.sleep(wait)
            elif wait < -period:                                           # atrasado mais de um quadro: descarta o atraso
                self._t0, self._base, self._n = now, 0, 1
                self.stats.reanchored += 1
            loop = asyncio.get_running_loop()
            img, seq, captured = None, None, 0.0
            repeated = False
            if self.info_provider is not None:
                deadline = time.perf_counter() + 1.5 * period
                while True:
                    img, seq, captured = self._fetch()
                    if img is None or seq != self._last_seq or time.perf_counter() >= deadline:
                        break
                    await asyncio.sleep(0.004)                             # mesmo quadro de antes: espera o próximo, sem recodificar
                repeated = img is not None and seq == self._last_seq
            else:
                img = await loop.run_in_executor(None, self.provider)
                self._legacy_seq += 1
                seq, captured = self._legacy_seq, time.perf_counter()
            dropped = 0
            if img is not None:
                if self._last_seq is not None and not repeated and isinstance(seq, int) and isinstance(self._last_seq, int):
                    dropped = max(0, seq - self._last_seq - 1)             # quadros da câmera substituídos sem serem enviados
                if not repeated or self._last is None:
                    import cv2
                    h, w = img.shape[:2]
                    if w > VIDEO_MAX_WIDTH:
                        sc = VIDEO_MAX_WIDTH / float(w)
                        img = cv2.resize(img, (int(w * sc) // 2 * 2, int(h * sc) // 2 * 2), interpolation=cv2.INTER_AREA)
                    elif h % 2 or w % 2:
                        img = img[:h // 2 * 2, :w // 2 * 2]                # codec exige dimensões pares
                    self._last = np.ascontiguousarray(img)
                self._last_seq = seq
            elif self._last is None:
                self._last = np.zeros((360, 640, 3), np.uint8)             # sem câmera: quadro preto
            frame = av.VideoFrame.from_ndarray(self._last, format="bgr24")
            t = time.perf_counter()
            if self._pts0 is None:
                self._pts0 = t
            frame.pts = int((t - self._pts0) * 90000)                       # tempo real: sem deriva quando quadros são descartados
            frame.time_base = fractions.Fraction(1, 90000)
            self.stats.record(t, (t - captured) if captured else 0.0, dropped=dropped, repeated=repeated)
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


def CameraTrack(provider, fps=lambda: VIDEO_FPS, info_provider=None, stats=None):
    return _track_classes()[0](provider, fps, info_provider, stats)


def MicTrack(mic):
    return _track_classes()[1](mic)
