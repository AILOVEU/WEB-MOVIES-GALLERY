// 页面截图助手（走 CDP）。用法：
//   node _script/shot_page.js <CDP端口> <页面路径或 URL> [_logs/shots/名字.jpg]
// 约定：截图统一放到 `_logs/shots/`；不给第三个参数就自动按时间命名到那里。
const http = require("http");
const fs = require("fs");
const path = require("path");
const PORT = process.argv[2] || "9223";
const TARGET = process.argv[3];
const OUT = process.argv[4] || path.join(__dirname, "..", "_logs", "shots", `shot_${new Date().toISOString().replace(/[-:T]/g, "").slice(0, 14)}.jpg`);
fs.mkdirSync(path.dirname(OUT), { recursive: true });   // _logs/ 被删空也能重建
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
  await new Promise(r => setTimeout(r, 7000));
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
  await send("Page.enable");
  await new Promise(r => setTimeout(r, 3000));
  const shot = await send("Page.captureScreenshot", { format: "jpeg", quality: 70 });
  fs.writeFileSync(OUT, Buffer.from(shot.data, "base64"));
  console.log("saved", OUT);
  ws.close();
  await gj("/json/close/" + tab.id).catch(()=>{});
  process.exit(0);
})().catch(e => { console.error("ERR", e.message); process.exit(1); });
