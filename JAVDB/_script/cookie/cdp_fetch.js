// Minimal CDP client: reads cookies and fetches a page's HTML with session credentials
const http = require('http');
const fs = require('fs');
const path = require('path');
const WORK = path.join(__dirname, '..', '..', '_pipeline');            // cookie 与断点都在 _pipeline/
const SAMPLES = path.join(WORK, 'samples');           // 抓回的页面样本
fs.mkdirSync(WORK, { recursive: true });
fs.mkdirSync(SAMPLES, { recursive: true });

function getJSON(urlPath) {        // 参数叫 urlPath，避免遮蔽上面 require 的 path 模块
  return new Promise((res, rej) => {
    http.get({ host: '127.0.0.1', port: 9222, path: urlPath, agent: false }, r => {
      let d = '';
      r.on('data', c => (d += c));
      r.on('end', () => { try { res(JSON.parse(d)); } catch (e) { rej(e); } });
    }).on('error', rej);
  });
}

(async () => {
  const targets = await getJSON('/json');
  const page = targets.find(t => t.type === 'page' && /javdb\.com/.test(t.url)) ||
               targets.find(t => t.type === 'page');
  if (!page) { console.error('ERR no page target'); process.exit(1); }
  console.log('target:', page.url);

  const ws = new WebSocket(page.webSocketDebuggerUrl);
  let id = 0; const pend = {};
  ws.onmessage = e => { const m = JSON.parse(e.data); if (m.id && pend[m.id]) { pend[m.id](m); delete pend[m.id]; } };
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });

  function send(method, params = {}) {
    return new Promise((res, rej) => {
      const i = ++id;
      pend[i] = m => (m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result));
      ws.send(JSON.stringify({ id: i, method, params }));
    });
  }

  // 1) dump cookies for javdb.com
  const ck = await send('Network.getCookies', { urls: ['https://javdb.com', 'https://www.javdb.com'] });
  const cookieHeader = ck.cookies.map(c => `${c.name}=${c.value}`).join('; ');
  fs.writeFileSync(path.join(WORK, 'javdb_cookie.txt'), cookieHeader, 'utf8');
  console.log('cookies saved:', ck.cookies.map(c => c.name).join(', '));

  // 2) fetch page HTML from inside the page (carries session automatically)
  const url = process.argv[2] || '';
  const expr = url
    ? `fetch(${JSON.stringify(url)},{credentials:'include'}).then(r=>r.text())`
    : `document.documentElement.outerHTML`;
  const html = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
  const out = process.argv[3] || path.join(SAMPLES, 'page.html');
  fs.writeFileSync(out, html.result.value, 'utf8');
  console.log('html saved:', out, html.result.value.length, 'bytes');

  ws.close();
  process.exit(0);
})().catch(e => { console.error('ERR', e.message); process.exit(1); });
