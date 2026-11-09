// Auto-login to JavDB via CDP: fill email/password, submit, then save cookies.
const http = require("http");
const fs = require("fs");
const path = require("path");
// cookie 写到仓库的 _pipeline/（过程中文件目录，Python 脚本都从那里读；勿写死机器路径）
const WB = path.join(__dirname, "..", "..", "_pipeline");
fs.mkdirSync(WB, { recursive: true });
const EMAIL = "hy_wanghuan@163.com";
const PASS = "12050426@Hy";

function gj(p) {
  return new Promise((res, rej) => {
    http.get({ host: "127.0.0.1", port: 9222, path: p, agent: false }, r => {
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
  const ev = (method, params = {}) => send(method, params); // events come without id, ignored

  const evalJS = async (expression) => {
    const r = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) throw new Error("page JS error: " + JSON.stringify(r.exceptionDetails).slice(0, 300));
    return r.result.value;
  };

  // wait for login form
  for (let i = 0; i < 20; i++) {
    const has = await evalJS(`!!document.querySelector('#user_email, input[name="user[email]"]')`);
    if (has) break;
    await new Promise(r => setTimeout(r, 1000));
  }
  const res = await evalJS(`(async () => {
    const setVal = (el, v) => {
      const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
      setter.call(el, v);
      el.dispatchEvent(new Event('input', { bubbles: true }));
    };
    const em = document.querySelector('#user_email, input[name="user[email]"]');
    const pw = document.querySelector('#user_password, input[name="user[user_password]"], input[type="password"]');
    if (!em || !pw) return 'FORM_NOT_FOUND';
    setVal(em, ${JSON.stringify(EMAIL)});
    setVal(pw, ${JSON.stringify(PASS)});
    await new Promise(r => setTimeout(r, 800));
    const form = em.closest('form');
    form.submit();
    return 'SUBMITTED';
  })()`);
  console.log("login:", res);

  // wait for redirect away from /login
  let ok = false;
  for (let i = 0; i < 30; i++) {
    await new Promise(r => setTimeout(r, 2000));
    const u = await evalJS(`location.href`);
    if (!/\/login/.test(u)) { ok = true; console.log("redirected to:", u); break; }
    console.log("waiting...", u);
  }
  if (!ok) throw new Error("login did not complete in 60s");

  // save cookies
  const { cookies } = await send("Network.getCookies", { urls: ["https://javdb.com"] });
  const line = cookies.map(c => c.name + "=" + c.value).join("; ");
  fs.writeFileSync(path.join(WB, "javdb_cookie.txt"), line);
  console.log("cookies saved:", cookies.length, "entries; has _jdb_session:", /_jdb_session/.test(line), "; has cf_clearance:", /cf_clearance/.test(line));
  ws.close(); process.exit(0);
})().catch(e => { console.error("ERR", e.message); process.exit(1); });
