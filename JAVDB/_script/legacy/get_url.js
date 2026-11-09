// Get fresh preview m3u8 URL for one video -> file
const http = require('http'), fs = require('fs');
const vid = process.argv[2] || '96YqG6';
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
    `fetch('/v/${vid}/preview_url',{credentials:'include'}).then(r=>r.ok?r.json():null).then(j=>{if(!j||!j.url)return 'NONE';return j.url.startsWith('http')?j.url:'https://javdb.com'+j.url}).catch(e=>'ERR:'+e.message)`,
    awaitPromise:true,returnByValue:true,wrapper:'value'});
  const u = JSON.stringify(r.result.value).replace(/^"|"$/g,'');
  fs.writeFileSync('C:/Users/wh/WorkBuddy/2026-10-10-07-52-17/.workbuddy/test_url.txt', u);
  console.log('url saved:', u.slice(0,120));
  ws.close(); process.exit(0);
})().catch(e=>{console.error('ERR',e.message);process.exit(1)});
