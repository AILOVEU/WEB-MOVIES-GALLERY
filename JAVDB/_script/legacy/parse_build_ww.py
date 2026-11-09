# -*- coding: utf-8 -*-
"""Parse javdb want_watch pages -> javdb_want folder"""
import re, io, os, json, subprocess, glob, datetime

WS = r"C:\Users\wh\WorkBuddy\2026-10-10-07-52-17"
OUT = os.path.join(WS, "javdb_want")
IMG = os.path.join(OUT, "images")
os.makedirs(IMG, exist_ok=True)

items, seen = [], set()
for path in sorted(glob.glob(os.path.join(WS, ".workbuddy", "javdb_ww_p*.html")),
                   key=lambda p: int(re.search(r'_p(\d+)\.html', p).group(1))):
    html = io.open(path, encoding="utf-8").read()
    parts = re.split(r'<div class="item" id="video-', html)[1:]
    for p in parts:
        vid = p.split('"', 1)[0]
        if vid in seen:
            continue
        seen.add(vid)
        href = re.search(r'<a href="(/v/[A-Za-z0-9]+)"', p)
        img = re.search(r'<img src="([^"]+)"', p)
        title = re.search(r'<div class="video-title">(?:<strong>([^<]*)</strong>)?\s*(.*?)</div>', p, re.S)
        score = re.search(r'<span class="value">.*?(\d+(?:\.\d+)?)\s*分,\s*由(\d+)人評價', p, re.S)
        date = re.search(r'<div class="meta">\s*([\d-]+)', p)
        tags = re.findall(r'<span class="tag[^"]*">([^<]+)</span>', p)
        code = title.group(1).strip() if title and title.group(1) else vid
        items.append({
            "id": vid,
            "code": code,
            "title": re.sub(r'\s+', ' ', title.group(2)).strip() if title else '',
            "url": "https://javdb.com" + (href.group(1) if href else "/v/" + vid),
            "img": img.group(1) if img else "",
            "score": float(score.group(1)) if score else None,
            "votes": int(score.group(2)) if score else None,
            "date": date.group(1) if date else "",
            "tags": [t.strip() for t in tags],
        })

items.sort(key=lambda x: x["date"], reverse=True)
print("parsed items:", len(items))

ok, fail = 0, []
for it in items:
    if not it["img"]:
        it["local"] = ""
        continue
    fname = it["code"].replace("/", "_") + os.path.splitext(it["img"])[1]
    dest = os.path.join(IMG, fname)
    it["local"] = "images/" + fname
    if os.path.exists(dest) and os.path.getsize(dest) > 1000:
        ok += 1
        continue
    r = subprocess.run(["curl", "-s", "--ssl-no-revoke", "--max-time", "40",
                        "-x", "http://127.0.0.1:7890",
                        "-A", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0",
                        "-o", dest, it["img"]])
    if r.returncode == 0 and os.path.exists(dest) and os.path.getsize(dest) > 1000:
        ok += 1
    else:
        fail.append(it["code"])
print("images ok:", ok, "fail:", len(fail), fail[:10])

cfg = {
    "title": "JavDB 想看的影片",
    "source": "https://javdb.com/users/want_watch_videos",
    "updated": datetime.date.today().isoformat(),
    "videos": items,
}
with io.open(os.path.join(OUT, "config.js"), "w", encoding="utf-8") as f:
    f.write("// 瀑布流卡片配置文件 —— 更新数据后改这里即可，保存后刷新页面生效\n")
    f.write("// 每条: code番号 / title标题 / url详情页 / local本地图 / score评分(满分5) / votes评分人数 / date发行日期 / tags标签\n")
    f.write("window.SITE_CONFIG = " + json.dumps(cfg, ensure_ascii=False, indent=2) + ";\n")
print("config.js written,", len(items), "videos")
