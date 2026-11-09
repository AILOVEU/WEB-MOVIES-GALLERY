# -*- coding: utf-8 -*-
"""把 movies.js / watched.js / magnets.js 的 key 从 JavDB id 切换为规范化 code。
规范化规则：strip + 连续空白压缩为单空格 + 转大写。
每条 movie 保留 id 字段作为备用映射。转换前做碰撞/完整性校验，校验失败则不落盘。
"""
import json, io, os, re, shutil, sys

ROOT = r"C:\Users\wh\WorkBuddy\2026-10-10-07-52-17"
DATA = os.path.join(ROOT, "javdb_data")

def load(fname):
    t = io.open(os.path.join(DATA, fname), encoding="utf-8").read()
    return json.loads(t.split("=", 1)[1].rsplit(";", 1)[0].strip())

def norm(code):
    return re.sub(r"\s+", " ", str(code or "").strip()).upper()

movies = load("movies.js")
watched = load("watched.js")
magnets = load("magnets.js")
print(f"loaded: movies={len(movies)} watched={len(watched)} magnets={len(magnets)}")

# --- build new movies keyed by norm(code) ---
nm, collisions, empty = {}, [], []
for mid, m in movies.items():
    k = norm(m.get("code"))
    if not k:
        empty.append(mid)
        continue
    if k in nm:
        collisions.append((k, nm[k]["id"], mid))
        continue
    nm[k] = m
if collisions or empty:
    print(f"ABORT: collisions={collisions} empty_code={empty}")
    sys.exit(1)
print(f"movies rekeyed: {len(nm)} (0 collisions, 0 empty)")

# --- watched: id -> code ---
nw, wmiss = {}, []
for mid, score in watched.items():
    m = movies.get(mid)
    if not m:
        wmiss.append(mid)
        continue
    nw[norm(m["code"])] = score
if wmiss:
    print(f"ABORT: watched ids missing from movies: {wmiss}")
    sys.exit(1)

# --- magnets: id -> code (collisions merge, keep longer list) ---
nmg, mgmiss, mgmerged = {}, [], 0
for mid, lst in magnets.items():
    m = movies.get(mid)
    if not m:
        mgmiss.append(mid)
        continue
    k = norm(m["code"])
    if k in nmg:
        mgmerged += 1
        if len(lst) > len(nmg[k]):
            nmg[k] = lst
    else:
        nmg[k] = lst
print(f"magnets rekeyed: {len(nmg)} (merged {mgmerged} code-collisions; ids missing: {len(mgmiss)})")

# --- sanity: counts preserved ---
assert len(nw) == len(watched), "watched count mismatch"
assert sum(len(v) for v in magnets.values()) == sum(len(v) for v in nmg.values()), "magnet entries lost"

# --- backup & write ---
for f in ("movies.js", "watched.js", "magnets.js"):
    shutil.copyfile(os.path.join(DATA, f), os.path.join(DATA, f + ".idkey.bak"))

def dump(fname, banner, obj):
    with io.open(os.path.join(DATA, fname), "w", encoding="utf-8") as f:
        f.write(f"/* {banner}（自动生成，勿手改） */\n")
        f.write("window." + obj[0] + " = ")
        json.dump(obj[1], f, ensure_ascii=False, indent=1)
        f.write(";\n")

dump("movies.js", "全量影片静态信息，key=规范化番号code（原JavDB id保留在每条的id字段）", ("JAVDB_MOVIES", nm))
dump("watched.js", "看过记录：code -> 评分（JavDB 5 分制，可手改）。以后同步只需维护此文件。", ("JAVDB_WATCHED", nw))
dump("magnets.js", "磁力链接库：code -> [{link,name,sizeMB,size,files,date,tags}]（自动生成）", ("JAVDB_MAGNETS", nmg))
print("written: movies.js / watched.js / magnets.js (backups: *.idkey.bak)")
print("sample keys:", list(nm)[:5], "| watched sample:", list(nw.items())[:3])
