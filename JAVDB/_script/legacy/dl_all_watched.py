# -*- coding: utf-8 -*-
"""Batch download watched-list preview videos. Restartable, skips existing files."""
import json, io, os, sys, time

WB = r"C:\Users\wh\WorkBuddy\2026-10-10-07-52-17\.workbuddy"
sys.path.insert(0, WB)
from dl_video import download, VID  # noqa

LOG = os.path.join(WB, "dl_progress.log")

def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")

prev = json.load(io.open(os.path.join(WB, "previews.json"), encoding="utf-8"))
targets = [x for x in prev if x.get("gall") == "watched" and x.get("url") not in (None, "", "ERROR")]
log(f"target videos: {len(targets)}")

ok = skip = fail = 0
fails = []
for i, t in enumerate(targets):
    code = t["code"].replace("/", "_")
    out_path = os.path.join(VID, code + ".mp4")
    if os.path.exists(out_path) and os.path.getsize(out_path) > 50000:
        skip += 1
        continue
    st = None
    for attempt in range(4):
        st = download(t["id"], t["code"], out_path)
        if st == "ok":
            break
        if st == "nopreview":
            # rate-limited by server; cool down before retrying
            time.sleep(45)
        else:
            time.sleep(10)
    if st == "ok":
        ok += 1
    else:
        fail += 1
        fails.append(f"{code}:{st}")
    log(f"[{i+1}/{len(targets)}] {code} -> {st} (ok={ok} skip={skip} fail={fail})")
    time.sleep(1)

log(f"ALL DONE ok={ok} skip={skip} fail={fail}")
if fails:
    log("failed list: " + ", ".join(fails))
