// Verify top250 popup renders magnet module for a movie that HAS magnets
const http = require("http");
const PORT = process.argv[2] || "9223";
const FILE = "C:/Users/wh/WorkBuddy/2026-10-10-07-52-17/top250.html";

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
  const url = "file:///" + encodeURI(FILE).replace(/%2F/gi, "/");
  const tab = await gj("/json/new?" + url, "PUT").catch(() => gj("/json/new?" + url));
  await new Promise(r => setTimeout(r, 4000));
  const tabs = await gj("/json");
  const pg = tabs.find(x => x.id === tab.id) || tabs.find(x => x.url.includes("top250.html"));
  if (!pg) throw new Error("tab not found");
  const ws = new WebSocket(pg.webSocketDebuggerUrl);
  let id = 0; const pend = {}; const logs = [];
  ws.onmessage = e => {
    const m = JSON.parse(e.data);
    if (m.id && pend[m.id]) { pend[m.id](m); delete pend[m.id]; return; }
    if (m.method === "Runtime.exceptionThrown") {
      const d = m.params.exceptionDetails;
      logs.push(`[EXC] ${d.text} @line ${d.lineNumber}`);
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
  await new Promise(r => setTimeout(r, 4000));
  const r = await send("Runtime.evaluate", { expression: `(async () => {
    const MAGS = window.JAVDB_MAGNETS || {};
    const DATA = window.JAVDB_TOP250 || [];
    const withMag = DATA.filter(c => MAGS[c.code] && MAGS[c.code].length);
    // stats
    const stats = { total: DATA.length, withMagnets: withMag.length, withoutMagnets: DATA.length - withMag.length };
    if (!withMag.length) return JSON.stringify({ stats });
    // click the card of the highest-ranked movie that has magnets
    const target = withMag[0];
    const card = document.querySelector('.card[data-code="' + target.code + '"]');
    if (!card) return JSON.stringify({ stats, error: 'card not found for ' + target.code });
    card.click();
    await new Promise(r => setTimeout(r, 300));
    const mask = document.getElementById('mask');
    const mag = document.querySelector('#mbody .mmag');
    return JSON.stringify({ stats, clicked: target.code, popupShown: mask.classList.contains('show'),
      magnetModule: !!mag, magItems: mag ? mag.querySelectorAll('.mmag-item').length : 0,
      firstTagText: mag ? (mag.querySelector('.mmag-meta b')||{}).textContent : null });
  })()`, returnByValue: true, awaitPromise: true });
  console.log("TOP250:", r.result.value);
  console.log("exceptions:", logs.length ? logs.join(" ; ") : "none");
  ws.close();
  await gj("/json/close/" + tab.id).catch(()=>{});
  process.exit(0);
})().catch(e => { console.error("ERR", e.message); process.exit(1); });
