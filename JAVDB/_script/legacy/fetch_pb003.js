// Fetch PB-003 cover via browser session as base64
const http = require('http'), fs = require('fs');
function gj(p){return new Promise((res,rej)=>{http.get({host:'127.0.0.1',port:9222,path:p,agent:false},r=>{let d='';r.on('data',c=>d+=c);r.on('end',()=>res(JSON.parse(d)))}).on('error',rej)})}
(async()=>{
  const t = await gj('/json');
  const pg = t.find(x=>x.type==='page' && /javdb\.com/.test(x.url));
  const ws = new WebSocket(pg.webSocketDebuggerUrl);
  let id=0; const pend={};
  ws.onmessage = e => { const m=JSON.parse(e.data); if(m.id&&pend[m.id]){pend[m.id](m);delete pend[m.id]} };
  await new Promise((res,rej)=>{ws.onopen=res;ws.onerror=rej});
  const send=(method,params={})=>new Promise((res,rej)=>{const i=++id;pend[i]=m=>m.error?rej(new Error(JSON.stringify(m.error))):res(m.result);ws.send(JSON.stringify({id:i,method,params}))});
  const expr = `(async()=>{
    const r = await fetch('https://c0.jdbstatic.com/covers/jo/JO0X8.jpg',{credentials:'include'});
    if (!r.ok) return 'HTTP '+r.status;
    const buf = await r.arrayBuffer();
    let bin=''; const u8=new Uint8Array(buf);
    for (let i=0;i<u8.length;i+=0x8000) bin += String.fromCharCode.apply(null, u8.subarray(i,i+0x8000));
    return 'B64:'+btoa(bin);
  })()`;
  const r = await send('Runtime.evaluate',{expression:expr,awaitPromise:true,returnByValue:true});
  console.log('raw:', JSON.stringify(r).slice(0, 400));
  const v = r.result && r.result.value;
  if (typeof v === 'string' && v.startsWith('B64:')) {
    fs.writeFileSync('C:/Users/wh/WorkBuddy/2026-10-10-07-52-17/javdb_watched/images/PB-003.jpg', Buffer.from(v.slice(4),'base64'));
    console.log('saved b64 length:', v.length - 4);
  }
  ws.close(); process.exit(0);
})().catch(e=>{console.error('ERR',e.message);process.exit(1)});
