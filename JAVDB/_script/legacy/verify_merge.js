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
  const out = {};
  for (const [name, file] of [["mylist", "mylist.html"], ["top250", "top250.html"]]) {
    const url = "file:///C:/Users/wh/WorkBuddy/2026-10-10-07-52-17/" + file;
    const tab = await gj("/json/new?" + encodeURI(url), "PUT").catch(() => gj("/json/new?" + encodeURI(url)));
    await new Promise(r => setTimeout(r, 4000));
    const tabs = await gj("/json");
    const pg = tabs.find(x => x.id === tab.id);
    if (!pg) throw new Error(name + " tab not found");
    const ws = new WebSocket(pg.webSocketDebuggerUrl);
    let id = 0; const pend = {};
    const errs = [];
    ws.onmessage = e => {
      const m = JSON.parse(e.data);
      if (m.id && pend[m.id]) { pend[m.id](m); delete pend[m.id]; }
      if (m.method === "Runtime.exceptionThrown") errs.push(m.params.exceptionDetails.text + " " + (m.params.exceptionDetails.exception && m.params.exceptionDetails.exception.description || ""));
    };
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    const send = (method, params = {}) => new Promise((res, rej) => {
      const i = ++id; pend[i] = m => m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
      ws.send(JSON.stringify({ id: i, method, params }));
    });
    await send("Runtime.enable");
    await new Promise(r => setTimeout(r, 2500));
    const r = await send("Runtime.evaluate", { expression: `(async () => {
      const out = {};
      if (document.querySelector('#tabs')) {  // mylist
        await new Promise(r => setTimeout(r, 2500));
        out.watched = document.querySelectorAll('.card').length + ' | ' + document.getElementById('cnt').textContent;
        // switch to want
        const btns = document.querySelectorAll('#tabs .btn');
        btns[1].click();
        await new Promise(r => setTimeout(r, 2500));
        out.want = document.querySelectorAll('.card').length + ' | ' + document.getElementById('cnt').textContent;
        // open first card modal
        document.querySelector('.card').click();
        await new Promise(r => setTimeout(r, 600));
        out.modal = !!document.querySelector('.mask.show');
        out.modalTitle = (document.querySelector('.mtitle')||{}).textContent || '';
        out.magnetMod = !!document.querySelector('.mmag');
        document.getElementById('mx').click();
        // img visibility check (loaded class on first few)
        out.imgsLoaded = [...document.querySelectorAll('.poster img')].slice(0,12).filter(i=>i.classList.contains('loaded')).length + '/12';
        // back to watched
        btns[0].click();
        await new Promise(r => setTimeout(r, 1500));
        out.back = document.querySelectorAll('.card').length;
      } else {  // top250
        await new Promise(r => setTimeout(r, 3000));
        out.default = document.querySelectorAll('.card').length + ' | ' + document.getElementById('cnt').textContent;
        const sel = document.getElementById('vsel');
        out.options = sel.options.length;
        // switch to a year variant
        const y = [...sel.options].find(o => /^y20\\d\\d$/.test(o.value));
        sel.value = y.value; sel.dispatchEvent(new Event('change'));
        await new Promise(r => setTimeout(r, 1500));
        out.yearTab = y.textContent + ': ' + document.querySelectorAll('.card').length + ' | ' + document.getElementById('cnt').textContent;
        out.imgsLoaded = [...document.querySelectorAll('.poster img')].slice(0,12).filter(i=>i.classList.contains('loaded')).length + '/12';
        document.querySelector('.card').click();
        await new Promise(r => setTimeout(r, 600));
        out.modalTitle = (document.querySelector('.mtitle')||{}).textContent || '';
        out.rank = (document.querySelector('.rk')||{}).textContent || '';
        out.modal = !!document.querySelector('.mask.show');
      }
      return JSON.stringify(out);
    })()`, returnByValue: true, awaitPromise: true });
    out[name] = JSON.parse(r.result.value);
    out[name].jsErrors = errs.slice(0, 3);
    ws.close();
    await gj("/json/close/" + tab.id).catch(() => {});
    await new Promise(r => setTimeout(r, 500));
  }
  console.log(JSON.stringify(out, null, 1));
  process.exit(0);
})().catch(e => { console.error("ERR", e.message); process.exit(1); });
