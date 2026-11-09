# -*- coding: utf-8 -*-
"""Download missing ranking covers, 6-way concurrent (resume-safe)."""
import json, io, os, subprocess, time, queue, threading
ROOT = r"C:/Users/wh/WorkBuddy/2026-10-10-07-52-17"
COV = os.path.join(ROOT, "covers")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
missing = [x for x in json.load(io.open(os.path.join(ROOT, ".workbuddy", "rank_covers_need.json"), encoding="utf-8"))
           if not (os.path.exists(os.path.join(COV, x[0] + ".jpg")) and os.path.getsize(os.path.join(COV, x[0] + ".jpg")) > 5000)]
print(f"to download: {len(missing)}", flush=True)
q = queue.Queue()
for x in missing: q.put(x)
lock = threading.Lock()
stat = {"ok": 0, "fail": 0}
def worker():
    while True:
        try: code, url = q.get_nowait()
        except queue.Empty: return
        dst = os.path.join(COV, code + ".jpg")
        good = False
        for a in range(3):
            subprocess.run(["curl","-s","--ssl-no-revoke","--max-time","30",
                            "-x","http://127.0.0.1:7890","-A",UA,
                            "-H","Referer: https://javdb.com/",url,"-o",dst], capture_output=True)
            if os.path.exists(dst) and os.path.getsize(dst) > 5000:
                good = True; break
            time.sleep(4)
        with lock:
            stat["ok" if good else "fail"] += 1
            n = stat["ok"] + stat["fail"]
            if n % 100 == 0:
                print(f"[{n}/{len(missing)}] ok={stat['ok']} fail={stat['fail']}", flush=True)
        if not good:
            print(f"FAIL {code} {url}", flush=True)
        q.task_done()
ts = [threading.Thread(target=worker, daemon=True) for _ in range(6)]
t0 = time.time()
for t in ts: t.start()
for t in ts: t.join()
print(f"DONE ok={stat['ok']} fail={stat['fail']} in {time.time()-t0:.0f}s", flush=True)
