// Teste da reconexão do painel (Node, sem navegador e sem rede): queda simulada, backoff, retomada sem perder comandos críticos.
const assert = require("assert");
const R = require("./operador_web/reconect.js");
let t = 0, timers = [], sent = [], connects = 0, plan = [], closed = 0;
const run = (ms) => { const end = t + ms; for (;;) { timers.sort((a, b) => a.at - b.at); const n = timers.find((x) => x.at <= end); if (!n) break; timers = timers.filter((x) => x !== n); t = n.at; n.fn(); } t = end; };
const tick = () => new Promise((r) => setImmediate(r));
const mk = () => R.criar({
  agora: () => t, espera: (fn, ms) => { const x = { at: t + ms, fn }; timers.push(x); return x; }, cancela: (x) => { timers = timers.filter((y) => y !== x); },
  sorteio: () => 0.5, silencioMs: 5000,
  conecta: async () => { connects++; return plan.length ? plan.shift() : { ok: true }; }, envia: (m) => { sent.push(m); return true; }, fecha: () => { closed++; },
});
async function ate(c, estado, max) { for (let s = 0; s < max && c.estado !== estado; s += 100) { run(100); await tick(); } }
(async () => {
  // 1) vigia: sem mensagens por >5 s derruba; 6 falhas seguidas respeitam o backoff 1,2,4,8,15,15 s
  let c = mk(); c.inicia(); await tick(); assert.equal(c.estado, "online");
  plan = [{ ok: false }, { ok: false }, { ok: false }, { ok: false }, { ok: false }, { ok: false }];
  run(6500); assert.equal(c.estado, "reconectando"); assert.equal(closed, 1);
  const marcas = [];
  for (let i = 0; i < 7; i++) { const a = connects; for (let s = 0; s < 20000 && connects === a; s += 100) { run(100); await tick(); } marcas.push(t); }
  const gaps = marcas.slice(1).map((v, i) => v - marcas[i]);
  assert.deepEqual(gaps.slice(0, 5), [2000, 4000, 8000, 15000, 15000], "backoff: " + gaps);
  await tick(); assert.equal(c.estado, "online", "voltou quando a rede voltou");
  // 2) comandos durante a queda: estado = o último vale, textos em ordem, cosmético descartado, interrupção recente vale
  sent.length = 0; c.caiu("canal"); assert.equal(c.estado, "reconectando");
  assert.equal(c.envia({ tipo: "look", gx: 1, gy: 0 }), false);
  c.envia({ tipo: "mode", modo: "teleoperado" }); c.envia({ tipo: "mode", modo: "assistido" }); c.envia({ tipo: "vision", ligado: false });
  c.envia({ tipo: "say", texto: "um" }); c.envia({ tipo: "say", texto: "dois" }); c.envia({ tipo: "interrupt" }); c.envia({ tipo: "expression", nome: "feliz" });
  assert.equal(c.pendentes, 5);
  await ate(c, "online", 3000);
  assert.deepEqual(sent.map((m) => m.tipo + ":" + (m.modo || m.texto || m.ligado || "")), ["mode:assistido", "vision:", "interrupt:", "say:um", "say:dois"]);
  assert.equal(c.pendentes, 0);
  // 3) teto de 5 textos (o mais antigo sai e é contado) e prazo de 60 s (texto vencido não é falado depois)
  c.caiu("canal"); c.envia({ tipo: "say", texto: "velho" }); for (let i = 0; i < 6; i++) c.envia({ tipo: "say", texto: "n" + i });
  assert.equal(c.perdidos, 2);
  c.para(); sent.length = 0;
  const c2 = mk(); c2.inicia(); await tick(); c2.caiu("canal");
  c2.envia({ tipo: "say", texto: "velho" }); plan = [{ ok: false }, { ok: false }, { ok: false }, { ok: false }, { ok: false }, { ok: false }, { ok: false }, { ok: false }];
  run(70000); await tick(); c2.envia({ tipo: "say", texto: "novo" }); plan = []; await ate(c2, "online", 30000);
  assert.deepEqual(sent.filter((m) => m.tipo === "say").map((m) => m.texto), ["novo"], "texto vencido não volta");
  c2.para();
  // 4) PIN errado/bloqueado: para de tentar
  connects = 0; plan = [{ ok: false, fatal: true, motivo: "PIN incorreto." }];
  const d = mk(); d.inicia(); await tick(); assert.equal(d.estado, "offline"); run(60000); await tick(); assert.equal(connects, 1);
  // 5) mensagens do robô mantêm a conexão viva
  plan = []; closed = 0; const e = mk(); e.inicia(); await tick();
  for (let i = 0; i < 40; i++) { run(500); e.viuMensagem(); }
  assert.equal(e.estado, "online"); assert.equal(closed, 0);
  // 6) "Tentar agora" zera a espera
  e.caiu("x"); plan = [{ ok: false }, { ok: false }, { ok: false }]; connects = 0; run(1000); await tick(); run(1500); await tick();
  const antes = connects; e.tentaJa(); await tick(); assert(connects > antes, "tentaJa tentou na hora");
  e.para();
  console.log("test_reconect OK");
})().catch((x) => { console.error(x); process.exit(1); });
