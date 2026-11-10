"""Linha de base da memória/contexto (Etapa 1 do projeto RAG): mede o que existe HOJE, sem API paga, sem rede e sem tocar em logs/memoria.db.

Cria um banco temporário, aplica o conjunto sintético `avaliacao/rag_conjunto_avaliacao.json` pelas mesmas funções públicas do app
(append_event, upsert_instruction, validate_memory, delete_memory) e mede:
  * comportamento: o que `get_session_context` (o que a IA recebe) e `search_memory` (busca local) devolvem por caso;
  * tamanho do contexto (caracteres; tokens só ESTIMADOS por caracteres/4) e nº de consultas SQL por montagem;
  * tempo de `get_session_context` e de `search_memory` (p50/p95), com e sem FTS, em bancos de tamanhos crescentes (--escala).
Uso:  python avaliar_baseline_rag.py [--escala 1000,10000] [--repeticoes 50] [--regressoes] [--saida arquivo.json]
`--regressoes` também roda os testes de interrupção/fila/ferramentas (pode levar alguns minutos).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics as st
import subprocess
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import memoria
from memoria import Memory, norm

DATASET = HERE / "avaliacao" / "rag_conjunto_avaliacao.json"
CANAIS = ("contexto", "busca_crua", "busca_palavras", "rag")        # "rag" = contexto.py com RAG ligado (Etapa 2)
SEARCH_LIMIT = 5                      # o mesmo limite padrão de search_memory


# ---------------------------------------------------------------------------------------------------------
# montagem do banco sintético
# ---------------------------------------------------------------------------------------------------------
def carregar(path: Path = DATASET) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _find_memory(mem: Memory, uid: str, contem: str):
    rows = mem._read("SELECT id, content FROM memories WHERE (owner_scope='user' AND owner_id=?) OR owner_scope='global'", (uid,))
    for r in rows:
        if norm(contem) in norm(r["content"]):
            return r["id"]
    return None


def aplicar(mem: Memory, passos: list) -> None:
    for p in passos:
        op, uid = p["op"], p.get("usuario", "")
        sess = f"sess_{uid}"
        if op == "fala":
            mem.append_event(sess, "user", "utterance", p["texto"], user_id=uid)
        elif op == "instrucao":
            mem.upsert_instruction(p["texto"], actor=p.get("ator", "user"), user_id=uid)
        elif op == "gerar_instrucoes":
            for i in range(int(p["n"])):
                mem.upsert_instruction(f"Regra sintética {i:02d}: ao falar de tópico{i:02d}, cite o exemplo{i:02d} e a observação extra{i:02d} com cuidado "
                                       f"e depois confirme o detalhe{i:02d}.", actor="user", user_id=uid)
        elif op in ("validar", "apagar_memoria"):
            mem.flush(5.0)                                             # a memória a achar pode estar ainda na fila
            ident = _find_memory(mem, uid, p["contem"])
            if ident is None:
                raise RuntimeError(f"passo {op}: memória '{p['contem']}' não encontrada para {uid}")
            if op == "validar":
                mem.validate_memory(ident, "revisor_sintetico", ok=True)
            else:
                mem.delete_memory(ident, user_id=uid)
        else:
            raise ValueError("op desconhecida: " + op)
    mem.flush(10.0)


_VOCAB = ("laboratório sala corredor projeto equipe prova turma horário lanche biblioteca quadra torneio kit placa cabo bateria ensaio "
          "relatório apresentação cartaz votação sorteio visita professora coordenação monitor oficina impressora folha caderno mochila "
          "recreio campeonato uniforme ônibus excursão ingresso fotografia mural exposição reunião sensor motor engrenagem programa").split()
_RUIDO = ("o", "a", "um", "uma", "sobre", "com", "para", "depois", "antes", "ontem", "hoje", "novo", "velho", "grande", "pequeno")


def gerar_ruido(mem: Memory, uid: str, n: int, seed: int = 7) -> None:
    """n eventos sintéticos (distratores realistas: reuniões, salas, sensores…) sem as respostas dos casos e sem gatilhos de aprendizado."""
    rnd = random.Random(seed)
    banidas = {"quinta", "março", "marco", "maio", "zeta", "girassol", "bidu", "senhor", "andar", "ultrassônico", "raioz"}
    for i in range(n):
        words = [rnd.choice(_VOCAB if rnd.random() < .6 else _RUIDO) for _ in range(rnd.randint(7, 16))]
        text = " ".join(w for w in words if norm(w) not in banidas) + f" r{i}."
        mem.append_event("sess_ruido_" + uid, "user", "ruido_sintetico", text, user_id=uid)
    mem.flush(120.0)


# ---------------------------------------------------------------------------------------------------------
# medição
# ---------------------------------------------------------------------------------------------------------
class Contador:
    """Conta consultas SQL (cada `_read` abre uma conexão) feitas durante um bloco."""
    def __init__(self, mem):
        self.mem, self.n, self._orig = mem, 0, mem._read

    def __enter__(self):
        def counted(sql, args=()):
            self.n += 1
            return self._orig(sql, args)
        self.mem._read = counted
        return self

    def __exit__(self, *a):
        self.mem._read = self._orig


def _pct(values, q):
    s = sorted(values)
    return s[min(len(s) - 1, int(len(s) * q))] if s else 0.0


def temporizar(fn, reps: int) -> dict:
    ts = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000)
    return dict(p50_ms=round(st.median(ts), 3), p95_ms=round(_pct(ts, .95), 3), max_ms=round(max(ts), 3), n=reps)


def texto_busca(res) -> str:
    return " | ".join(r["text"] for r in res)


def avaliar_canal(caso: dict, texto: str, n_resultados: int | None) -> dict:
    ok_contem = all(norm(x) in norm(texto) for x in caso.get("esperado_contem", []))
    vazamentos = [x for x in caso.get("esperado_nao_contem", []) if norm(x) in norm(texto)]
    ok = ok_contem and not vazamentos
    out = dict(ok=ok, contem_tudo=ok_contem, vazamentos=vazamentos)
    if caso.get("esperado_vazio"):
        n = n_resultados if n_resultados is not None else (1 if texto.strip() else 0)
        out.update(resultados_devolvidos=n, ok=ok and n == 0)
    return out


def rodar_caso(mem: Memory, caso: dict, asm=None) -> dict:
    uid = caso["usuario"]
    r = {}
    if asm is not None and "regressao" not in caso["canais"]:
        p = asm.montar(uid, f"sess_{uid}", caso["pergunta"])
        r["rag"] = avaliar_canal(caso, p.texto, len(p.fontes))
        r["rag"]["chars"] = len(p.texto)
        r["rag"]["fontes"] = p.fontes
    if "contexto" in caso["canais"]:
        ctx = mem.get_session_context(f"sess_{uid}", user_id=uid)
        r["contexto"] = avaliar_canal(caso, ctx, None)
        r["contexto"]["chars"] = len(ctx)
    for canal, q in (("busca_crua", caso["pergunta"]), ("busca_palavras", caso.get("consulta", caso["pergunta"]))):
        if canal in caso["canais"]:
            res = mem.search_memory(q, user_id=uid, limit=SEARCH_LIMIT)
            r[canal] = avaliar_canal(caso, texto_busca(res), len(res))
            r[canal]["resultados"] = len(res)
    return r


def medir_contexto(mem: Memory, dataset: dict, reps: int) -> dict:
    out = {}
    for uid in dataset["usuarios"]:
        sess = f"sess_{uid}"
        with Contador(mem) as c:
            ctx = mem.get_session_context(sess, user_id=uid)
        consultas = c.n
        with Contador(mem) as c2:
            mem.get_session_context(sess, user_id=uid, handoff=True)
        out[uid] = dict(chars=len(ctx), palavras=len(ctx.split()), tokens_estimados=round(len(ctx) / 4), truncado=ctx.endswith("…"),
                        limite_chars=1100, consultas_sql=consultas, consultas_sql_handoff=c2.n,
                        tempo=temporizar(lambda u=uid, s=sess: mem.get_session_context(s, user_id=u), reps))
    return out


def medir_busca(mem: Memory, dataset: dict, reps: int) -> dict:
    casos = [c for c in dataset["casos"] if any(k in c["canais"] for k in ("busca_crua", "busca_palavras"))]
    out = {}
    for canal, chave in (("busca_crua", "pergunta"), ("busca_palavras", "consulta")):
        ts = []
        for c in casos:
            if canal not in c["canais"]:
                continue
            q = c.get(chave, c["pergunta"])
            ts.append(temporizar(lambda q=q, u=c["usuario"]: mem.search_memory(q, user_id=u, limit=SEARCH_LIMIT), max(3, reps // 5)))
        out[canal] = dict(p50_ms=round(st.median([t["p50_ms"] for t in ts]), 3), p95_ms=round(max(t["p95_ms"] for t in ts), 3), consultas=len(ts))
    return out


def resumo_por_categoria(casos_res: dict, dataset: dict) -> dict:
    cats = {}
    for caso in dataset["casos"]:
        if caso["id"] not in casos_res:
            continue
        for canal, v in casos_res[caso["id"]].items():
            d = cats.setdefault(caso["categoria"], {}).setdefault(canal, dict(ok=0, total=0))
            d["total"] += 1
            d["ok"] += 1 if v["ok"] else 0
    return cats


def rodar_regressoes(dataset: dict) -> dict:
    arquivos = sorted({a for c in dataset["casos"] for a in c.get("regressoes", [])})
    py = HERE / ".venv" / "Scripts" / "python.exe"
    py = str(py) if py.exists() else sys.executable
    out = {}
    for a in arquivos:
        t0 = time.perf_counter()
        try:
            p = subprocess.run([py, str(HERE / a)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600, cwd=str(HERE),
                               env=dict(os.environ, PYTHONIOENCODING="utf-8"))
            linha = next((l for l in reversed(p.stdout.splitlines()) if "verificações OK" in l), "")
            out[a] = dict(codigo=p.returncode, resumo=linha.strip(), segundos=round(time.perf_counter() - t0, 1))
        except subprocess.TimeoutExpired:
            out[a] = dict(codigo=-1, resumo="tempo esgotado", segundos=600)
    return out


def executar(dataset: dict, escalas=(), reps: int = 50, regressoes: bool = False) -> dict:
    tmp = tempfile.mkdtemp(prefix="rag_baseline_")
    resultado = dict(dataset=dataset["rotulo"], versao=dataset["versao"], python=sys.version.split()[0], api_paga=False, rede=False,
                     banco_temporario=True, avisos=[], escalas={})
    mem = Memory(Path(tmp) / "base.db")
    if not mem.active:
        raise RuntimeError("banco temporário não abriu: " + mem.last_error)
    try:
        resultado["fts5"] = mem.fts
        resultado["banco"] = str(mem.path)
        aplicar(mem, dataset["passos"])
        import contexto as _ctx
        asm = _ctx.ContextAssembler(mem, _ctx.RagConfig(rag_ligado=True, prazo_busca_ms=2000))
        casos = {}
        for c in dataset["casos"]:
            if "regressao" in c["canais"]:
                continue
            casos[c["id"]] = rodar_caso(mem, c, asm)
        resultado["casos"] = casos
        resultado["por_categoria"] = resumo_por_categoria(casos, dataset)
        resultado["contexto"] = medir_contexto(mem, dataset, reps)
        resultado["busca"] = dict(com_fts=medir_busca(mem, dataset, reps))
        resultado["banco_bytes"] = mem.path.stat().st_size
        if mem.fts:                                                     # o caminho alternativo (LIKE) usado quando o SQLite não tem FTS5
            mem.fts = False
            resultado["busca"]["sem_fts_like"] = medir_busca(mem, dataset, reps)
            mem.fts = True
        for n in escalas:
            gerar_ruido(mem, "sint_ana", int(n))
            casos_n = {c["id"]: rodar_caso(mem, c, asm) for c in dataset["casos"] if "regressao" not in c["canais"] and c["usuario"] == "sint_ana"}
            ok_antes = sum(1 for cid in casos_n for v in casos[cid].values() if v["ok"])
            ok_depois = sum(1 for cid in casos_n for v in casos_n[cid].values() if v["ok"])
            ana_ctx = temporizar(lambda: mem.get_session_context("sess_sint_ana", user_id="sint_ana"), max(5, reps // 5))
            resultado["escalas"][str(n)] = dict(banco_bytes=mem.path.stat().st_size, busca=medir_busca(mem, dataset, reps),
                                                 contexto_ana=ana_ctx, canais_ok_antes=ok_antes, canais_ok_depois=ok_depois,
                                                 total_canais=sum(len(v) for v in casos_n.values()))
        resultado["queries_por_contexto"] = {u: v["consultas_sql"] for u, v in resultado["contexto"].items()}
        if regressoes:
            resultado["regressoes"] = rodar_regressoes(dataset)
        else:
            resultado["avisos"].append("casos de interrupção (C21–C23) são regressões: use --regressoes para executá-los aqui")
        resultado["avisos"] += [
            "tokens são ESTIMADOS (caracteres/4); tokens reais só no logs/uso_api.jsonl de sessões reais",
            "tempo até o primeiro áudio NÃO é medido aqui (exige API real); aqui só o custo local da busca/montagem",
            "o 'contexto' avalia o que a IA recebe no começo da sessão; a busca local ainda NÃO é chamada pelo caminho de voz",
        ]
    finally:
        try:
            asm.fechar()
        except NameError:
            pass
        mem.close()
    return resultado


def imprimir(res: dict) -> None:
    print(f"Conjunto: {res['dataset']}")
    print(f"FTS5: {res['fts5']} · banco {res['banco_bytes'] / 1024:.0f} KB · Python {res['python']} · API paga: não · rede: não\n")
    print("Casos (OK/total por canal):")
    for cat, canais in res["por_categoria"].items():
        print(f"  {cat:22} " + "  ".join(f"{c}: {v['ok']}/{v['total']}" for c, v in canais.items()))
    print("\nFalhas e vazamentos:")
    for cid, canais in res["casos"].items():
        for canal, v in canais.items():
            if not v["ok"]:
                extra = f" vazou {v['vazamentos']}" if v.get("vazamentos") else ""
                extra += f" devolveu {v['resultados_devolvidos']} resultado(s) sem haver resposta" if v.get("resultados_devolvidos") else ""
                extra += " não achou o esperado" if not v["contem_tudo"] else ""
                print(f"  {cid} {canal}:{extra}")
    print("\nContexto que a IA recebe (início de sessão):")
    for u, v in res["contexto"].items():
        print(f"  {u:11} {v['chars']:4} caracteres (~{v['tokens_estimados']} tokens est.){' TRUNCADO' if v['truncado'] else ''} · "
              f"{v['consultas_sql']} consultas SQL (+{v['consultas_sql_handoff'] - v['consultas_sql']} no handoff) · p50 {v['tempo']['p50_ms']} ms p95 {v['tempo']['p95_ms']} ms")
    print("\nBusca local (p50/p95 ms): " + " · ".join(f"{k} {v['busca_crua']['p50_ms']}/{v['busca_crua']['p95_ms']} (crua) {v['busca_palavras']['p50_ms']}/{v['busca_palavras']['p95_ms']} (palavras)"
                                                       for k, v in res["busca"].items()))
    for n, e in res["escalas"].items():
        print(f"  com +{n} eventos de ruído: banco {e['banco_bytes'] / 1e6:.1f} MB · busca crua p50 {e['busca']['busca_crua']['p50_ms']} ms p95 {e['busca']['busca_crua']['p95_ms']} ms · "
              f"contexto p50 {e['contexto_ana']['p50_ms']} ms · canais OK {e['canais_ok_antes']}→{e['canais_ok_depois']} de {e['total_canais']}")
    if res.get("regressoes"):
        print("\nRegressões obrigatórias:")
        for a, v in res["regressoes"].items():
            print(f"  {a}: código {v['codigo']} · {v['resumo']} ({v['segundos']} s)")
    print("\nAvisos:\n  - " + "\n  - ".join(res["avisos"]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--escala", default="", help="eventos de ruído a acrescentar, ex.: 1000,10000")
    ap.add_argument("--repeticoes", type=int, default=50)
    ap.add_argument("--regressoes", action="store_true")
    ap.add_argument("--saida", default=str(HERE / "avaliacao" / "baseline_ultimo.json"))
    args = ap.parse_args(argv)
    escalas = [int(x) for x in args.escala.split(",") if x.strip()]
    res = executar(carregar(), escalas, args.repeticoes, args.regressoes)
    Path(args.saida).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    imprimir(res)
    print(f"\n(JSON em {args.saida})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
