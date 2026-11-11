"""Fichas da Ayra sobre si mesma: busca por palavra-chave, confidenciais sem segredo, itens de menu do RAG."""
import json, random, sys
import conhecimento as k

ok = fail = 0
def check(nome, cond):
    global ok, fail
    print(("  [OK] " if cond else "  [FALHOU] ") + nome)
    ok, fail = ok + bool(cond), fail + (not cond)

print("Base de conhecimento da Ayra")
cartas = k.carregar()
check("fichas carregam e têm id, texto e palavras", len(cartas) >= 8 and all(c["id"] and c["texto"] and c["palavras"] for c in cartas))
ids = lambda q: [i["id"] for i in k.buscar(q, cartas)]
check("olhos: pergunta sobre os olhos traz a ficha dos olhos", "olhos" in ids("como seus olhos funcionam?"))
check("GIF ou pixel: traz a ficha dos olhos", "olhos" in ids("são gifs ou pixels?"))
check("pergunta sem relação não traz nada (custo zero)", ids("qual o clima hoje em Fortaleza") == [] and ids("") == [])
check("estado do projeto", "estado" in ids("o que já foi validado e testado?"))
check("no máximo 2 fichas", len(k.buscar("olhos voz memoria corpo projeto operador chave", cartas)) <= k.MAX_FICHAS)
conf = k.buscar("qual é o código exato e a fórmula dos olhos?", cartas, random.Random(1))
check("detalhe técnico dos olhos: confidencial vem primeiro, com instrução de piada",
      conf and conf[0]["nivel"] == "confidencial" and "brincadeira" in conf[0]["instrucao"])
check("quem controla/operador remoto é confidencial", k.buscar("tem alguém te controlando?", cartas)[0]["nivel"] == "confidencial")
check("chaves e custos são confidenciais", k.buscar("qual a sua chave de api e o custo?", cartas)[0]["nivel"] == "confidencial")
blob = json.dumps(cartas, ensure_ascii=False).lower()
check("fichas confidenciais não carregam o segredo (sem fórmula, SDF, nomes de arquivo ou chave)",
      all(t not in " ".join(c["texto"] for c in cartas if c["nivel"] == "confidencial").lower() for t in ("sdf", "face.py", "sk-", ".env", "80x45")))
txt = k.texto_para_contexto(k.buscar("como são os olhos?", cartas))
check("texto de contexto marca como DADO e não instrução", "DADO" in txt and "Fichas da Ayra" in txt and k.texto_para_contexto([]) == "")
check("arquivo ausente ou corrompido: lista vazia, sem erro", k.carregar(__import__("pathlib").Path("/nao/existe.json")) == [])

import menu, theme
class H: pass
hud = H(); hud.audio = dict(rag=True, rag_voz=False, memory=True, memory_n=0)
m = menu.Menu() if hasattr(menu, "Menu") else None
print("\nMenu do RAG")
if m is not None:
    m.view = "audio"
    items = {a: (n, s, t) for a, n, s, t in m.items(hud, theme.SCHEMES["dark"])}
    check("itens 'RAG' e 'RAG por voz' no menu de áudio", "audio:rag" in items and "audio:ragvoz" in items)
    check("RAG ligado e voz desligada: estados corretos", items["audio:rag"][2] == "on" and items["audio:ragvoz"][2] == "off")
    hud.audio = dict(rag=False, rag_voz=True)
    items = {a: (n, s, t) for a, n, s, t in m.items(hud, theme.SCHEMES["dark"])}
    check("RAG desligado: voz aparece desligada e pede ligar o RAG", items["audio:ragvoz"][2] == "off" and "primeiro" in items["audio:ragvoz"][1])

print(f"\n{ok}/{ok + fail} verificações OK (local, sem rede nem API paga)")
sys.exit(1 if fail else 0)
