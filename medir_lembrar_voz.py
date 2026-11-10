"""Medida SIMULADA da ferramenta `lembrar` (servidor Realtime FALSO com latência fixa): quanto o app acrescenta quando o modelo chama `lembrar`
(busca local + volta ao servidor), e quantas chamadas/continuações acontecem. NÃO mede rede, tokens nem latência real da API: a validação com sessão real
(tokens de usage, tempo até o primeiro áudio) continua PENDENTE. Uso: python medir_lembrar_voz.py [--n 10]"""
import argparse, json, statistics as st, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_lembrar_voz as L


def rodar(plan, n):
    h, mem, _ = L.montar(plan, True, True)
    h.a.rag_assembler().cfg.max_itens = 4
    h.a.ask_text("aquecimento")
    h.idle(8)
    ts, calls = [], []
    for _ in range(n):
        antes = len(h.sent("response.create"))
        h.servers[-1].n = 0
        h.a.ask_text("Onde ficam os sensores de distância?")
        h.idle(8)
        s = h.a.snapshot()
        ts.append(s.timings.get("primeiro_audio", 0) * 1000)
        calls.append(len(h.sent("response.create")) - antes)
    met = h.a.rag.metricas()
    h.close(); mem.close()
    return dict(plano=plan, primeiro_audio_p50_ms=round(st.median(ts), 1), primeiro_audio_p95_ms=round(sorted(ts)[int(len(ts) * .95)], 1),
                response_create_por_pergunta=round(st.mean(calls), 2), recuperacao_p50_ms=met["ms_recuperacao"]["p50"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=10); a = ap.parse_args()
    print(json.dumps([rodar(["audio"], a.n), rodar(["lembrar", "audio"], a.n)], ensure_ascii=False, indent=1))
    print("SIMULADO (latência fixa do servidor falso): a diferença é o custo local + a volta extra, não latência real.")
