// Probe preview_url for all videos in both galleries
// outputs: .workbuddy/previews.json  [{id, code, gall, url}]
const http = require('http');
const fs = require('fs');
const DIR = 'C:/Users/wh/WorkBuddy/2026-10-10-07-52-17/.workbuddy';

function gj(p){return new Promise((res,rej)=>{http.get({host:'127.0.0.1',port:9222,path:p,agent:false},r=>{let d='';r.on('data',c=>d+=c);r.on('end',()=>res(JSON.parse(d)))}).on('error',rej)})}
const sleep = ms => new Promise(r=>setTimeout(r,ms));

(async()=>{
  // load both configs
  const vids = [];
  for (const [f,gall] of [['../javdb_watched/config.js','watched'],['../javdb_want/config.js','want']]) {
    let s = fs.readFileSync(DIR+'/'+f,'utf8');
    s = s.slice(s.indexOf('{'), s.lastIndexOf('}')+1);
    const cfg = JSON.parse(s);
    for (const v of cfg.videos) vids.push({id:v.id, code:v.code, gall});
  }
  console.log('total videos to probe:', vids.length);

  const t = await gj('/json');
  const pg = t.find(x=>x.type==='page' && /javdb\.com/.test(x.url));
  const ws = new WebSocket(pg.webSocketDebuggerUrl);
  let id=0; const pend={};
  ws.onmessage = e => { const m=JSON.parse(e.data); if(m.id&&pend[m.id]){pend[m.id](m);delete pend[m.id]} };
  await new Promise((res,rej)=>{ws.onopen=res;ws.onerror=rej});
  const send=(method,params={})=>new Promise((res,rej)=>{const i=++id;pend[i]=m=>m.error?rej(new Error(JSON.stringify(m.error))):res(m.result);ws.send(JSON.stringify({id:i,method,params}))});

  const out = [];
  let done = 0, found = 0;
  for (const v of vids) {
    let ok = false, url = '';
    try {
      const r = await send('Runtime.evaluate',{expression:
        `fetch('/v/${v.id}/preview_url',{credentials:'include'}).then(r=>r.ok?r.json():null).then(j=>j&&j.url||'')`,
        awaitPromise:true,returnByValue:true});
      const u = r.result.value;
      if (typeof u === 'string' && u) { ok = true; url = u; found++; }
    } catch(e) { /* count as none */ }
    if (ok) out.push({id:v.id, code:v.code, gall:v.gall, url});
    done++;
    if (done % 50 === 0) {
      console.log(`probed ${done}/${vids.length}, found ${found}`);
      fs.writeFileSync(DIR+'/previews.json', JSON.stringify(out,null,1));
    }
    await sleep(250);
  }
  fs.writeFileSync(DIR+'/previews.json', JSON.stringify(out,null,1));
  console.log(`DONE probed ${done}, previews: ${found}`);
  ws.close(); process.exit(0);
})().catch(e=>{console.error('ERR',e.message);process.exit(1)});
