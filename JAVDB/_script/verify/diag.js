// Diagnose: list targets + test in-page fetch + location
const http = require('http');
function gj(p){return new Promise((res,rej)=>{http.get({host:'127.0.0.1',port:9222,path:p,agent:false},r=>{let d='';r.on('data',c=>d+=c);r.on('end',()=>res(JSON.parse(d)))}).on('error',rej)})}
(async()=>{
  const t = await gj('/json');
  for (const x of t) console.log(x.type, '|', (x.url||'').slice(0,80));
  const pg = t.find(x=>x.type==='page' && /javdb\.com/.test(x.url)) || t.find(x=>x.type==='page');
  console.log('chosen:', pg.url);
  const ws = new WebSocket(pg.webSocketDebuggerUrl);
  let id=0; const pend={};
  ws.onmessage = e => { const m=JSON.parse(e.data); if(m.id&&pend[m.id]){pend[m.id](m);delete pend[m.id]} };
  await new Promise((res,rej)=>{ws.onopen=res;ws.onerror=rej});
  const send=(method,params={})=>new Promise((res,rej)=>{const i=++id;pend[i]=m=>m.error?rej(new Error(JSON.stringify(m.error))):res(m.result);ws.send(JSON.stringify({id:i,method,params}))});
  const r = await send('Runtime.evaluate',{expression:"JSON.stringify({href:location.href, ready:document.readyState, online:navigator.onLine})",
    awaitPromise:true,returnByValue:true});
  console.log('state:', JSON.stringify(r.result.value));
  const r2 = await send('Runtime.evaluate',{expression:"fetch(location.origin+'/v/96YqG6/preview_url',{credentials:'include'}).then(r=>r.status).then(s=>'http '+s).catch(e=>'ERR '+e.message)",
    awaitPromise:true,returnByValue:true});
  console.log('fetch test:', JSON.stringify(r2.result.value));
  ws.close(); process.exit(0);
})().catch(e=>{console.error('ERR',e.message);process.exit(1)});
