"""Ganho do microfone do operador (simulado: sinais sintéticos, sem rede nem microfone)."""
import numpy as np
from operador import apply_mic_gain, mic_gain_clamp, OperatorServer, MIC_GAIN_DEFAULT

ok = fail = 0
def check(nome, cond):
    global ok, fail
    if cond: ok += 1
    else: fail += 1; print("  [FALHOU]", nome)

t = np.arange(4800) / 24000.0
x = (3000 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
rms = lambda a: float(np.sqrt(np.mean(a ** 2)))

check("100 % não altera", np.array_equal(apply_mic_gain(x.copy(), 100), x))
check("200 % dobra o nível", abs(rms(apply_mic_gain(x.copy(), 200)) / rms(x) - 2.0) < 0.02)
check("500 % amplifica ~5x (sinal baixo)", abs(rms(apply_mic_gain(x.copy(), 500)) / rms(x) - 5.0) < 0.1)
loud = (30000 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
y = apply_mic_gain(loud.copy(), 500)
check("picos nunca passam da escala", float(np.max(np.abs(y))) <= 32767.0)
check("limitador sem degrau (sem estalos)", float(np.max(np.abs(np.diff(y)))) < 0.5 * 32768)
check("sinal sem NaN", bool(np.all(np.isfinite(y))))
check("clamp mínimo", mic_gain_clamp(10) == 100)
check("clamp máximo", mic_gain_clamp(9999) == 500)
check("clamp lixo → padrão", mic_gain_clamp("abc") == MIC_GAIN_DEFAULT)
silent = np.zeros(480, np.float32)
check("silêncio continua silêncio", not apply_mic_gain(silent.copy(), 500).any())

srv = OperatorServer(lambda: None, lambda: {}, lambda pcm: None, use_tls=False)
saved = []
srv.on_mic_gain = saved.append
srv._handle_message('{"tipo":"mic_gain","nivel":350,"final":false}')
check("ajuste ao arrastar vale na hora", srv.mic_gain == 350 and saved == [])
srv._handle_message('{"tipo":"mic_gain","nivel":350,"final":true}')
check("ao soltar grava", saved == [350])
check("não vai para a fila do robô", srv.commands.empty())
srv._handle_message('{"tipo":"mic_gain","nivel":99999}')
check("valor absurdo é limitado", srv.mic_gain == 500)
print(f"test_ganho_mic: {ok}/{ok+fail} verificações OK (sinais sintéticos; validar com microfone real)")
