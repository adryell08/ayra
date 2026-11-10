"""Medida LOCAL do custo do RAG na pergunta digitada (servidor Realtime falso: mede só o overhead do app, não a rede nem a API).
Compara RAG desligado × ligado: tempo do envio da pergunta até o primeiro áudio, itens enviados e a montagem. Sem API paga.
Uso: python medir_rag_texto.py [--n 12]"""
import argparse, json, statistics as st, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_realtime as T
from memoria import Memory


def rodar(rag: bool, n: int):
    h = T.Harness()
    tmp = Path(tempfile.mkdtemp(prefix="medrag_"))
    mem = Memory(tmp / "m.db", user_id="local")
    for t in ("O armário 3 guarda os sensores de distância.", "A reunião do clube de robótica é toda quinta às 16h.", "Meu nome é Ana."):
        mem.append_event(h.a.session_id, "user", "utterance", t)
    for i in range(3000):
        mem.append_event("ruido", "user", "ruido", f"sala corredor projeto equipe prova turma horário lanche r{i}")
    mem.flush(30)
    h.a.memory = mem
    cfg = tmp / "rag.local.json"
    cfg.write_text(json.dumps({"rag_ligado": rag}), encoding="utf-8")
    h.a.rag_config_path = cfg
    h.a.ask_text("aquecimento")
    h.idle(8)
    ts, itens = [], 0
    for q in ["Onde ficam os sensores de distância?", "Quando é a reunião do clube de robótica?", "Qual é minha comida favorita?"] * (n // 3):
        t0 = time.perf_counter()
        h.a.ask_text(q)
        h.idle(8)
        s = h.a.snapshot()
        ts.append(s.timings.get("primeiro_audio", 0) * 1000 + 0)
        itens = len([e for e in h.sent("conversation.item.create") if e["item"].get("id", "").startswith("rag_")])
    met = h.a.rag.metricas() if h.a.rag else {}
    h.close()
    mem.close()
    return dict(rag=rag, primeiro_audio_p50_ms=round(st.median(ts), 1), primeiro_audio_p95_ms=round(sorted(ts)[int(len(ts) * .95)], 1), itens_rag_enviados=itens,
                montagem_p50_ms=met.get("ms_total", {}).get("p50"), montagem_p95_ms=met.get("ms_total", {}).get("p95"),
                recuperacao_p50_ms=met.get("ms_recuperacao", {}).get("p50"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=12)
    a = ap.parse_args()
    out = [rodar(False, a.n), rodar(True, a.n)]
    print(json.dumps(out, ensure_ascii=False, indent=1))
    print("Servidor Realtime FALSO com latência fixa: a diferença é só o custo local do app (busca + um item a mais), não latência de API.")
