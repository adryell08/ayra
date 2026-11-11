"""Vídeo da sala do operador: estatísticas, ajuste adaptativo de taxa, trilha (descarte, repetição, pts) e captura por referência."""
import asyncio, sys, time
import numpy as np

ok = fail = 0
def check(nome, cond):
    global ok, fail
    print(("  [OK] " if cond else "  [FALHOU] ") + nome)
    ok, fail = ok + bool(cond), fail + (not cond)

import operador as op

print("Estatísticas do vídeo")
st = op.VideoStats()
t = 100.0
for i in range(31):
    st.record(t + i / 30.0, 0.05, dropped=1 if i == 10 else 0)
s = st.snapshot(t + 1.0)
check("fps real medido na janela (~30)", 28 <= s["fps_real"] <= 32)
check("idade do quadro em ms e descartados contados", s["idade_ms"] == 50 and s["descartados"] == 1)
check("snapshot traz CPU e memória", "cpu_pct" in s and s["mem_mb"] >= 0)
check("janela deslizante: sem envios há > 3 s zera o fps", op.VideoStats().snapshot()["fps_real"] == 0)

print("\nAjuste adaptativo de taxa")
a = op.AdaptiveFps(30)
check("começa no teto", a.eff == 30)
for _ in range(2): a.update(15.0, 100, True)
check("fps real baixo duas vezes seguidas: desce um degrau (30 → 15)", a.eff == 15)
for _ in range(2): a.update(5.0, 100, True)
check("continua baixo: 15 → 10", a.eff == 10)
for _ in range(5): a.update(10.0, 50, True)
check("recuperação exige 6 avaliações boas (histerese): ainda em 10", a.eff == 10)
a.update(10.0, 50, True)
check("6ª avaliação boa: sobe um degrau (10 → 15)", a.eff == 15)
b = op.AdaptiveFps(15)
for _ in range(20): b.update(15.0, 30, True)
check("nunca passa do teto escolhido pelo usuário", b.eff == 15)
c = op.AdaptiveFps(30)
for _ in range(2): c.update(30.0, 900, True)
check("quadro chegando velho (> 400 ms) também reduz", c.eff == 15)
d = op.AdaptiveFps(30)
for _ in range(10): d.update(0.0, 0, False)
check("sem vídeo não mexe na taxa", d.eff == 30)
d.set_cap(10)
check("trocar o teto no menu reajusta na hora", d.eff == 10)

print("\nTrilha de vídeo (aiortc)")
async def trilha():
    frames = [np.full((480, 640, 3), i, np.uint8) for i in range(40)]
    state = {"seq": 0, "img": frames[0], "cap": time.perf_counter()}
    def info():
        return state["img"], state["seq"], state["cap"]
    stats = op.VideoStats()
    tr = op.CameraTrack(lambda: None, lambda: 30, info, stats)
    out = []
    for i in range(12):
        state["seq"] += 3 if i == 5 else 1                                 # câmera avançou 3: 2 quadros descartados
        state["img"], state["cap"] = frames[state["seq"] % 40], time.perf_counter()
        f = await tr.recv()
        out.append(f)
    check("quadros novos viram quadros enviados", len(out) == 12 and stats.dropped == 2)
    check("pts cresce com o tempo real", all(out[i + 1].pts >= out[i].pts for i in range(11)))
    n_before = stats.repeated
    t0 = time.perf_counter()
    f2 = await tr.recv()                                                   # câmera não avançou: espera e repete só para manter o fluxo
    check("sem quadro novo: repete depois de ~1,5 período, sem recodificar a imagem", stats.repeated == n_before + 1 and time.perf_counter() - t0 < 0.2)
    odd = np.zeros((481, 641, 3), np.uint8)
    state.update(img=odd, seq=state["seq"] + 1, cap=time.perf_counter())
    f3 = await tr.recv()
    check("dimensões ímpares viram pares (codec)", f3.width % 2 == 0 and f3.height % 2 == 0)
    big = np.zeros((960, 1280, 3), np.uint8)
    state.update(img=big, seq=state["seq"] + 1, cap=time.perf_counter())
    f4 = await tr.recv()
    check("acima de 640 de largura reduz para 640", f4.width == 640 and f4.height == 480)
    # atraso grande: relógio refeito, sem rajada
    await asyncio.sleep(0.5)
    t1 = time.perf_counter()
    state.update(seq=state["seq"] + 1, cap=time.perf_counter())
    await tr.recv()
    state.update(seq=state["seq"] + 1, cap=time.perf_counter())
    await tr.recv()
    check("depois de um atraso o relógio é refeito (sem recuperar em rajada)", stats.reanchored >= 1 and time.perf_counter() - t1 >= 0.03)
    legacy = op.CameraTrack(lambda: np.zeros((480, 640, 3), np.uint8), lambda: 30)
    fl = await legacy.recv()
    check("provedor antigo (sem sequência) continua funcionando", fl.width == 640)
    none = op.CameraTrack(lambda: None, lambda: 30)
    fn = await none.recv()
    check("sem câmera: quadro preto", fn.width == 640 and fn.height == 360)
asyncio.run(trilha())

print("\nCaptura (tracker)")
import tracker
cam = tracker.CameraSource.__new__(tracker.CameraSource)
import threading
cam._lock, cam._full, cam._full_seq, cam._full_last, cam._full_wanted = threading.Lock(), None, 0, 0.0, -1e9
cam._paused, cam.status = threading.Event(), "ok"
cam.operator_fps = 16
img = np.zeros((4, 4, 3), np.uint8)
cam._full, cam._full_seq, cam._full_last = img, 7, 5.0
f, seq, ts = cam.latest_frame_info()
check("quadro entregue por referência (sem cópia), com sequência e instante", f is img and seq == 7 and ts == 5.0)
check("latest_frame continua devolvendo o quadro (compatibilidade)", cam.latest_frame() is img)
cam.set_operator_fps(30)
check("taxa pedida pela sala vai de 1 a 60", cam.operator_fps == 30 and (cam.set_operator_fps(999), cam.operator_fps)[1] == 60)
cam._paused.set()
check("câmera pausada: nada é entregue", cam.latest_frame_info()[0] is None)

print(f"\n{ok}/{ok + fail} verificações OK (simulado; sem câmera real nem rede)")
sys.exit(1 if fail else 0)
