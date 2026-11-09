// Verify magnet module in popups for watched.html / want.html / top250.html
const http = require("http");
const PORT = process.argv[2] || "9223";
const ROOT = "C:/Users/wh/WorkBuddy/2026-10-10-07-52-17/";
const PAGES = ["watched.html", "want.html", "top250.html"];

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
  const out = [];
  for (const page of PAGES) {
    const url = "file:///" + encodeURI(ROOT + page).replace(/%2F/gi, "/");
    const tab = await gj("/json/new?" + url, "PUT").catch(() => gj("/json/new?" + url));
    await new Promise(r => setTimeout(r, 4000));
    const tabs = await gj("/json");
    const pg = tabs.find(x => x.id === tab.id) || tabs.find(x => x.url.includes(page));
    if (!pg) { out.push(`${page}: TAB NOT FOUND`); continue; }
    const ws = new WebSocket(pg.webSocketDebuggerUrl);
    let id = 0; const pend = {}; const logs = [];
    ws.onmessage = e => {
      const m = JSON.parse(e.data);
      if (m.id && pend[m.id]) { pend[m.id](m); delete pend[m.id]; return; }
      if (m.method === "Runtime.exceptionThrown") {
        const d = m.params.exceptionDetails;
        logs.push(`[EXC] ${d.text} ${d.exception && d.exception.description || ""} @line ${d.lineNumber}`);
      }
      if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error") {
        logs.push("[ERR] " + (m.params.args || []).map(a => a.value != null ? String(a.value) : a.type).join(" "));
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
      const cards = document.querySelectorAll('.card');
      if (!cards.length) return JSON.stringify({ cards: 0 });
      cards[0].click();
      await new Promise(r => setTimeout(r, 300));
      const mask = document.getElementById('mask');
      const shown = mask && mask.classList.contains('show');
      const mag = document.querySelector('#mbody .mmag');
      const magH = mag ? mag.querySelector('.mmag-h') : null;
      const items = mag ? mag.querySelectorAll('.mmag-item').length : 0;
      const copyBtn = !!document.querySelector('#mbody .mmag-copy');
      const openLink = !!document.querySelector('#mbody a.mmag-open');
      const mfoot = !!document.querySelector('#mbody .mfoot a');
      return JSON.stringify({ cards: cards.length, popupShown: !!shown,
        magnetModule: !!mag, magHeader: magH ? magH.textContent.trim() : null,
        magItems: items, copyBtn, openLink, mfootLink: mfoot });
    })()`, returnByValue: true, awaitPromise: true });
    out.push(`${page}: ${r.result.value} | exceptions: ${logs.length ? logs.slice(0,3).join(' ; ') : 'none'}`);
    ws.close();
    // close tab
    await gj("/json/close/" + tab.id).catch(()=>{});
    await new Promise(r => setTimeout(r, 500));
  }
  console.log(out.join("\n"));
  process.exit(0);
})().catch(e => { console.error("ERR", e.message); process.exit(1); });
