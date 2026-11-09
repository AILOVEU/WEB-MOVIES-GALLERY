// Fetch ALL pages of javdb watched_videos via one CDP session
const http = require('http');
const fs = require('fs');
const DIR = 'C:/Users/wh/WorkBuddy/2026-10-10-07-52-17/.workbuddy';

function getJSON(path) {
  return new Promise((res, rej) => {
    http.get({ host: '127.0.0.1', port: 9222, path, agent: false }, r => {
      let d = '';
      r.on('data', c => (d += c));
      r.on('end', () => { try { res(JSON.parse(d)); } catch (e) { rej(e); } });
    }).on('error', rej);
  });
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const targets = await getJSON('/json');
  const page = targets.find(t => t.type === 'page' && /javdb\.com/.test(t.url));
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  let id = 0; const pend = {};
  ws.onmessage = e => { const m = JSON.parse(e.data); if (m.id && pend[m.id]) { pend[m.id](m); delete pend[m.id]; } };
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  const send = (method, params = {}) => new Promise((res, rej) => {
    const i = ++id;
    pend[i] = m => (m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result));
    ws.send(JSON.stringify({ id: i, method, params }));
  });

  async function fetchPage(n) {
    const r = await send('Runtime.evaluate', {
      expression: `fetch('https://javdb.com/users/watched_videos?page=${n}',{credentials:'include'}).then(r=>r.text())`,
      awaitPromise: true, returnByValue: true,
    });
    return r.result.value;
  }
  const countItems = h => (h.match(/class="video-title"/g) || []).length;

  // exponential + binary search for last page
  let lo = 1, hi = 2;
  for (;;) {
    const h = await fetchPage(hi); await sleep(600);
    const n = countItems(h);
    console.log(`page ${hi}: ${n} items`);
    if (n === 0) break;
    lo = hi; hi *= 2;
    if (hi > 500) { console.log('abort: too many pages'); process.exit(1); }
  }
  while (hi - lo > 1) {
    const mid = Math.floor((lo + hi) / 2);
    const h = await fetchPage(mid); await sleep(600);
    const n = countItems(h);
    console.log(`probe ${mid}: ${n}`);
    if (n > 0) lo = mid; else hi = mid;
  }
  const LAST = lo;
  console.log('LAST_PAGE =', LAST);

  // fetch & save pages 1..LAST (dedupe by content already probed is fine to redo)
  for (let p = 1; p <= LAST; p++) {
    const h = await fetchPage(p); await sleep(500);
    fs.writeFileSync(`${DIR}/javdb_p${p}.html`, h, 'utf8');
    console.log(`saved p${p}: ${countItems(h)} items, ${h.length} bytes`);
  }
  ws.close(); process.exit(0);
})().catch(e => { console.error('ERR', e.message); process.exit(1); });
