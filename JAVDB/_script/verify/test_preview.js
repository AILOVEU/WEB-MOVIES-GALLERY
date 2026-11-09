// Test preview_url endpoint format
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
  for (const vid of ['96YqG6','JO0X8']) {
    const r = await send('Runtime.evaluate',{expression:
      `fetch('/v/${vid}/preview_url',{credentials:'include'}).then(async r=>r.status+' | '+r.headers.get('content-type')+' | '+(await r.text()).slice(0,300))`,
      awaitPromise:true,returnByValue:true});
    console.log(vid, '->', JSON.stringify(r.result.value));
  }
  ws.close(); process.exit(0);
})().catch(e=>{console.error('ERR',e.message);process.exit(1)});
