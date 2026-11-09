const http = require("http");
const PORT = process.argv[2] || "9223";
const TARGET = process.argv[3];
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
  const url = TARGET.startsWith("http") ? TARGET : "file:///" + encodeURI(TARGET).replace(/%2F/gi, "/");
  const tab = await gj("/json/new?" + encodeURI(url), "PUT").catch(() => gj("/json/new?" + encodeURI(url)));
  await new Promise(r => setTimeout(r, 6000));
  const tabs = await gj("/json");
  const pg = tabs.find(x => x.id === tab.id) || tabs.find(x => x.url.includes("top250"));
  if (!pg) throw new Error("tab not found");
  const ws = new WebSocket(pg.webSocketDebuggerUrl);
  let id = 0; const pend = {};
  ws.onmessage = e => { const m = JSON.parse(e.data); if (m.id && pend[m.id]) { pend[m.id](m); delete pend[m.id]; } };
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  const send = (method, params = {}) => new Promise((res, rej) => {
    const i = ++id; pend[i] = m => m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
    ws.send(JSON.stringify({ id: i, method, params }));
  });
  await send("Runtime.enable");
  await new Promise(r => setTimeout(r, 3000));
  const r = await send("Runtime.evaluate", { expression: `(async () => {
    await new Promise(r => setTimeout(r, 3000));
    const imgs = [...document.querySelectorAll('.card .poster img')].slice(0, 60);
    let ok = 0, natural0 = 0, displayNone = 0;
    const failedSrc = [];
    imgs.forEach(i => {
      if (i.complete && i.naturalWidth > 100) ok++;
      else if (i.naturalWidth === 0) { natural0++; if (failedSrc.length < 5) failedSrc.push(i.src.slice(0, 90)); }
      if (i.style.display === 'none') displayNone++;
    });
    // probe one failing src directly
    let probe = null;
    if (failedSrc.length) {
      try {
        const resp = await fetch(failedSrc[0]);
        probe = resp.status;
      } catch (e) { probe = 'fetch-err ' + e.message; }
    }
    return JSON.stringify({ url: location.href.slice(0, 80), tried: imgs.length, ok,
      natural0, displayNone, failedSrc, probe });
  })()`, returnByValue: true, awaitPromise: true });
  console.log(r.result.value);
  ws.close();
  await gj("/json/close/" + tab.id).catch(()=>{});
  process.exit(0);
})().catch(e => { console.error("ERR", e.message); process.exit(1); });
