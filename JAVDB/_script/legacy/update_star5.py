# -*- coding: utf-8 -*-
"""从 star=5 的「看过」列表页提取番号，把 watched.js 中对应条目评分更新为 5。"""
import json, io, re, os, shutil, sys, glob

WB = r"C:\Users\wh\WorkBuddy\2026-10-10-07-52-17\.workbuddy"
DATA = r"C:\Users\wh\WorkBuddy\2026-10-10-07-52-17\javdb_data"

def load_win(fname):
    """parse window.JAVDB_XXX = {...}; tolerates = inside comments"""
    t = io.open(os.path.join(DATA, fname), encoding="utf-8").read()
    m = re.search(r"window\.JAVDB_\w+\s*=\s*(\{.*\})\s*;\s*$", t, re.S)
    if not m:
        raise RuntimeError("cannot parse " + fname)
    return json.loads(m.group(1))

ids = []
for p in sorted(glob.glob(os.path.join(WB, "star5_p*.html"))):
    h = io.open(p, encoding="utf-8").read()
    if "video-title" not in h:
        print("ABORT: not a video list:", p)
        sys.exit(1)
    got = re.findall(r'data-batch-id="([A-Za-z0-9]+)"', h)
    print(os.path.basename(p), "->", len(got), "items")
    ids += got
ids = list(dict.fromkeys(ids))
print(f"star5 unique ids: {len(ids)}")

movies = load_win("movies.js")
ID2CODE = {m["id"]: k for k, m in movies.items()}
targets, missing = {}, []
for vid in ids:
    c = ID2CODE.get(vid)
    (targets.__setitem__(c, vid) if c else missing.append(vid))
print(f"mapped to codes: {len(targets)} | not in library: {len(missing)} {missing}")

watched = load_win("watched.js")
changed, already, absent = 0, 0, []
for c in targets:
    if c not in watched:
        absent.append(c)
        continue
    if watched[c] == 5:
        already += 1
    else:
        watched[c] = 5
        changed += 1
print(f"in watched.js: updated={changed} already-5={already} not-in-watched={len(absent)} {absent[:10]}")
print(f"watched.js total={len(watched)} | now rated 5: {sum(1 for v in watched.values() if v == 5)}")

if changed:
    shutil.copyfile(os.path.join(DATA, "watched.js"), os.path.join(DATA, "watched.js.bak"))
    with io.open(os.path.join(DATA, "watched.js"), "w", encoding="utf-8") as f:
        f.write("/* 看过记录：code -> 评分（JavDB 5 分制，可手改）。以后同步只需维护此文件。 */\n")
        f.write("window.JAVDB_WATCHED = ")
        json.dump(watched, f, ensure_ascii=False, indent=1)
        f.write(";\n")
    print("watched.js written (backup: watched.js.bak)")
else:
    print("no changes needed")
