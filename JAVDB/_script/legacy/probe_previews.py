# -*- coding: utf-8 -*-
"""Probe preview_url for all videos via curl (no browser needed)."""
import json, io, os, subprocess, time, re
from concurrent.futures import ThreadPoolExecutor

WS = r"C:\Users\wh\WorkBuddy\2026-10-10-07-52-17"
WB = os.path.join(WS, ".workbuddy")
CK = io.open(os.path.join(WB, "javdb_cookie.txt"), encoding="utf-8").read().strip()
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

vids = []
for f, gall in [("javdb_watched/config.js", "watched"), ("javdb_want/config.js", "want")]:
    t = io.open(os.path.join(WS, f), encoding="utf-8").read()
    cfg = json.loads(t[t.index("{"): t.rindex("}") + 1])
    for v in cfg["videos"]:
        vids.append({"id": v["id"], "code": v["code"], "gall": gall})
print("to probe:", len(vids))

def probe(v, tries=3):
    url = f"https://javdb.com/v/{v['id']}/preview_url"
    for i in range(tries):
        r = subprocess.run(["curl", "-s", "--ssl-no-revoke", "--max-time", "20",
                            "-x", "http://127.0.0.1:7890", "-A", UA,
                            "-H", f"Cookie: {CK}", "-H", "Referer: https://javdb.com/",
                            "-w", "\n%{http_code}", url], capture_output=True, text=True)
        out = r.stdout.strip()
        if not out:
            time.sleep(1.5 * (i + 1)); continue
        body, _, code = out.rpartition("\n")
        if code == "200":
            m = re.search(r'"url"\s*:\s*"([^"]+)"', body)
            if m:
                u = m.group(1).replace("\\u0026", "&")
                v["url"] = u if u.startswith("http") else "https://javdb.com" + u
                return v
            return None  # 200 but no url -> no preview
        if code == "404":
            return None
        time.sleep(1.5 * (i + 1))  # 000/429/5xx -> retry
    v["url"] = "ERROR"
    return v

results, done = [], [0]
def work(v):
    r = probe(v)
    done[0] += 1
    if done[0] % 50 == 0:
        n_ok = sum(1 for x in results if x and x.get("url") not in (None, "ERROR"))
        print(f"probed {done[0]}/{len(vids)}, previews found: {n_ok}", flush=True)
    if r and r.get("url") not in (None, "ERROR"):
        results.append(r)
    elif r and r.get("url") == "ERROR":
        results.append(r)  # keep errors for re-probe
    return r

with ThreadPoolExecutor(max_workers=4) as ex:
    list(ex.map(work, vids))

errs = [x for x in results if x.get("url") == "ERROR"]
results = [x for x in results if x.get("url") != "ERROR"]
io.open(os.path.join(WB, "previews.json"), "w", encoding="utf-8").write(json.dumps(results, ensure_ascii=False, indent=1))
io.open(os.path.join(WB, "previews_errors.json"), "w", encoding="utf-8").write(json.dumps(errs, ensure_ascii=False, indent=1))
print("previews found:", len(results), "| errors:", len(errs))
