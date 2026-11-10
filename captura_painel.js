// Abre a sala do operador no Chrome (headless), entra com o PIN, tira prints em vários tamanhos, mede rolagem e simula queda de conexão.
// Uso: node captura_painel.js <pasta_saida> [url] [porta_cdp] [pin]
const { spawn } = require("child_process");
const fs = require("fs");
const CH = process.env.CHROME || "C:/Program Files/Google/Chrome/Application/chrome.exe";
const OUT = process.argv[2];
const URL = process.argv[3] || "http://127.0.0.1:18795/";
const PORT = process.argv[4] || "9333";
const PIN = process.argv[5] || "123456";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  const ch = spawn(CH, ["--headless=new", "--remote-debugging-port=" + PORT, "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
    "--no-first-run", "--no-sandbox", "--user-data-dir=" + process.env.TEMP + "/chprof" + Date.now(), "--autoplay-policy=no-user-gesture-required", "about:blank"], { stdio: "ignore" });
  let tab; for (let i = 0; i < 50; i++) { try { tab = await (await fetch("http://127.0.0.1:" + PORT + "/json/new?about:blank", { method: "PUT" })).json(); break; } catch (e) { await sleep(200); } }
  const ws = new WebSocket(tab.webSocketDebuggerUrl); await new Promise((r) => (ws.onopen = r));
  let id = 0; const pend = {};
  ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id && pend[d.id]) { pend[d.id](d); delete pend[d.id]; } };
  const cmd = (method, params = {}) => new Promise((r) => { const i = ++id; pend[i] = r; ws.send(JSON.stringify({ id: i, method, params })); });
  const ev = async (expr) => (await cmd("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true })).result.result.value;
  const shot = async (name, w, h, mobile) => {
    await cmd("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: mobile ? 2 : 1, mobile: !!mobile });
    await sleep(500);
    const r = await cmd("Page.captureScreenshot", { format: "png" });
    fs.writeFileSync(OUT + "/" + name + ".png", Buffer.from(r.result.data, "base64"));
  };
  await cmd("Page.enable");
  await cmd("Emulation.setDeviceMetricsOverride", { width: 1280, height: 720, deviceScaleFactor: 1, mobile: false });
  await cmd("Page.navigate", { url: URL }); await sleep(1500);
  await shot("0_login", 1280, 720);
  await ev(`document.getElementById("pin").value="${PIN}"; document.getElementById("nome").value="Operador"; document.getElementById("enter").click(); 1`);
  for (let i = 0; i < 100 && (await ev(`document.getElementById("connText").textContent`)) !== "Online"; i++) await sleep(150);
  await sleep(500);
  const r = {};
  r.estado = await ev(`document.getElementById("connText").textContent`);
  r.noscroll = await ev(`JSON.stringify({sh:document.documentElement.scrollHeight, ch:document.documentElement.clientHeight, sw:document.documentElement.scrollWidth, cw:document.documentElement.clientWidth})`);
  await shot("1_desktop_1280x720", 1280, 720);
  await shot("2_desktop_1920x1080", 1920, 1080);
  await shot("3_tablet_1024x768", 1024, 768);
  await shot("4_celular_390x844", 390, 844, true);
  await ev(`document.getElementById("openLeft").click(); 1`); await sleep(300);
  await shot("5_celular_gestos", 390, 844, true);
  await ev(`document.getElementById("openRight").click(); 1`); await sleep(300);
  await shot("6_celular_voz_expressoes", 390, 844, true);
  await ev(`document.querySelectorAll(".col.open").forEach(p=>p.classList.remove("open")); 1`);
  await shot("7_celular_deitado_844x390", 844, 390, true);
  for (const [w, h] of [[1280,720],[1024,768],[390,844],[844,390],[360,640]]) {
    await cmd("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: w < 900 }); await sleep(300);
    r["scroll_" + w + "x" + h] = await ev(`(()=>{const e=document.documentElement;return (e.scrollHeight<=e.clientHeight+1 && e.scrollWidth<=e.clientWidth+1)?"sem rolagem":"ROLA "+e.scrollHeight+">"+e.clientHeight+" / "+e.scrollWidth+">"+e.clientWidth})()`);
  }
  await cmd("Emulation.setDeviceMetricsOverride", { width: 1280, height: 720, deviceScaleFactor: 1, mobile: false });
  // queda simulada: derruba a conexão do lado do navegador (desliga a rede do canal) e mostra o estado
  await ev(`window.__pc = null; 1`);
  await ev(`(()=>{ try{ dc.close(); }catch(e){} return 1 })()`);
  await sleep(600);
  r.apos_queda = await ev(`document.getElementById("connText").textContent`);
  await shot("8_reconectando_1280x720", 1280, 720);
  for (let i = 0; i < 150 && (await ev(`document.getElementById("connText").textContent`)) !== "Online"; i++) await sleep(150);
  r.apos_volta = await ev(`document.getElementById("connText").textContent`);
  await shot("9_online_de_novo_1280x720", 1280, 720);
  fs.writeFileSync(OUT + "/resultado.json", JSON.stringify(r, null, 1));
  console.log(JSON.stringify(r, null, 1));
  ch.kill(); process.exit(0);
})().catch((e) => { console.error(e); process.exit(1); });
