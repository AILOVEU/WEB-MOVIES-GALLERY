# -*- coding: utf-8 -*-
"""Download missing TOP250 covers (resume-safe)."""
import json, io, os, subprocess, time

ROOT = r"C:\Users\wh\WorkBuddy\2026-10-10-07-52-17"
IMG = os.path.join(ROOT, "javdb_top250", "images")
need = json.load(io.open(os.path.join(ROOT, ".workbuddy", "top250_need.json"), encoding="utf-8"))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

ok = fail = 0
for i, x in enumerate(need, 1):
    dst = os.path.join(IMG, x["code"] + ".jpg")
    if os.path.exists(dst) and os.path.getsize(dst) > 5000:
        continue
    url = x["cover"]
    for a in range(3):
        r = subprocess.run(["curl", "-s", "--ssl-no-revoke", "--max-time", "30",
                            "-x", "http://127.0.0.1:7890", "-A", UA,
                            "-H", "Referer: https://javdb.com/", url, "-o", dst],
                           capture_output=True)
        if os.path.exists(dst) and os.path.getsize(dst) > 5000:
            ok += 1
            break
        time.sleep(8)
    else:
        fail += 1
        print(f"[{i}/{len(need)}] {x['code']} FAIL {url}", flush=True)
    if i % 25 == 0:
        print(f"[{i}/{len(need)}] ok={ok} fail={fail}", flush=True)
    time.sleep(0.4)
print(f"DONE ok={ok} fail={fail}", flush=True)
