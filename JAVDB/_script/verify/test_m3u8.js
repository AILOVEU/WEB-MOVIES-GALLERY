// Inspect m3u8 playlist structure
const http = require('http');
function gj(p){return new Promise((res,rej)=>{http.get({host:'127.0.0.1',port:9222,path:p,agent:false},r=>{let d='';r.on('data',c=>d+=c);r.on('end',()=>res(JSON.parse(d)))}).on('error',rej)})}
(async()=>{
  const t = await gj('/json');
  const pg = t.find(x=>x.type==='page' && /javdb\.com/.test(x.url));
  const ws = new WebSocket(pg.webSocketDebuggerUrl);
  let id=0; const pend={};
  ws.onmessage = e => { const m=JSON.parse(e.data); if(m.id&&pend[m.id]){pend[m.id](m);delete pend[m.id]} };
  await new Promise((res,rej)=>{ws.onopen=res;ws.onerror=rej});
  const send=(method,params={})=>new Promise((res,rej)=>{const i=++id;pend[i]=m=>m.error?rej(new Error(JSON.stringify(m.error))):res(m.result);ws.send(JSON.stringify({id:i,method,params}))});
  const r = await send('Runtime.evaluate',{expression:
    "fetch('/v/96YqG6/preview_url',{credentials:'include'}).then(r=>r.json()).then(j=>fetch(j.url,{credentials:'include'}).then(r=>r.text()))",
    awaitPromise:true,returnByValue:true});
  const txt = String(r.result.value);
  console.log(txt.slice(0, 900));
  console.log('--- total lines:', txt.split('\n').length);
  ws.close(); process.exit(0);
})().catch(e=>{console.error('ERR',e.message);process.exit(1)});
