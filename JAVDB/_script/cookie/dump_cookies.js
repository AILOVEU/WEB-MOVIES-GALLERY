// Dump javdb.com cookies from CDP without navigating or touching other tabs.
const http = require("http");
const fs = require("fs");
const path = require("path");
// cookie 写到仓库的 _pipeline/（Python 脚本都从那里读；勿写死机器路径）
const WB = path.join(__dirname, "..", "..", "_pipeline");
fs.mkdirSync(WB, { recursive: true });
const PORT = process.argv[2] || "9222";

function gj(p) {
  return new Promise((res, rej) => {
    http.get({ host: "127.0.0.1", port: PORT, path: p, agent: false }, r => {
      let d = ""; r.on("data", c => d += c); r.on("end", () => res(JSON.parse(d)));
    }).on("error", rej);
  });
}

(async () => {
  const tabs = await gj("/json");
  const pg = tabs.find(x => x.type === "page" && /javdb\.com/.test(x.url));
  if (!pg) throw new Error("no javdb tab");
  const ws = new WebSocket(pg.webSocketDebuggerUrl);
  let id = 0; const pend = {};
  ws.onmessage = e => { const m = JSON.parse(e.data); if (m.id && pend[m.id]) { pend[m.id](m); delete pend[m.id]; } };
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  const send = (method, params = {}) => new Promise((res, rej) => {
    const i = ++id; pend[i] = m => m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
    ws.send(JSON.stringify({ id: i, method, params }));
  });
  const { cookies } = await send("Network.getCookies", { urls: ["https://javdb.com"] });
  const line = cookies.map(c => c.name + "=" + c.value).join("; ");
  fs.writeFileSync(path.join(WB, "javdb_cookie.txt"), line);
  console.log("saved", cookies.length, "cookies; _jdb_session:", /_jdb_session/.test(line), "; cf_clearance:", /cf_clearance/.test(line));
  ws.close(); process.exit(0);
})().catch(e => { console.error("ERR", e.message); process.exit(1); });
