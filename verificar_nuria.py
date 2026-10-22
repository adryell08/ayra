"""Checagem rápida antes da demonstração: chave, internet/API, microfone, alto-falante e câmera.

Uso: python verificar_nuria.py [--sem-api] [--camera N]
Não grava nada; a chave nunca é exibida. Cada item mostra OK ou o motivo da falha.
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np

import credenciais


def line(ok, name, detail=""):
    print(f"  [{'OK' if ok else 'FALHA'}] {name}" + (f" — {detail}" if detail else ""))
    return ok


def check_realtime(cfg) -> bool:
    """Conecta de verdade, manda uma imagem + pergunta e mede o tempo até o primeiro áudio."""
    import base64
    import ia
    import realtime as rt
    from assistente_realtime import build_session
    from voz import StreamPlayer
    first, done, text, errors = threading.Event(), threading.Event(), [], []
    player = StreamPlayer()
    t_sent = [0.0]
    lat = [None]

    def on_event(ev):
        kind = ev.get("type")
        if kind == "response.output_audio.delta":
            if not first.is_set():
                lat[0] = time.perf_counter() - t_sent[0]
                first.set()
            player.feed(np.frombuffer(base64.b64decode(ev["delta"]), np.int16))
        elif kind == "response.output_audio_transcript.delta":
            text.append(ev.get("delta", ""))
        elif kind == "response.done":
            done.set()
        elif kind == "error":
            errors.append((ev.get("error") or {}).get("message", "erro"))
            done.set()

    client = rt.RealtimeClient(lambda: rt.WebSocketTransport(cfg.realtime_url.format(model=cfg.realtime_model),
                                                              cfg.api_key, cfg.timeout),
                               on_event, lambda reason: (errors.append(reason), done.set()))
    try:
        t = time.perf_counter()
        session = build_session(cfg)
        try:
            client.connect(session, timeout=cfg.timeout)
        except rt.TransportClosed as error:
            if "reasoning" not in str(error).lower():
                raise
            session.pop("reasoning", None)
            client.connect(session, timeout=cfg.timeout)
        line(True, "Realtime conectada (WebSocket + sessão)", f"{time.perf_counter() - t:.1f} s")
        img = np.full((240, 320, 3), (30, 140, 230), np.uint8)
        url = "data:image/jpeg;base64," + base64.b64encode(ia.encode_jpeg(img)).decode()
        try:
            player.start()
        except Exception as error:
            line(False, "Alto-falante para a Realtime", str(error))
        client.send({"type": "conversation.item.create", "item": {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "Teste técnico: diga em uma frase curta qual cor predomina na imagem."},
            {"type": "input_image", "image_url": url}]}})
        t_sent[0] = time.perf_counter()
        client.send({"type": "response.create"})
        done.wait(30)
        while player.pending():
            time.sleep(0.05)
        player.close()
        if errors:
            return line(False, "Realtime respondeu", errors[0])
        return line(first.is_set(), "Realtime respondeu com áudio",
                    f"1º áudio em {lat[0]:.2f} s: “{''.join(text).strip()}”" if lat[0] else "sem áudio")
    except Exception as error:
        return line(False, "Realtime", str(error))
    finally:
        client.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sem-api", action="store_true", help="não chamar a OpenAI")
    ap.add_argument("--camera", type=int, default=None)
    ap.add_argument("--legado", action="store_true", help="testar também o modo legado (3 chamadas)")
    args = ap.parse_args(argv)
    results = []
    print("Nuria — verificação do ambiente\n")

    cfg = credenciais.load()
    results.append(line(cfg.configured, "Chave da OpenAI configurada",
                        "ok (valor oculto)" if cfg.configured else "crie o .env a partir do .env.example"))
    print(f"         Realtime: {cfg.realtime_model} · voz {cfg.realtime_voice}")

    if cfg.configured and not args.sem_api:
        results.append(check_realtime(cfg))
    if cfg.configured and not args.sem_api and args.legado:
        try:
            import ia
            backend = ia.OpenAIBackend(cfg)
            t = time.perf_counter()
            img = np.full((240, 320, 3), (30, 140, 230), np.uint8)
            answer = backend.answer(ia.encode_jpeg(img), "Que cor predomina nesta imagem? Responda em 5 palavras.")
            results.append(line(True, "Legado: IA multimodal respondeu", f"{answer.model} em {time.perf_counter() - t:.1f} s"))
        except Exception as error:
            results.append(line(False, "Legado: acesso à API", str(error)))

    try:
        import sounddevice as sd
        dev_in = sd.query_devices(kind="input")
        dev_out = sd.query_devices(kind="output")
        results.append(line(True, "Dispositivos de áudio", f"entrada: {dev_in['name']} · saída: {dev_out['name']}"))
        print("         Fale algo por 3 segundos…")
        data = sd.rec(int(3 * 16000), samplerate=16000, channels=1, dtype="int16")
        sd.wait()
        rms = float(np.sqrt(np.mean((data.astype(np.float32) / 32768.0) ** 2)))
        results.append(line(rms > 0.005, "Microfone capta som", f"nível {rms:.3f} (fale mais perto se < 0,005)"))
    except Exception as error:
        results.append(line(False, "Microfone/alto-falante", str(error)))

    try:
        import cv2
        from camera_devices import enumerate_cameras, preferred_cameras
        if args.camera is not None:
            index, name = args.camera, f"#{args.camera}"
        else:
            best = (preferred_cameras(enumerate_cameras()) or [None])[0]
            index, name = (best.index, best.name) if best else (0, "#0")
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW) if sys.platform == "win32" else cv2.VideoCapture(index)
        ok, frame = cap.read() if cap.isOpened() else (False, None)
        cap.release()
        results.append(line(bool(ok) and frame is not None, f"Webcam {name} (#{index}) entrega imagem",
                            f"{frame.shape[1]}×{frame.shape[0]}" if ok else "feche OBS/Teams ou use --camera N"))
    except Exception as error:
        results.append(line(False, "Webcam", str(error)))

    good = sum(results)
    print(f"\n{good}/{len(results)} itens OK")
    return 0 if good == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
