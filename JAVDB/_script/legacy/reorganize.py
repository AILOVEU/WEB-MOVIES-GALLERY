# -*- coding: utf-8 -*-
"""目录结构重组：
ROOT/
  watched.html  want.html  top250.html
  covers/  videos/
  movies.js  watched.js  want.js  magnets.js  top250.js
旧目录内容移走后删除，*.bak 备份集中到 _backup/。
"""
import json, io, os, re, shutil, sys

ROOT = r"C:\Users\wh\WorkBuddy\2026-10-10-07-52-17"
COVERS = os.path.join(ROOT, "covers")
VIDEOS = os.path.join(ROOT, "videos")
BK = os.path.join(ROOT, "_backup")
os.makedirs(COVERS, exist_ok=True)
os.makedirs(VIDEOS, exist_ok=True)
os.makedirs(BK, exist_ok=True)

# ---------- 1. merge covers ----------
srcs = [os.path.join(ROOT, "javdb_watched", "images"),
        os.path.join(ROOT, "javdb_want", "images"),
        os.path.join(ROOT, "javdb_top250", "images")]
moved = dup = 0
for s in srcs:
    if not os.path.isdir(s):
        continue
    for f in os.listdir(s):
        dst = os.path.join(COVERS, f)
        if os.path.exists(dst):
            dup += 1
            continue
        shutil.move(os.path.join(s, f), dst)
        moved += 1
print(f"covers merged: moved={moved} dup-skipped={dup} total={len(os.listdir(COVERS))}")

# ---------- 2. videos folder ----------
old_v = os.path.join(ROOT, "javdb_videos")
if os.path.isdir(old_v):
    for f in os.listdir(old_v):
        dst = os.path.join(VIDEOS, f)
        if not os.path.exists(dst):
            shutil.move(os.path.join(old_v, f), dst)
print(f"videos: {len(os.listdir(VIDEOS))} files")

# ---------- 3. move data js to root + patch paths + build want.js ----------
def load_win(fname):
    t = io.open(os.path.join(ROOT, "javdb_data", fname), encoding="utf-8").read()
    m = re.search(r"window\.JAVDB_\w+\s*=\s*(\{.*\})\s*;\s*$", t, re.S)
    if not m:
        raise RuntimeError("parse fail " + fname)
    return json.loads(m.group(1)), m.group(0)

def dump_root(fname, obj, banner):
    with io.open(os.path.join(ROOT, fname), "w", encoding="utf-8") as f:
        f.write(f"/* {banner}（自动生成） */\n")
        f.write("window." + ("JAVDB_" + fname.split(".")[0].upper()) + " = ")
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.write(";\n")

movies, raw_movies = load_win("movies.js")
n_local = n_vid = 0
for c in movies.values():
    if c.get("local"):
        c["local"] = c["local"].replace("images/", "covers/", 1)
        n_local += 1
    if c.get("video"):
        c["video"] = "videos/" + c["video"].rsplit("/", 1)[1]
        n_vid += 1
want_codes = sorted([c["code"] for c in movies.values() if c.get("want")])
print(f"movies patched: local={n_local} video={n_vid} | want codes={len(want_codes)}")

dump_root("movies.js", movies, "全量影片静态信息，key=规范化番号code（原JavDB id保留在id字段）")
dump_root("want.js", want_codes, "想看列表：番号code数组")

for f in ("watched.js", "magnets.js", "top250.js"):
    shutil.move(os.path.join(ROOT, "javdb_data", f), os.path.join(ROOT, f))
# watched.js banner mentions id->评分, rewrite comment only
wt = io.open(os.path.join(ROOT, "watched.js"), encoding="utf-8").read()
wt = wt.replace("/* 看过记录：code -> 评分", "/* 看过记录：code -> 评分", 1)
io.open(os.path.join(ROOT, "watched.js"), "w", encoding="utf-8").write(wt)

# ---------- 4. html: copy + rename + patch ----------
def patch_html(src, dst, edits):
    t = io.open(src, encoding="utf-8").read()
    for a, b in edits:
        t = t.replace(a, b)
    io.open(dst, "w", encoding="utf-8").write(t)
    print("written", dst, len(t))

watch_src = os.path.join(ROOT, "javdb_watched", "index.html")
want_src = os.path.join(ROOT, "javdb_want", "index.html")
t250_src = os.path.join(ROOT, "javdb_top250", "index.html")

patch_html(watch_src, os.path.join(ROOT, "watched.html"), [
    ('<script src="../javdb_data/movies.js"></script>', '<script src="movies.js"></script>'),
    ('<script src="../javdb_data/watched.js"></script>', '<script src="watched.js"></script>'),
])
patch_html(want_src, os.path.join(ROOT, "want.html"), [
    ('<script src="../javdb_data/movies.js"></script>', '<script src="movies.js"></script>'),
    ('<script src="../javdb_data/watched.js"></script>', '<script src="want.js"></script>'),
    ('const WREC = window.JAVDB_WATCHED || {};', 'const WANT = window.JAVDB_WANT || [];'),
    ('// 想看列表 = movies.js 中带 want 标记的影片', '// 想看列表 = want.js 番号数组映射 movies.js'),
    ('const DATA = Object.keys(MOVIES).map(id => MOVIES[id]).filter(m => m.want);',
     'const DATA = WANT.map(code => MOVIES[code]).filter(Boolean);'),
    ('请确认 ../javdb_data/movies.js 存在。', '请确认 movies.js 与 want.js 存在。'),
])
patch_html(t250_src, os.path.join(ROOT, "top250.html"), [
    ('<script src="../javdb_data/top250.js"></script>', '<script src="top250.js"></script>'),
    ('src="images/${esc(c.code)}.jpg"', 'src="covers/${esc(c.code)}.jpg"'),
])

# ---------- 5. cleanup old dirs ----------
def rmdir_if_empty(p):
    if os.path.isdir(p) and not os.listdir(p):
        os.rmdir(p)
        return True
    return False

for f in os.listdir(os.path.join(ROOT, "javdb_data")):
    shutil.move(os.path.join(ROOT, "javdb_data", f), os.path.join(BK, f))
removed = []
for d in ("javdb_watched", "javdb_want", "javdb_top250", "javdb_videos", "javdb_data"):
    full = os.path.join(ROOT, d)
    if os.path.isdir(full):
        # may still contain non-empty subfolders? images were moved; check leftover
        leftover = []
        for dp, dns, fns in os.walk(full):
            leftover += [os.path.join(dp, f) for f in fns]
        for f in leftover:
            shutil.move(f, os.path.join(BK, os.path.basename(f)))
        shutil.rmtree(full)
        removed.append(d)
print("removed dirs:", removed)
print("final root js/html:", sorted([f for f in os.listdir(ROOT) if f.endswith(('.js', '.html'))]))
print("backup folder:", len(os.listdir(BK)), "files")
