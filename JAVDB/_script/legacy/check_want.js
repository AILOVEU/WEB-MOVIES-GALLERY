// Open local want.html in a CDP tab, capture console errors and page state.
const http = require("http");
const PORT = process.argv[2] || "9223";
const FILE = process.argv[3] || "C:/Users/wh/WorkBuddy/2026-10-10-07-52-17/want.html";

function gj(p, method) {
  return new Promise((res, rej) => {
    const req = http.request({ host: "127.0.0.1", port: PORT, path: p, agent: false, method: method || "GET" }, r => {
      let d = ""; r.on("data", c => d += c); r.on("end", () => res(JSON.parse(d)));
    });
    req.on("error", rej); req.end();
  });
}

(async () => {
  // create new tab (newer Chrome requires PUT)
  const tab = await gj("/json/new?file:///" + encodeURI(FILE).replace(/%2F/gi, "/"), "PUT").catch(() => gj("/json/new?file:///" + encodeURI(FILE).replace(/%2F/gi, "/")));
  await new Promise(r => setTimeout(r, 3500));
  const tabs = await gj("/json");
  const pg = tabs.find(x => x.id === tab.id) || tabs.find(x => /want\.html/.test(x.url));
  if (!pg) throw new Error("tab not found");
  const ws = new WebSocket(pg.webSocketDebuggerUrl);
  let id = 0; const pend = {}; const logs = [];
  ws.onmessage = e => {
    const m = JSON.parse(e.data);
    if (m.id && pend[m.id]) { pend[m.id](m); delete pend[m.id]; return; }
    if (m.method === "Runtime.consoleAPICalled") {
      const args = (m.params.args || []).map(a => a.value != null ? String(a.value) : a.type).join(" ");
      logs.push(`[${m.params.type}] ${args}`);
    }
    if (m.method === "Runtime.exceptionThrown") {
      const d = m.params.exceptionDetails;
      logs.push(`[EXC] ${d.text} ${d.exception && d.exception.description || ""} @line ${d.lineNumber}`);
    }
  };
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  const send = (method, params = {}) => new Promise((res, rej) => {
    const i = ++id; pend[i] = m => m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
    ws.send(JSON.stringify({ id: i, method, params }));
  });
  await send("Runtime.enable");
  await send("Page.enable");
  await send("Page.reload");
  await new Promise(r => setTimeout(r, 3000));
  const r = await send("Runtime.evaluate", { expression: `JSON.stringify({
    url: location.href,
    movies: typeof window.JAVDB_MOVIES !== 'undefined' ? Object.keys(window.JAVDB_MOVIES).length : 'UNDEF',
    want: typeof window.JAVDB_WANT !== 'undefined' ? (Array.isArray(window.JAVDB_WANT) ? window.JAVDB_WANT.length : typeof window.JAVDB_WANT) : 'UNDEF',
    cards: document.querySelectorAll('.card').length,
    gridText: document.getElementById('grid').innerText.slice(0, 120),
    sentinel: document.getElementById('sentinel').innerText.slice(0, 60)
  })`, returnByValue: true });
  console.log("STATE:", r.result.value);
  console.log("CONSOLE LOGS (last 10):");
  logs.slice(-10).forEach(l => console.log(" ", l));
  ws.close(); process.exit(0);
})().catch(e => { console.error("ERR", e.message); process.exit(1); });
