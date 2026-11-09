const http = require("http");
const PORT = process.argv[2] || "9223";
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
  const url = "file:///C:/Users/wh/WorkBuddy/2026-10-10-07-52-17/top250.html";
  const tab = await gj("/json/new?" + encodeURI(url), "PUT").catch(() => gj("/json/new?" + encodeURI(url)));
  await new Promise(r => setTimeout(r, 7000));
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
  await send("Runtime.enable");
  await new Promise(r => setTimeout(r, 2000));
  const r = await send("Runtime.evaluate", { expression: `(async () => {
    const imgs = [...document.querySelectorAll('.card .poster img')].slice(0, 6);
    const info = imgs.map(i => ({
      src: i.getAttribute('src'), cur: i.currentSrc.slice(0, 60),
      complete: i.complete, nw: i.naturalWidth,
      disp: i.style.display, fb: i.dataset.fb || '',
      loadedCls: i.classList.contains('loaded')
    }));
    // probe direct fetch of one cover
    let probe = null;
    try { const resp = await fetch('assets/covers/' + imgs[0].getAttribute('src').split('/').pop()); probe = resp.status + ' type=' + resp.headers.get('content-type'); } catch (e) { probe = 'err ' + e.message; }
    return JSON.stringify({ info, probe }, null, 1);
  })()`, returnByValue: true, awaitPromise: true });
  console.log(r.result.value);
  ws.close();
  await gj("/json/close/" + tab.id).catch(()=>{});
  process.exit(0);
})().catch(e => { console.error("ERR", e.message); process.exit(1); });
