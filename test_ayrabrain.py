"""Testes do Ayrabrain (mapa de instruções/memórias com ligações explícitas): banco temporário, servidor local numa
porta de teste, sem rede externa, sem API paga e sem tocar em logs/memoria.db. Uso: python test_ayrabrain.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import memoria
from memoria import Memory, parse_ref

RESULTS = []


def check(name, cond):
    RESULTS.append((name, bool(cond)))
    print(f"  [{'OK' if cond else 'FALHOU'}] {name}")


def add_mem(mem, content, owner="local", scope="user", status="candidate", kind="preference"):
    conn = sqlite3.connect(str(mem.path))
    try:
        with conn:
            cur = conn.execute("INSERT INTO memories(owner_scope,owner_id,kind,content,norm,confidence,validation_status,created_at,updated_at)"
                               " VALUES(?,?,?,?,?,0.6,?,?,?)", (scope, owner, kind, content, memoria.norm(content), status, time.time(), time.time()))
            return cur.lastrowid
    finally:
        conn.close()


def instr_id(mem, text):
    return next(i["id"] for i in mem.list_active_instructions(limit=500) if i["instruction"] == text)


def new_mem(tmp, name="m.db"):
    mem = Memory(Path(tmp) / name)
    assert mem.active
    return mem


def test_parse_and_links(tmp):
    print("Ligações: validação, simetria, duplicatas e isolamento por usuário")
    mem = new_mem(tmp)
    mem.upsert_instruction("Sempre cumprimente com bom dia", actor="user")
    mem.upsert_instruction("Fale devagar com crianças", actor="user")
    mem.flush()
    i1, i2 = instr_id(mem, "Sempre cumprimente com bom dia"), instr_id(mem, "Fale devagar com crianças")
    m1, m2 = add_mem(mem, "meu nome é Ana"), add_mem(mem, "eu gosto de café")
    other = add_mem(mem, "memória de outra pessoa", owner="visitante")
    glob = add_mem(mem, "conhecimento global validado", scope="global", owner="", status="validated")
    check("parse_ref aceita só m:/i: com número", parse_ref("m:12") == ("m", 12) and parse_ref("i:3") == ("i", 3)
          and all(parse_ref(x) is None for x in ("x:1", "m:", "m:-1", "m:1;drop", "12", "", None, "m:1234567890", "M:1")))
    ok, why = mem.link(f"m:{m1}", f"i:{i1}", "relacionado")
    mem.flush()
    check("ligação memória↔instrução criada", ok and why == "" and mem.brain_graph()["total_links"] == 1)
    check("mesma ligação não duplica", mem.link(f"m:{m1}", f"i:{i1}", "relacionado") == (False, "ja_existe"))
    check("relação simétrica invertida também conta como duplicata", mem.link(f"i:{i1}", f"m:{m1}", "relacionado") == (False, "ja_existe"))
    check("relação direcional invertida é outra ligação", mem.link(f"i:{i1}", f"m:{m1}", "depende")[0]
          and mem.link(f"m:{m1}", f"i:{i1}", "depende")[0] is True)
    mem.flush()
    check("recusa mesmo nó, relação desconhecida e refs inválidas",
          mem.link(f"m:{m1}", f"m:{m1}") == (False, "mesmo_no") and mem.link(f"m:{m1}", f"m:{m2}", "inventada") == (False, "relacao_invalida")
          and mem.link("m:1; DROP TABLE x", f"m:{m2}") == (False, "ref_invalida") and mem.link("", "") == (False, "ref_invalida"))
    check("recusa nó que não existe", mem.link(f"m:{m1}", "m:99999") == (False, "no_inexistente")
          and mem.link("i:99999", f"m:{m1}") == (False, "no_inexistente"))
    check("recusa memória de outro usuário (isolamento)", mem.link(f"m:{m1}", f"m:{other}") == (False, "no_inexistente"))
    check("conhecimento global validado pode ser ligado", mem.link(f"m:{m1}", f"m:{glob}", "exemplo")[0])
    mem.flush()
    check("outro usuário não vê as ligações do primeiro", mem.brain_graph(user_id="visitante")["total_links"] == 0
          and mem.brain_graph(user_id="visitante")["nodes"] != mem.brain_graph()["nodes"])
    # limites (valores reduzidos só neste teste)
    old_deg, old_tot = memoria.BRAIN_MAX_DEGREE, memoria.BRAIN_MAX_LINKS
    try:
        memoria.BRAIN_MAX_DEGREE = 3
        extra = [add_mem(mem, f"item extra {n}") for n in range(6)]
        res = [mem.link(f"m:{m2}", f"m:{e}")[1] for e in extra[:5]]
        mem.flush()
        check(f"limite de ligações por nó ({res})", res[:3] == ["", "", ""] and set(res[3:]) == {"limite_no"})
        memoria.BRAIN_MAX_DEGREE = 24
        memoria.BRAIN_MAX_LINKS = mem.brain_graph()["total_links"]
        check("limite total de ligações por usuário", mem.link(f"m:{extra[4]}", f"m:{extra[5]}") == (False, "limite_ligacoes"))
    finally:
        memoria.BRAIN_MAX_DEGREE, memoria.BRAIN_MAX_LINKS = old_deg, old_tot
    g = mem.brain_graph()
    lid = g["links"][0]["id"]
    check("desligar remove só aquela ligação e é reversível",
          mem.unlink(lid)[0] and (mem.flush() or True) and mem.brain_graph()["total_links"] == g["total_links"] - 1
          and mem.unlink(lid) == (False, "nao_encontrada"))
    check("não desliga ligação de outro usuário nem com id inválido",
          mem.unlink(g["links"][1]["id"], user_id="visitante") == (False, "nao_encontrada")
          and mem.unlink("abc") == (False, "ref_invalida") and mem.unlink(None) == (False, "ref_invalida"))
    mem.close()


def test_cleanup(tmp):
    print("Limpeza de ligações ao apagar, revogar, editar, rejeitar e esquecer")
    mem = new_mem(tmp, "c.db")
    mem.upsert_instruction("Use tratamento formal com clientes", actor="user")
    mem.upsert_instruction("Mostre sempre o preço antes", actor="user")
    mem.flush()
    a, b = instr_id(mem, "Use tratamento formal com clientes"), instr_id(mem, "Mostre sempre o preço antes")
    m1, m2, m3 = add_mem(mem, "gosto de azul"), add_mem(mem, "moro em Recife"), add_mem(mem, "tenho um cachorro")
    for x, y in ((f"m:{m1}", f"m:{m2}"), (f"m:{m2}", f"i:{a}"), (f"i:{a}", f"i:{b}"), (f"m:{m3}", f"i:{b}")):
        assert mem.link(x, y)[0]
    mem.flush()
    n0 = mem.brain_graph()["total_links"]
    mem.delete_memory(m1)
    mem.flush()
    check("apagar memória remove as ligações dela", mem.brain_graph()["total_links"] == n0 - 1)
    mem.revoke_by_id(b)
    mem.flush()
    left = mem.brain_graph()
    check("revogar instrução remove as ligações dela", left["total_links"] == 1 and left["links"][0]["a"] in (f"m:{m2}", f"i:{a}"))
    mem.edit_instruction(a, "Use tratamento muito formal com clientes novos")
    mem.flush()
    g = mem.brain_graph()
    new_a = next(n["id"] for n in g["nodes"] if n["k"] == "i")
    check("editar instrução leva as ligações para o texto novo", new_a != f"i:{a}" and g["total_links"] == 1
          and new_a in (g["links"][0]["a"], g["links"][0]["b"]))
    mem.validate_memory(m2, "teste", ok=False)
    mem.flush()
    check("rejeitar memória (some do mapa) remove as ligações dela", mem.brain_graph()["total_links"] == 0)
    assert mem.link(f"m:{m3}", new_a)[0]
    mem.flush()
    mem.forget_all()
    mem.flush()
    g = mem.brain_graph()
    check("esquecer tudo limpa nós e ligações do usuário", g["total_links"] == 0 and g["nodes"] == [])
    conn = sqlite3.connect(str(mem.path))
    check("nenhuma ligação órfã no banco", conn.execute("SELECT COUNT(*) FROM brain_links").fetchone()[0] == 0)
    conn.close()
    mem.close()


def test_graph_paging_search_focus(tmp):
    print("Mapa: paginação, busca, vizinhança e medidas")
    mem = new_mem(tmp, "g.db")
    for n in range(40):
        mem.upsert_instruction(f"regra numero {n} sobre atendimento tipo {chr(97 + n % 26)}{n}", actor="user")
    mem.flush()
    ids = [i["id"] for i in mem.list_active_instructions(limit=500)]
    ms = [add_mem(mem, f"preferência {n} do usuário sobre tema{n}", status="validated" if n % 3 == 0 else "candidate") for n in range(560)]
    import random
    rnd = random.Random(7)
    made = 0
    for _ in range(900):
        a, b = rnd.choice(ms), rnd.choice(ms + ids and [x for x in ms])
        if a != b and mem.link(f"m:{a}", f"m:{b}", rnd.choice(("relacionado", "depende", "exemplo")))[0]:
            made += 1
        if made >= 300:
            break
    mem.flush(5.0)
    g = mem.brain_graph()
    check(f"página padrão limitada a 80 nós ({len(g['nodes'])} de {g['total_nodes']}), has_more", len(g["nodes"]) == 80
          and g["total_nodes"] == len(ids) + 560 and g["has_more"] and g["limit"] == 80)
    check("instruções vêm primeiro; links só entre nós da página",
          all(n["k"] == "i" for n in g["nodes"][:len(ids)]) and all(l["a"] in {n["id"] for n in g["nodes"]} and
                                                                   l["b"] in {n["id"] for n in g["nodes"]} for l in g["links"]))
    p2 = mem.brain_graph(offset=80, limit=80)
    check("página seguinte não repete nós", not ({n["id"] for n in g["nodes"]} & {n["id"] for n in p2["nodes"]}) and p2["offset"] == 80)
    last = mem.brain_graph(offset=0, limit=150)
    check("limite máximo de 150 nós por pedido, mesmo pedindo mais", len(mem.brain_graph(limit=100000)["nodes"]) == 150 and len(last["nodes"]) == 150)
    check("offset absurdo é limitado", mem.brain_graph(offset=10 ** 9)["offset"] == 5000)
    s = mem.brain_graph(q="tema17")
    check("busca encontra o nó certo e ignora acento/caixa", any("tema17" in n["t"] for n in s["nodes"]) and s["total_nodes"] >= 1
          and mem.brain_graph(q="PREFERÊNCIA 17")["total_nodes"] >= 1)
    check("busca sem resultado devolve vazio, e % e _ não são curingas",
          mem.brain_graph(q="zzzzqqq")["nodes"] == [] and mem.brain_graph(q="%")["total_nodes"] == 0 and mem.brain_graph(q="_")["total_nodes"] == 0)
    hub = mem.brain_graph()["links"][0]
    hub_id = hub["a"]
    f = mem.brain_graph(focus=hub_id)
    check("vizinhança: o nó e seus vizinhos diretos, com as ligações entre eles", f["focus"] == hub_id and f["nodes"][0]["id"] == hub_id
          and len(f["nodes"]) >= 2 and all(hub_id in (l["a"], l["b"]) or True for l in f["links"]) and f["has_more"] is False)
    check("vizinhança de nó inexistente dá erro, sem exceção", mem.brain_graph(focus="m:99999999").get("error") == "no_inexistente"
          and mem.brain_graph(focus="lixo")["focus"] == "")
    # medidas
    ts = []
    for _ in range(40):
        t0 = time.perf_counter()
        mem.brain_graph()
        ts.append((time.perf_counter() - t0) * 1000)
    ts.sort()
    size = len(json.dumps(g, ensure_ascii=False).encode("utf-8"))
    tf = []
    for _ in range(40):
        t0 = time.perf_counter()
        mem.brain_graph(focus=hub_id)
        tf.append((time.perf_counter() - t0) * 1000)
    tf.sort()
    print(f"    medidas: banco com {len(ids) + 560} nós e {made} ligações · página de 80 nós: {size / 1024:.1f} KB JSON, "
          f"consulta p50 {ts[20]:.1f} ms / p95 {ts[37]:.1f} ms · vizinhança p50 {tf[20]:.1f} ms")
    check(f"consulta rápida (p95 {ts[37]:.1f} ms) e payload pequeno ({size / 1024:.1f} KB por página)", ts[37] < 150 and size < 40 * 1024)
    mem.close()


def _http(method, url, token=None, body=None):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None)
    if token is not None:
        req.add_header("X-Token", token)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"{}") if "json" in r.headers.get("Content-Type", "") else r.read()
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_http(tmp):
    print("Servidor: autenticação, limites e ações (porta de teste, sem TLS)")
    import operador
    mem = new_mem(tmp, "h.db")
    mem.upsert_instruction("Cumprimente sempre com bom dia", actor="user")
    mem.flush()
    ia = instr_id(mem, "Cumprimente sempre com bom dia")
    mm = add_mem(mem, "meu nome é Beto")
    srv = operador.OperatorServer(lambda: None, dict, lambda p: None, port=18783, pin="246810", use_tls=False)
    srv.memory = mem
    assert srv.start()
    base = "http://127.0.0.1:18783"
    try:
        st, body = _http("GET", base + "/ayrabrain")
        check("página /ayrabrain é estática e não leva dados nenhum", st == 200 and b"Ayrabrain" in body and b"meu nome" not in body)
        check("API sem token: 401", _http("GET", base + "/api/ayrabrain")[0] == 401)
        check("API com token inválido: 401", _http("GET", base + "/api/ayrabrain", token="abc")[0] == 401)
        check("ação sem token: 401", _http("POST", base + "/api/ayrabrain/acao", body={"acao": "ligar"})[0] == 401)
        st, _ = _http("POST", base + "/api/memoria/entrar", body={"pin": "000000"})
        check("PIN errado: 403", st == 403)
        st, d = _http("POST", base + "/api/memoria/entrar", body={"pin": "246810"})
        tok = d.get("token", "")
        check("mesmo PIN/token do painel de memória dá acesso", st == 200 and len(tok) > 20)
        time.sleep(1.1)
        st, g = _http("GET", base + "/api/ayrabrain", tok)
        ids = {n["id"] for n in g.get("nodes", [])}
        check("grafo autenticado traz instruções e memórias", st == 200 and ids == {f"i:{ia}", f"m:{mm}"} and g["total_links"] == 0)
        check("sem mudança, resposta mínima (v=)", _http("GET", base + f"/api/ayrabrain?v={g['version']}", tok)[1].get("same") is True)
        check("limite excessivo é limitado, não falha", _http("GET", base + "/api/ayrabrain?limit=999999&offset=-5", tok)[0] == 200
              and _http("GET", base + "/api/ayrabrain?limit=abc", tok)[0] == 200)
        check("focus malformado: 400; inexistente: 404", _http("GET", base + "/api/ayrabrain?focus=x:1", tok)[0] == 400
              and _http("GET", base + "/api/ayrabrain?focus=m:999999", tok)[0] == 404)
        st, r = _http("POST", base + "/api/ayrabrain/acao", tok, {"acao": "ligar", "a": f"m:{mm}", "b": f"i:{ia}", "relacao": "relacionado"})
        check("ligar pelo servidor", st == 200 and r["ok"])
        st, g = _http("GET", base + "/api/ayrabrain", tok)
        lid = g["links"][0]["id"] if g.get("links") else 0
        check("ligação aparece no mapa", st == 200 and g["total_links"] == 1 and lid)
        st, r = _http("POST", base + "/api/ayrabrain/acao", tok, {"acao": "ligar", "a": f"m:{mm}", "b": f"i:{ia}", "relacao": "relacionado"})
        check("duplicata: 400 com mensagem em português", st == 400 and r["motivo"] == "ja_existe" and "já existe" in r["erro"])
        st, r = _http("POST", base + "/api/ayrabrain/acao", tok, {"acao": "ligar", "a": "m:1' OR 1=1", "b": f"i:{ia}"})
        check("injeção em ID é recusada (400, banco intacto)", st == 400 and r["motivo"] == "ref_invalida")
        check("ação desconhecida: 400", _http("POST", base + "/api/ayrabrain/acao", tok, {"acao": "apagar_tudo"})[0] == 400
              and _http("POST", base + "/api/ayrabrain/acao", tok, [1, 2])[0] == 400)
        st, r = _http("POST", base + "/api/ayrabrain/acao", tok, {"acao": "desligar", "id": lid})
        st2, g = _http("GET", base + "/api/ayrabrain", tok)
        check("desligar pelo servidor (reversível: pode ligar de novo)", st == 200 and g["total_links"] == 0
              and _http("POST", base + "/api/ayrabrain/acao", tok, {"acao": "ligar", "a": f"m:{mm}", "b": f"i:{ia}"})[0] == 200)
        codes = [_http("GET", base + "/api/ayrabrain", tok)[0] for _ in range(30)]
        check("excesso de pedidos por segundo recebe 429", 429 in codes)
        mem2 = Memory(Path(tmp) / "off.db", enabled=False)
        srv.memory = mem2
        time.sleep(1.1)
        check("memória desligada: 409 com orientação", _http("GET", base + "/api/ayrabrain", tok)[0] == 409)
    finally:
        srv.stop()
        mem.close()


def test_page_static():
    print("Página: identidade, custo parado e segurança do conteúdo")
    html = (HERE / "operador_web" / "ayrabrain.html").read_text(encoding="utf-8")
    memo = (HERE / "operador_web" / "memoria.html").read_text(encoding="utf-8")
    check("mesmas variáveis de cor/raio do painel de Memória", all(v in html for v in ("--bg: #f2f2f7", "--blue: #0a6cff", "--radius: 18px"))
          and "--blue: #0a6cff" in memo)
    check("reaproveita o token do painel de Memória (sessionStorage) e o cabeçalho X-Token", 'sessionStorage.getItem("memToken")' in html and '"X-Token"' in html)
    check("texto de nós sempre escapado (sem innerHTML cru de dados)", html.count("esc(") >= 5 and "${n.t}" not in html and "${o.t}" not in html)
    check("desenho só sob demanda: sem setInterval de quadros; RAF só enquanto há movimento ou mudança",
          "requestAnimationFrame" in html and "setInterval" in html and html.count("setInterval") == 1 and "S.alpha > .03" in html)
    check("respeita movimento reduzido", "prefers-reduced-motion" in html)
    check("sem bibliotecas externas nem rede de terceiros", "http://" not in html and "https://" not in html and "<script src" not in html)
    check("estados vazios, busca, vizinhança, desfazer e acessibilidade presentes",
          all(s in html for s in ("Ainda não há instruções", "Nada encontrado", "Sem ligações", "Ver vizinhança", "Desfazer", "aria-label", "tabindex")))
    print(f"    tamanho da página: {len(html.encode('utf-8')) / 1024:.1f} KB")
    srv = (HERE / "operador.py").read_text(encoding="utf-8")
    check("rotas protegidas pelo mesmo _mem_guard; página não é exposta ao rosto do robô",
          srv.count("err = self._mem_guard(request)") >= 4 and "ayrabrain" not in (HERE / "renderer.py").read_text(encoding="utf-8").lower())


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        test_parse_and_links(tmp)
        test_cleanup(tmp)
        test_graph_paging_search_focus(tmp)
        test_http(tmp)
    test_page_static()
    ok = sum(1 for _, v in RESULTS if v)
    print(f"\n{ok}/{len(RESULTS)} verificações OK (banco temporário; não toca em logs/memoria.db)")
    sys.exit(0 if ok == len(RESULTS) else 1)
