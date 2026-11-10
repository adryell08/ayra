/* Reconexão do painel do operador: backoff + vigia de silêncio + fila de comandos críticos.
   Sem DOM e sem relógio próprio (tudo injetado) para ser testável em Node. Leve: um temporizador de cada vez. */
(function (root) {
  "use strict";
  var BACKOFF = [1000, 2000, 4000, 8000, 15000];          // espera entre tentativas (±20 % de sorteio); o último valor se repete
  var ESTADO = { mode: 1, vision: 1, body: 1, robot_voice: 1 };   // "o último vale": só o valor mais novo é reenviado
  var TEXTO = { say: 1, instruction: 1 };                 // fala/instrução: reenviadas na ordem, com prazo e teto
  var TETO_TEXTO = 5, PRAZO_TEXTO = 60000, PRAZO_INTERRUPCAO = 5000;

  function criar(o) {
    var estado = "reconectando", tentativa = 0, timer = null, vigia = null, visto = 0, parado = false, ocupado = false;
    var ultimos = {}, textos = [], interrupt = 0, perdidos = 0;
    function agora() { return o.agora(); }
    function muda(e, info) { estado = e; if (o.aoMudar) o.aoMudar(e, info || {}); }
    function limpa() { if (timer !== null) { o.cancela(timer); timer = null; } }

    function agenda() {
      limpa();
      if (parado) return;
      var base = BACKOFF[Math.min(tentativa, BACKOFF.length - 1)];
      var espera = Math.round(base * (0.8 + 0.4 * o.sorteio()));
      muda("reconectando", { tentativa: tentativa + 1, em: espera });
      timer = o.espera(function () { timer = null; tenta(); }, espera);
    }
    function tenta() {
      if (parado || ocupado) return;
      ocupado = true;
      Promise.resolve().then(function () { return o.conecta(); }).then(function (r) {
        ocupado = false;
        if (parado) return;
        if (r && r.ok) { tentativa = 0; visto = agora(); muda("online"); descarrega(); vigiar(); }
        else if (r && r.fatal) { muda("offline", { motivo: r.motivo || "" }); }     // PIN errado/bloqueado: não insiste
        else { tentativa++; agenda(); }
      }, function () { ocupado = false; tentativa++; agenda(); });
    }
    function descarrega() {                               // retomada: estado atual, depois o que foi dito, sem repetir nada
      var t = agora(), k;
      for (k in ultimos) { o.envia(ultimos[k]); }
      ultimos = {};
      if (interrupt && t - interrupt <= PRAZO_INTERRUPCAO) o.envia({ tipo: "interrupt" });
      interrupt = 0;
      for (var i = 0; i < textos.length; i++) { if (t - textos[i].t <= PRAZO_TEXTO) o.envia(textos[i].m); else perdidos++; }
      textos = [];
    }
    function vigiar() {
      if (vigia !== null) o.cancela(vigia);
      vigia = o.espera(function checa() {
        vigia = null;
        if (parado || estado !== "online") return;
        if (agora() - visto > o.silencioMs) { cai("silencio"); return; }
        vigia = o.espera(checa, 1000);
      }, 1000);
    }
    function cai(motivo) {                                // chamada pelo vigia, pelo fechamento do canal ou pelo estado "failed"
      if (parado || estado !== "online") return;
      if (vigia !== null) { o.cancela(vigia); vigia = null; }
      try { o.fecha(); } catch (e) { /* já fechada */ }
      tentativa = 0;
      muda("reconectando", { motivo: motivo || "" });
      agenda();
    }
    return {
      get estado() { return estado; },
      get pendentes() { var n = textos.length + (interrupt ? 1 : 0); for (var k in ultimos) n++; return n; },
      get perdidos() { return perdidos; },
      inicia: function () { visto = agora(); parado = false; tenta(); },
      para: function () { parado = true; limpa(); if (vigia !== null) { o.cancela(vigia); vigia = null; } },
      viuMensagem: function () { visto = agora(); },
      caiu: cai,
      tentaJa: function () { if (estado === "reconectando" && !ocupado) { tentativa = 0; limpa(); tenta(); } },
      /* true = entregue agora; false = guardado (ou descartado, se for só cosmético: olhar/expressão/gesto/ping) */
      envia: function (m) {
        if (estado === "online" && o.envia(m)) return true;
        var k = m.tipo;
        if (ESTADO[k]) ultimos[k] = m;
        else if (TEXTO[k]) { textos.push({ t: agora(), m: m }); if (textos.length > TETO_TEXTO) { textos.shift(); perdidos++; } }
        else if (k === "interrupt") interrupt = agora();
        return false;
      },
    };
  }
  var api = { criar: criar, BACKOFF: BACKOFF };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.Reconect = api;
})(typeof self !== "undefined" ? self : this);
