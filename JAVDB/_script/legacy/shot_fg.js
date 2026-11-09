const http = require("http");
const fs = require("fs");
const PORT = process.argv[2] || "9223";
const TARGET = process.argv[3];
const OUT = process.argv[4];
function gj(p, method) {
  return new Promise((res, rej) => {
    const req = http.request({ host: "127.0.0.1", port: PORT, path: p, agent: false, method: method || "GET" }, r => {
      let d = ""; r.on("data", c => d += c);
      r.on("end", () => { try { res(JSON.parse(d)); } catch (e) { res(null); } });
    });
    req.on("error", rej); req.end();
  });
}
(async () => {
  const url = "file:///C:/Users/wh/WorkBuddy/2026-10-10-07-52-17/" + TARGET;
  const tab = await gj("/json/new?" + encodeURI(url), "PUT").catch(() => gj("/json/new?" + encodeURI(url)));
  await new Promise(r => setTimeout(r, 3000));
  await gj("/json/activate/" + tab.id).catch(()=>{});
  await new Promise(r => setTimeout(r, 5000));
  const tabs = await gj("/json");
  const pg = tabs.find(x => x.id === tab.id);
  const ws = new WebSocket(pg.webSocketDebuggerUrl);
  let id = 0; const pend = {};
  ws.onmessage = e => { const m = JSON.parse(e.data); if (m.id && pend[m.id]) { pend[m.id](m); delete pend[m.id]; } };
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  const send = (method, params = {}) => new Promise((res, rej) => {
    const i = ++id; pend[i] = m => m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
    ws.send(JSON.stringify({ id: i, method, params }));
  });
  await send("Page.enable");
  await new Promise(r => setTimeout(r, 2500));
  const r = await send("Runtime.evaluate", { expression: `JSON.stringify({
    loaded: [...document.querySelectorAll('.poster img')].slice(0,12).filter(i=>i.classList.contains('loaded') && i.naturalWidth>0).length,
    firstRank: (document.querySelector('.card .rankb')||{}).textContent
  })`, returnByValue: true });
  console.log(TARGET, r.result.value);
  const shot = await send("Page.captureScreenshot", { format: "jpeg", quality: 70 });
  fs.writeFileSync(OUT, Buffer.from(shot.data, "base64"));
  ws.close();
  await gj("/json/close/" + tab.id).catch(()=>{});
  process.exit(0);
})().catch(e => { console.error("ERR", e.message); process.exit(1); });
