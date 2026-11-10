"""Guarda da linha de base do aceite PM (C01–C20) contra o fluxo atual: isolamento nunca pode falhar; nenhum veredito pode piorar.
Sintético, banco temporário, sem rede nem API paga. Uso: python test_aceite_pm.py"""
import os
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import avaliar_aceite_pm as P

RESULTS = []
RANK = {"ok": 3, "parcial": 2, "lacuna": 1}
# veredito de HOJE (2026-11-01, depois da Etapa 3). Melhorar é bem-vindo (só avisa); piorar falha.
HOJE = {"C01": "ok", "C02": "ok", "C03": "ok", "C04": "ok", "C05": "ok", "C06": "ok", "C07": "ok", "C08": "ok",
        "C09": "ok", "C10": "pendente_api", "C11": "ok", "C12": "ok", "C13": "nao_aplicavel_hoje", "C14": "ok",
        "C15": "ok", "C16": "nao_aplicavel_hoje", "C17": "nao_aplicavel_hoje", "C18": "ok", "C19": "ok", "C20": "ok"}


def check(n, c):
    RESULTS.append((n, bool(c)))
    print(f"  [{'OK' if c else 'FALHOU'}] {n}")


res = P.executar()
casos = res["casos"]
check("20 casos do PM presentes, todos com evidência classificada e campos de registro", sorted(casos) == sorted(HOJE)
      and all({"estado_inicial", "eventos_ordem", "fontes_selecionadas", "fontes_consumidas", "saida", "motivo_fallback", "veredito", "evidencia"} <= set(c) for c in casos.values()))
check("sem API paga/rede e banco temporário; configuração dos ensaios registrada", res["api_paga"] is False and res["rede"] is False
      and res["banco"] == "temporário" and res["configuracao"]["limite_contexto_chars"] == 1100)
check("ISOLAMENTO: nenhuma falha (bloqueia avanço se falhar)", not res["falhas_de_isolamento"] and not res["bloqueio_isolamento"])
# transição para status fora da escala (pendente/não aplicável/desconhecido) a partir de um status medido também conta como piora
pior = [(k, HOJE[k], casos[k]["veredito"]) for k in HOJE if (HOJE[k] in RANK and (casos[k]["veredito"] not in RANK or RANK[casos[k]["veredito"]] < RANK[HOJE[k]]))
        or (HOJE[k] not in RANK and casos[k]["veredito"] != HOJE[k])]
check(f"nenhum veredito piorou ({pior or 'ok'})", not pior)
melhor = [(k, HOJE[k], casos[k]["veredito"]) for k in HOJE if HOJE[k] in RANK and casos[k]["veredito"] in RANK and RANK[casos[k]["veredito"]] > RANK[HOJE[k]]]
if melhor:
    print(f"    AVISO: melhoraram (atualize HOJE): {melhor}")
check("C11: o orçamento é configurável de verdade (orçamento menor é respeitado) e sem dados de B", casos["C11"]["configuravel"] and casos["C11"]["veredito"] == "ok")
check("os impedimentos de aceite do PM estão fechados no caminho novo: busca/handoff sem revogados, ordem maliciosa não é regra, identidade vazia falha fechado",
      not casos["C06"]["superficies"]["historico_resumo"] and not casos["C06"]["superficies"]["busca"] and casos["C09"]["veredito"] == "ok" and casos["C20"]["assembler_falha_fechado"])
check("pergunta de qualidade de linguagem não é declarada validada (C10 pendente_api)", casos["C10"]["veredito"] == "pendente_api")
check("Etapa 2 confirmada: revogado/excluído fora da busca; ordem maliciosa não vira regra; contexto.py falha fechado sem identidade (o caminho antigo ainda herda o padrão)",
      not casos["C06"]["superficies"]["busca"] and not casos["C07"]["superficies"]["busca"] and casos["C20"]["assembler_falha_fechado"] and casos["C20"]["herdou_usuario_padrao"]
      and casos["C09"]["veredito"] == "ok")
ok = sum(1 for _, v in RESULTS if v)
print(f"\n{ok}/{len(RESULTS)} verificações OK (sintético; sem rede nem API paga)")
sys.exit(0 if ok == len(RESULTS) else 1)
