# -*- coding: utf-8 -*-
import io, os, subprocess, time
WB = r"C:/Users/wh/WorkBuddy/2026-10-10-07-52-17/.workbuddy"
CK = io.open(os.path.join(WB, "javdb_cookie.txt"), encoding="utf-8").read().strip()
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
print("cookie len:", len(CK))


def curl(url, timeout=30):
    r = subprocess.run(["curl","-s","--ssl-no-revoke","--max-time",str(timeout),
                        "-x","http://127.0.0.1:7890","-A",UA,"-H",f"Cookie: {CK}",
                        "-H","Referer: https://javdb.com/",
                        "-H","Accept-Language: zh-TW,zh;q=0.9,en;q=0.8", url],
                       capture_output=True, text=True, encoding="utf-8", errors="ignore")
    return r.stdout or ""


for u in ("https://javdb.com/actors/Q7G7", "https://javdb.com/actors/Q7G7?page=2"):
    t0 = time.time()
    h = curl(u)
    print(u, "dur=%.1fs" % (time.time()-t0), "len=", len(h))
    print("   movie-list:", "movie-list" in h, "| item:", '<div class="item">' in h,
          "| sign_in:", "sign_in" in h[:3000])
    print("   head:", repr(h[:160]))
    time.sleep(2)
