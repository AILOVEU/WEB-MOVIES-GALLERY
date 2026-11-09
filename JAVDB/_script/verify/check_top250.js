// Verify top250.html rendering via CDP
const http = require("http");
function gj(p, method) {
  return new Promise((res, rej) => {
    const q = http.request({ host: "127.0.0.1", port: 9223, path: p, agent: false, method: method || "GET" }, r => {
      let d = ""; r.on("data", c => d += c); r.on("end", () => res(JSON.parse(d)));
    });
    q.on("error", rej); q.end();
  });
}
(async () => {
  const tabs = await gj("/json");
  const pg = tabs.find(x => /top250\.html/.test(x.url));
  if (!pg) { console.log("no top250 tab"); process.exit(0); }
  const ws = new WebSocket(pg.webSocketDebuggerUrl);
  let id = 0; const pend = {}; const logs = [];
  ws.onmessage = e => {
    const m = JSON.parse(e.data);
    if (m.id && pend[m.id]) { pend[m.id](m); delete pend[m.id]; return; }
    if (m.method === "Runtime.exceptionThrown") {
      const d = m.params.exceptionDetails;
      logs.push(d.text + " " + (d.exception && d.exception.description || ""));
    }
  };
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  const send = (method, params = {}) => new Promise((res, rej) => {
    const i = ++id; pend[i] = m => m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
    ws.send(JSON.stringify({ id: i, method, params }));
  });
  await send("Runtime.enable");
  const expr = "JSON.stringify({cards:document.querySelectorAll('.card,article').length,imgs:document.querySelectorAll('img').length,title:document.title})";
  const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true });
  console.log("top250 STATE:", r.result.value);
  console.log("exceptions:", logs.length ? logs : "none");
  ws.close(); process.exit(0);
})().catch(e => { console.error("ERR", e.message); process.exit(1); });
