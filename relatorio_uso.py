"""Resumo dos tokens por resposta a partir de logs/uso_api.jsonl (só números, nunca texto/áudio/chaves).

Uso:  python relatorio_uso.py                       -> resumo do arquivo padrão
      python relatorio_uso.py antes.jsonl depois.jsonl   -> compara duas rodadas (sem concluir nada com amostra pequena)
Não chama API nem rede: só lê o arquivo que o próprio app grava.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List

DEFAULT = Path(__file__).resolve().parent / "logs" / "uso_api.jsonl"
MIN_SAMPLE = 10          # abaixo disso a comparação é só ilustrativa


def _pct(values: List[float], q: float) -> float:
    s = sorted(values)
    return float(s[min(len(s) - 1, int(len(s) * q))]) if s else 0.0


def load(path) -> List[dict]:
    rows = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and row.get("status", "completed") == "completed":
                    rows.append(row)
    except OSError:
        pass
    return rows


def _num(row: dict, *path) -> float:
    cur = row
    for key in path:
        cur = cur.get(key) if isinstance(cur, dict) else None
    return float(cur) if isinstance(cur, (int, float)) and not isinstance(cur, bool) else 0.0


def summarize(rows: List[dict]) -> Dict[str, float]:
    """Por resposta: entrada, saída (texto/áudio), imagem e fração de entrada vinda do cache."""
    inp = [_num(r, "input_tokens") for r in rows]
    out = [_num(r, "output_tokens") for r in rows]
    cached = sum(_num(r, "input_token_details", "cached_tokens") for r in rows)
    total_in = sum(inp)
    n = len(rows)
    return dict(respostas=n,
                entrada_media=sum(inp) / n if n else 0.0, entrada_p95=_pct(inp, .95),
                saida_media=sum(out) / n if n else 0.0, saida_p50=_pct(out, .5), saida_p95=_pct(out, .95),
                saida_audio_media=sum(_num(r, "output_token_details", "audio_tokens") for r in rows) / n if n else 0.0,
                saida_texto_media=sum(_num(r, "output_token_details", "text_tokens") for r in rows) / n if n else 0.0,
                imagem_media=sum(_num(r, "input_token_details", "image_tokens") for r in rows) / n if n else 0.0,
                cache_fracao=cached / total_in if total_in else 0.0)


def compare(a: List[dict], b: List[dict]) -> dict:
    sa, sb = summarize(a), summarize(b)
    delta = {k: sb[k] - sa[k] for k in sa if k != "respostas"}
    return dict(antes=sa, depois=sb, delta=delta, amostra_suficiente=min(len(a), len(b)) >= MIN_SAMPLE)


def _fmt(s: Dict[str, float]) -> str:
    return (f"{int(s['respostas'])} respostas · entrada média {s['entrada_media']:.0f} (p95 {s['entrada_p95']:.0f}) · "
            f"saída média {s['saida_media']:.0f} (p50 {s['saida_p50']:.0f}, p95 {s['saida_p95']:.0f}; áudio {s['saida_audio_media']:.0f}, "
            f"texto {s['saida_texto_media']:.0f}) · imagem {s['imagem_media']:.0f} · cache {100 * s['cache_fracao']:.0f} %")


if __name__ == "__main__":
    args = [Path(a) for a in sys.argv[1:]] or [DEFAULT]
    if len(args) == 1:
        rows = load(args[0])
        print(_fmt(summarize(rows)) if rows else "Sem dados em " + str(args[0]))
    else:
        res = compare(load(args[0]), load(args[1]))
        print("antes :", _fmt(res["antes"]))
        print("depois:", _fmt(res["depois"]))
        print("variação (depois − antes):", {k: round(v, 1) for k, v in res["delta"].items()})
        if not res["amostra_suficiente"]:
            print(f"ATENÇÃO: menos de {MIN_SAMPLE} respostas em uma das rodadas; não conclua economia com isto.")
