// 新站 LTX 抓取（2026-11-19 起）：浏览器过 Turnstile 后，走 ECDH/HKDF/AES-GCM 握手，
// 分页拉全量 items，导出 _pipeline/<日期>/items_all.json，供 ltx_merge_harvest.py 合并。
//
// 前置：
//   1) 代理 127.0.0.1:7890 可用；
//   2) 一个「可视」Chrome 已开 CDP（本脚本连 127.0.0.1:<PORT>，默认 9333），
//      且已打开 https://ltxlib.dpdns.org/ 并通过 Turnstile 人机验证（页面显示作品列表）。
//      启动示例（PowerShell）：
//        $chrome='C:\Program Files\Google\Chrome\Application\chrome.exe'
//        $prof='D:\workspace\z_WEB-MOVIES-GALLERY\JAVDB\_pipeline\ltx_chrome'
//        Start-Process $chrome -ArgumentList @('--remote-debugging-port=9333',
//          "--user-data-dir=$prof",'--proxy-server=http://127.0.0.1:7890',
//          '--no-first-run','--no-default-browser-check','--new-window','https://ltxlib.dpdns.org/')
//      等页面出现 3936+ 部作品（验证自动过），再跑本脚本。
//
// 用法（Node 22+，需全局 WebSocket）：
//   node _script/cookie/ltx_harvest.js [CDP_PORT] [OUT_PATH]
//   OUT_PATH 缺省 _pipeline/<今天>/items_all.json
const http = require('http'), fs = require('fs'), path = require('path');
const PORT = process.argv[2] || '9333';
const ROOT = path.join(__dirname, '..', '..');
const day = new Date().toLocaleDateString('sv-SE'); // YYYY-MM-DD 本地时区
const OUT = process.argv[3] || path.join(ROOT, '_pipeline', day, 'items_all.json');

function gj(p) {
  return new Promise((res, rej) => {
    http.get({ host: '127.0.0.1', port: PORT, path: p, agent: false }, r => {
      let d = ''; r.on('data', c => d += c); r.on('end', () => { try { res(JSON.parse(d)); } catch (e) { rej(e); } });
    }).on('error', rej);
  });
}
(async () => {
  const tabs = await gj('/json');
  const pg = tabs.find(x => x.type === 'page' && /ltxlib\.dpdns\.org/.test(x.url));
  if (!pg) { console.error('ERR 没有 ltxlib 标签页；先在可视 Chrome 打开站点并过验证'); process.exit(1); }
  const ws = new WebSocket(pg.webSocketDebuggerUrl);
  let id = 0; const pend = {};
  ws.onmessage = e => { const m = JSON.parse(e.data); if (m.id && pend[m.id]) { pend[m.id](m); delete pend[m.id]; } };
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  const send = (method, params = {}) => new Promise((res, rej) => {
    const i = ++id; pend[i] = m => m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
    ws.send(JSON.stringify({ id: i, method, params }));
  });
  const ev = async expr => {
    const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || JSON.stringify(r.exceptionDetails));
    return r.result.value;
  };

  // 安装会话与加密调用助手（页面内，借用 crypto.subtle + 已过验证的 cookie）
  const init = await ev(`(async()=>{
    const TAG='ltx-api-response-v1';
    const b64u=s=>{s=s.replace(/-/g,'+').replace(/_/g,'/');while(s.length%4)s+='=';return Uint8Array.from(atob(s),c=>c.charCodeAt(0))};
    const b64enc=a=>{let s='';for(const b of a)s+=String.fromCharCode(b);return btoa(s).replace(/\\+/g,'-').replace(/\\//g,'_').replace(/=+$/,'')};
    const enc=new TextEncoder();
    const aad=(sid,rid,method,path,status,encoding)=>enc.encode(JSON.stringify([TAG,sid,rid,method.toUpperCase(),path,status,encoding]));
    async function session(){
      const kp=await crypto.subtle.generateKey({name:'ECDH',namedCurve:'P-256'},false,['deriveBits']);
      const pub=b64enc(new Uint8Array(await crypto.subtle.exportKey('raw',kp.publicKey)));
      let r=await fetch('/api/crypto/session',{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify({v:1,publicKey:pub})});
      if(!r.ok)throw Error('session '+r.status+' '+(await r.text()).slice(0,120));
      const s=await r.json();
      const srv=await crypto.subtle.importKey('raw',b64u(s.publicKey),{name:'ECDH',namedCurve:'P-256'},false,[]);
      const bits=await crypto.subtle.deriveBits({name:'ECDH',public:srv},kp.privateKey,256);
      const hk=await crypto.subtle.importKey('raw',bits,'HKDF',false,['deriveKey']);
      const key=await crypto.subtle.deriveKey({name:'HKDF',hash:'SHA-256',salt:b64u(s.salt),info:enc.encode(TAG+':'+s.sid)},hk,{name:'AES-GCM',length:256},false,['decrypt']);
      window.__ltx={sess:{sid:s.sid,key,expiresAt:s.expiresAt}};
      return window.__ltx.sess;
    }
    async function call(method,path,body){
      let sess=window.__ltx&&window.__ltx.sess;
      if(!sess||(sess.expiresAt&&Date.now()>sess.expiresAt-5000))sess=await session();
      const rid=b64enc(crypto.getRandomValues(new Uint8Array(16)));
      const h={'X-LTX-Session':sess.sid,'X-LTX-Request':rid,Accept:'application/json'};
      const opt={method,credentials:'include',headers:h};
      if(body!==undefined){h['Content-Type']='application/json';opt.body=JSON.stringify(body);}
      let r=await fetch(path,opt);
      const ct=(r.headers.get('content-type')||'').split(';')[0].trim().toLowerCase();
      if(ct!=='application/vnd.ltx.encrypted+json')return {err:'HTTP '+r.status+' ct='+ct+' body='+(await r.text()).slice(0,150)};
      const e=await r.json();
      let plain=await crypto.subtle.decrypt({name:'AES-GCM',iv:b64u(e.iv),additionalData:aad(e.sid,e.rid,method,path,r.status,e.encoding)},sess.key,b64u(e.body));
      if(e.encoding==='gzip')plain=await new Response(new Blob([plain]).stream().pipeThrough(new DecompressionStream('gzip'))).arrayBuffer();
      return JSON.parse(new TextDecoder().decode(plain));
    }
    await session();
    const base=await call('GET','/api/items',undefined);
    if(base.err)return JSON.stringify({err:base.err});
    window.__ltx.call=call; window.__ltx.snapshot=base.snapshot; window.__ltx.total=base.total||0;
    return JSON.stringify({ok:true,snapshot:base.snapshot,total:base.total,updated_at:base.updated_at});
  })()`);
  const meta = JSON.parse(init);
  if (meta.err) { console.error('ERR 握手失败（确认已过 Turnstile）:', meta.err); process.exit(1); }
  console.log('snapshot', meta.snapshot, 'total', meta.total, 'updated_at', meta.updated_at);

  const all = []; let offset = 0; const limit = 60; let guard = 0;
  while (offset < meta.total && guard++ < 200) {
    const out = await ev(`(async()=>{const r=await window.__ltx.call('POST','/api/items/query',{snapshot:window.__ltx.snapshot,offset:${offset},limit:${limit}});return JSON.stringify(r)})()`);
    const r = JSON.parse(out);
    if (r.err) { console.error('ERR 分页失败 @', offset, r.err); process.exit(1); }
    const its = r.items || [];
    if (!its.length) { console.log('空页 @', offset, '提前结束'); break; }
    all.push(...its); offset += its.length;
    if (guard % 10 === 0 || offset >= meta.total) console.log('progress', all.length, '/', meta.total);
    if (offset >= meta.total) break;
  }
  const map = {}; for (const it of all) if (it.code) map[it.code] = it;
  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  fs.writeFileSync(OUT, JSON.stringify(Object.values(map)));
  console.log('DONE got=' + all.length + ' uniq=' + Object.keys(map).length + ' -> ' + OUT);
  ws.close(); process.exit(0);
})().catch(e => { console.error('ERR', e.message); process.exit(1); });
