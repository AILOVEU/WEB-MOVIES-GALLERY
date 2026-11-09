// Verify top250.html category tabs + covers + popup
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
  await new Promise(r => setTimeout(r, 4500));
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
  await new Promise(r => setTimeout(r, 4500));
  const r = await send("Runtime.evaluate", { expression: `(async () => {
    const out = { cats: Object.keys(window.JAVDB_RANKINGS||{}) };
    out.tabButtons = document.querySelectorAll('#tabs .sbtn').length;
    out.cnt = document.getElementById('cnt').textContent;
    // cover load check on current (top250) tab
    const imgs = [...document.querySelectorAll('.card .poster img')].slice(0, 40);
    await new Promise(r => setTimeout(r, 2500));
    out.coversLoaded = imgs.filter(i => i.complete && i.naturalWidth > 100).length;
    out.coversTried = imgs.length;
    // switch through all tabs (re-query each round: renderTabs rebuilds buttons)
    out.perCat = {};
    const nTabs = document.querySelectorAll('#tabs .sbtn').length;
    for (let i = 0; i < nTabs; i++) {
      const b = document.querySelectorAll('#tabs .sbtn')[i];
      b.click();
      await new Promise(r => setTimeout(r, 250));
      const cards = document.querySelectorAll('.card').length;
      out.perCat['tab' + i + '_' + b.textContent.trim().replace(/\s+/g, ' ')] =
        cards + ' | ' + document.getElementById('cnt').textContent;
    }
    // fanza popup (award display + magnets)
    const btns = document.querySelectorAll('#tabs .sbtn');
    btns[btns.length-1].click();
    await new Promise(r => setTimeout(r, 300));
    const card = document.querySelector('.card');
    card.click();
    await new Promise(r => setTimeout(r, 300));
    out.fanzaPopup = {
      shown: document.getElementById('mask').classList.contains('show'),
      award: (document.querySelector('#mbody .maward')||{}).textContent || null,
      mag: !!document.querySelector('#mbody .mmag'),
      mfoot: !!document.querySelector('#mbody .mfoot a')
    };
    // top250 popup cover
    document.getElementById('mx').click();
    btns[1].click();
    await new Promise(r => setTimeout(r, 300));
    document.querySelector('.card').click();
    await new Promise(r => setTimeout(r, 500));
    const mi = document.querySelector('#mbody .mposter img');
    out.top250PopupCover = mi ? (mi.complete && mi.naturalWidth > 100 ? 'loaded' : ('failed:' + mi.src.slice(0, 60))) : 'no-img';
    return JSON.stringify(out);
  })()`, returnByValue: true, awaitPromise: true });
  console.log("TOP250 TABS:", r.result.value);
  console.log("exceptions:", logs.length ? logs.slice(0, 5).join(" ; ") : "none");
  ws.close();
  await gj("/json/close/" + tab.id).catch(()=>{});
  process.exit(0);
})().catch(e => { console.error("ERR", e.message); process.exit(1); });
