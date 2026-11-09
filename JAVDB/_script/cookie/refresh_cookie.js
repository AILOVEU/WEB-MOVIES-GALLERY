// 通过 CDP 刷新 JavDB cookie：导航 -> 等 Cloudflare 过 -> 确保“真正登录”(用页面内 fetch 校验) -> 导出 cookie。
// 需 Node 21+（全局 WebSocket）。运行：node _script/refresh_cookie.js 9222
// 前提：已用走同一代理的 Chrome 开了 --remote-debugging-port=9222（见 launch_chrome.sh）。
const http = require("http");
const fs = require("fs");
const path = require("path");

// cookie 写到仓库的 _pipeline/（Python 脚本都从那里读）
const WB = path.join(__dirname, "..", "..", "_pipeline");
fs.mkdirSync(WB, { recursive: true });
const CK_FILE = path.join(WB, "javdb_cookie.txt");
const PORT = process.argv[2] || "9222";
const EMAIL = "hy_wanghuan@163.com";
const PASS = "12050426@Hy";
// 用“页面内 fetch preview_url 是否返回 JSON”判定登录态：登录成功才有 {url:...}，未登录会返回 window.location.href='/login'。
const PROBE = "96YqG6"; // 一个确定有预告的片做探针

function gj(p, method) {
  return new Promise((res, rej) => {
    const req = http.request({ host: "127.0.0.1", port: PORT, path: p, method: method || "GET", agent: false },
      r => { let d = ""; r.on("data", c => d += c); r.on("end", () => { try { res(JSON.parse(d)); } catch (e) { rej(e); } }); });
    req.on("error", rej); req.end();
  });
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  let tabs = await gj("/json");
  let pg = tabs.find(t => t.type === "page" && /javdb\.com/.test(t.url)) || tabs.find(t => t.type === "page");
  if (!pg) pg = await gj("/json/new?https://javdb.com/users/sign_in", "PUT");

  const ws = new WebSocket(pg.webSocketDebuggerUrl);
  let id = 0; const pend = {};
  ws.onmessage = e => { const m = JSON.parse(e.data); if (m.id && pend[m.id]) { pend[m.id](m); delete pend[m.id]; } };
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  const send = (method, params = {}) => new Promise((res, rej) => {
    const i = ++id; pend[i] = m => m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
    ws.send(JSON.stringify({ id: i, method, params }));
  });
  const ev = async (expression) => {
    const r = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) throw new Error("pageJS: " + JSON.stringify(r.exceptionDetails).slice(0, 200));
    return r.result.value;
  };

  await send("Page.enable"); await send("Network.enable"); await send("Runtime.enable");
  await send("Page.navigate", { url: "https://javdb.com/users/sign_in" });

  const isAuth = async () => {
    try {
      const t = String(await ev(`fetch('/v/${PROBE}/preview_url',{credentials:'include'}).then(r=>r.text()).then(x=>(x||'').slice(0,30))`));
      return t.trim().startsWith("{");
    } catch (e) { return false; }
  };
  const cfPending = async () => {
    try { return /Just a moment|Attention Required|Checking|验证/i.test(String(await ev("document.title"))); } catch (e) { return true; }
  };

  let authed = false;
  for (let i = 0; i < 75; i++) {
    await sleep(2000);
    if (await cfPending()) { console.log(`[${i}] CF 质询中(请在弹出的 Chrome 里点一下验证框，如出现)...`); continue; }
    if (await isAuth()) { authed = true; console.log(`[${i}] ✅ 已认证`); break; }
    // 未认证：确保在登录页并（每轮）尝试提交登录
    const hasForm = await ev(`!!document.querySelector('#user_email, input[name="user[email]"]')`);
    if (hasForm) {
      console.log(`[${i}] 提交登录...`);
      await ev(`(async()=>{
        const setVal=(el,v)=>{const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;s.call(el,v);el.dispatchEvent(new Event('input',{bubbles:true}));};
        const em=document.querySelector('#user_email, input[name="user[email]"]');
        const pw=document.querySelector('#user_password, input[type="password"]');
        if(!em||!pw)return 'NOFORM'; setVal(em,${JSON.stringify(EMAIL)});setVal(pw,${JSON.stringify(PASS)});
        await new Promise(r=>setTimeout(r,800)); em.closest('form').submit(); return 'OK';
      })()`);
    } else {
      await send("Page.navigate", { url: "https://javdb.com/users/sign_in" });
    }
  }

  if (!authed) {
    console.error("!! 未能确认登录（可能需要你在 Chrome 窗口手动过 CF/登录）。未覆盖旧 cookie。");
    ws.close(); process.exit(1);
  }

  const { cookies } = await send("Network.getCookies", { urls: ["https://javdb.com", "https://www.javdb.com"] });
  const line = cookies.map(c => c.name + "=" + c.value).join("; ");
  const hasCF = /cf_clearance=/.test(line), hasSess = /_jdb_session=/.test(line);
  if (!hasCF || !hasSess) {
    console.error("!! cookie 不全: cf_clearance=", hasCF, " _jdb_session=", hasSess);
    ws.close(); process.exit(1);
  }
  fs.writeFileSync(CK_FILE, line, "utf8");
  console.log("✅ 已写入", CK_FILE, "，共", cookies.length, "项（含 cf_clearance + 已登录 _jdb_session）");
  ws.close(); process.exit(0);
})().catch(e => { console.error("ERR", e.message); process.exit(1); });
