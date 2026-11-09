# -*- coding: utf-8 -*-
import subprocess, re, io, os
WB = r"C:/Users/wh/WorkBuddy/2026-10-10-07-52-17/.workbuddy"
CK = io.open(os.path.join(WB, "javdb_cookie.txt"), encoding="utf-8").read().strip()
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
def curl(url):
    r = subprocess.run(["curl","-s","--ssl-no-revoke","--max-time","25","-x","http://127.0.0.1:7890","-A",UA,"-H",f"Cookie: {CK}",url], capture_output=True, text=True, encoding="utf-8", errors="ignore")
    return r.stdout or ""
for name, url in [("playback","https://javdb.com/rankings/playback"),
                  ("censored_all","https://javdb.com/rankings/movies?t=censored&p=all"),
                  ("fanza","https://javdb.com/rankings/fanza_award")]:
    h = curl(url)
    io.open(os.path.join(WB, f"probe_{name}.html"),"w",encoding="utf-8").write(h)
    items = h.count('class="item ')
    print(name, len(h), "bytes, items:", items)
