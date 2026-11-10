"""Gesto e expressão são canais independentes: pedir um não cancela, não troca e não adia o outro (operador e IA).
Ex.: dedo do meio + expressão "Bravo" (discussão) ou + "Feliz" (zoeira entre amigos). Simulado, sem rede nem API paga.
Uso: python test_gesto_expressao.py"""
import os, sys
from pathlib import Path
os.environ.setdefault("SDL_VIDEODRIVER", "dummy"); os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import orquestrador as oq
import gestos

R = []
def check(n, c):
    R.append(bool(c)); print(f"  [{'OK' if c else 'FALHOU'}] {n}")

o = oq.Orchestrator(duration_of=lambda n: 2.0)
check("gesto não usa o canal de expressão", all(oq.EXPRESSION not in oq.channels_for(g) for g in gestos.LIBRARY))
for quem in ("operator", "ai"):
    for ordem in ("expressao_antes", "gesto_antes"):
        o = oq.Orchestrator(duration_of=lambda n: 2.0); t = 10.0
        if ordem == "expressao_antes":
            e = o.claim_expression(quem, t); g = o.submit_gesture(quem, "dedo_do_meio", t + .1)
        else:
            g = o.submit_gesture(quem, "dedo_do_meio", t); e = o.claim_expression(quem, t + .1)
        o.update(t + .5)
        ativos = o.active(t + .5)
        check(f"{quem} · {ordem}: gesto inicia e expressão é aceita ao mesmo tempo", g == "started" and e is True)
        check(f"{quem} · {ordem}: os dois ficam ativos juntos (canais separados)", oq.EXPRESSION in ativos and (oq.ARM_R in ativos or oq.ARM_L in ativos))
o = oq.Orchestrator(duration_of=lambda n: 2.0)
o.claim_expression("operator", 1.0); o.submit_gesture("ai", "tchau", 1.1)
check("operador segura a expressão e a IA ainda pode gesticular (mas não trocar a expressão)", not o.expression_allowed("ai", 1.2) and o.active(1.2).get(oq.ARM_R) is not None)
o2 = oq.Orchestrator(duration_of=lambda n: 2.0); o2.submit_gesture("operator", "joia", 1.0)
check("gesto do operador não bloqueia a expressão da IA", o2.expression_allowed("ai", 1.2))
html = (Path(__file__).resolve().parent / "operador_web" / "index.html").read_text(encoding="utf-8")
src = (Path(__file__).resolve().parent / "gestos.py").read_text(encoding="utf-8") + (Path(__file__).resolve().parent / "corpo3d.py").read_text(encoding="utf-8")
check("código dos gestos/corpo não força expressão (sem set_mood/claim_expression)", "set_mood" not in src and "claim_expression" not in src)
print(f"\n{sum(R)}/{len(R)} verificações OK"); sys.exit(0 if all(R) else 1)
