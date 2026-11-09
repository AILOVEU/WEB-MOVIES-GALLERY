// Verify top250 variant dropdown + updated display
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
    const out = {};
    const sel = document.getElementById('vsel');
    out.selVisible = sel.style.display !== 'none';
    out.selOptions = sel.querySelectorAll('option').length;
    out.cnt0 = document.getElementById('cnt').textContent;
    out.cards0 = document.querySelectorAll('.card').length;
    // switch variant via select
    const sels = ['all','t0','y2024','y2011'];
    out.perVariant = {};
    for (const v of sels) {
      sel.value = v;
      sel.dispatchEvent(new Event('change'));
      await new Promise(r => setTimeout(r, 200));
      out.perVariant[v] = {
        cards: document.querySelectorAll('.card').length,
        cnt: document.getElementById('cnt').textContent,
        firstCode: (document.querySelector('.card .bdg')||{}).textContent
      };
    }
    // switch to hot: dropdown should hide (single variant)
    document.querySelector('#tabs .sbtn[data-c="hot"]').click();
    await new Promise(r => setTimeout(r, 250));
    out.hotSelVisible = sel.style.display !== 'none';
    out.hotCnt = document.getElementById('cnt').textContent;
    // back to top250 popup
    document.querySelector('#tabs .sbtn[data-c="top250"]').click();
    await new Promise(r => setTimeout(r, 250));
    sel.value = 'y2024'; sel.dispatchEvent(new Event('change'));
    await new Promise(r => setTimeout(r, 250));
    document.querySelector('.card').click();
    await new Promise(r => setTimeout(r, 300));
    out.popup = {
      shown: document.getElementById('mask').classList.contains('show'),
      mfoot: !!document.querySelector('#mbody .mfoot a')
    };
    return JSON.stringify(out);
  })()`, returnByValue: true, awaitPromise: true });
  console.log("VARIANT TEST:", r.result.value);
  console.log("exceptions:", logs.length ? logs.slice(0, 5).join(" ; ") : "none");
  ws.close();
  await gj("/json/close/" + tab.id).catch(()=>{});
  process.exit(0);
})().catch(e => { console.error("ERR", e.message); process.exit(1); });
