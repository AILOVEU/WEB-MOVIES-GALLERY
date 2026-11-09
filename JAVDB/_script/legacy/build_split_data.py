# -*- coding: utf-8 -*-
"""Split per-gallery config.js into shared data files:
  javdb_data/movies.js   -> window.JAVDB_MOVIES  {id: {code,title,url,img,local,video,tags,date,score,votes,want}}
  javdb_data/watched.js  -> window.JAVDB_WATCHED {id: score}
Old config.js renamed to config.js.bak."""
import json, io, os

ROOT = r"C:\Users\wh\WorkBuddy\2026-10-10-07-52-17"
OUT = os.path.join(ROOT, "javdb_data")
os.makedirs(OUT, exist_ok=True)

def load_cfg(path):
    t = io.open(path, encoding="utf-8").read()
    body = t[t.find("{"):]
    return json.loads(body.rsplit(";", 1)[0].strip())["videos"]

watched = load_cfg(os.path.join(ROOT, "javdb_watched", "config.js"))
want = load_cfg(os.path.join(ROOT, "javdb_want", "config.js"))

movies = {}  # id -> info
for v in want:
    m = {k: v[k] for k in ("id", "code", "title", "url", "img", "score", "votes", "date", "tags", "local") if k in v}
    m["want"] = 1
    movies[v["id"]] = m
for v in watched:  # watched wins on conflict (none currently, but safe)
    m = {k: v[k] for k in ("id", "code", "title", "url", "img", "score", "votes", "date", "tags", "local", "video") if k in v}
    movies[v["id"]] = m

# order entries by release date desc so object key order = default display order
def dkey(m):
    return m.get("date") or ""
ordered = sorted(movies.values(), key=dkey, reverse=True)

wmap = {v["id"]: v.get("score") for v in watched}

# write movies.js
with io.open(os.path.join(OUT, "movies.js"), "w", encoding="utf-8") as f:
    f.write("/* 全量影片静态信息（自动生成，勿手改；新增影片由同步脚本追加） */\n")
    f.write("window.JAVDB_MOVIES = ")
    f.write(json.dumps({m["id"]: m for m in ordered}, ensure_ascii=False, indent=1))
    f.write(";\n")

# write watched.js
with io.open(os.path.join(OUT, "watched.js"), "w", encoding="utf-8") as f:
    f.write("/* 看过记录：id -> 评分（JavDB 5 分制，可手改）。以后同步只需维护此文件。 */\n")
    f.write("window.JAVDB_WATCHED = ")
    f.write(json.dumps(wmap, ensure_ascii=False, indent=1))
    f.write(";\n")

# rename old configs to .bak
for g in ("javdb_watched", "javdb_want"):
    p = os.path.join(ROOT, g, "config.js")
    if os.path.exists(p):
        os.replace(p, p + ".bak")

print(f"movies.js: {len(ordered)} entries | watched.js: {len(wmap)} entries")
