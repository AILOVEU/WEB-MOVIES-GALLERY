# -*- coding: utf-8 -*-
"""Crawl JavDB TOP250 ranking -> javdb_data/top250.js
window.JAVDB_TOP250 = [{rank,id,code,title,url,score,votes,cover,tags}]"""
import json, io, os, re, subprocess, time, html as htmllib

WB = r"C:\Users\wh\WorkBuddy\2026-10-10-07-52-17\.workbuddy"
ROOT = r"C:\Users\wh\WorkBuddy\2026-10-10-07-52-17"
OUT = os.path.join(ROOT, "javdb_data", "top250.js")
CK = io.open(os.path.join(WB, "javdb_cookie.txt"), encoding="utf-8").read().strip()
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

def curl(url, timeout=25):
    r = subprocess.run(["curl", "-s", "--ssl-no-revoke", "--max-time", str(timeout),
                        "-x", "http://127.0.0.1:7890", "-A", UA, "-H", f"Cookie: {CK}",
                        "-H", "Referer: https://javdb.com/rankings/top", url],
                       capture_output=True, text=True, encoding="utf-8", errors="ignore")
    return r.stdout or ""

ITEM = re.compile(r'<div class="item[^"]*">\s*<a href="(/v/[^"]+)" class="box" title="([^"]*)">', re.S)
RANK = re.compile(r'<span class="ranking">(\d+)</span>')
CODE = re.compile(r'<strong>([^<]+)</strong>')
SCORE = re.compile(r'([\d.]+)分,\s*由(\d+)人評價')
TAGS = re.compile(r'<span class="tag[^"]*">([^<]+)</span>')
COVER = re.compile(r'src="(https://[^"]+\.j?dbstatic\.com[^"]+\.(?:jpg|png|webp))"', re.I)

def parse(html):
    # split strictly by item anchors (same strategy as magnet parser)
    anchors = [(m.start(), m.group(1), m.group(2)) for m in ITEM.finditer(html)]
    items = []
    for i, (s, url, title_attr) in enumerate(anchors):
        end = anchors[i + 1][0] if i + 1 < len(anchors) else len(html)
        block = html[s:end]
        if 'class="ranking"' not in block:
            continue  # regular list item, not ranking
        rank = RANK.search(block)
        code = CODE.search(block)
        sc = SCORE.search(block)
        tags = [t.strip() for t in TAGS.findall(block) if t.strip() and '可播放' not in t]
        tags = [t for t in tags if t.startswith('含') or t in ('無碼破解', '中文', '中字')]
        cv = COVER.search(block)
        vid = url.rsplit('/', 1)[-1]
        items.append({
            "rank": int(rank.group(1)) if rank else None,
            "id": vid,
            "code": code.group(1).strip() if code else "",
            "title": htmllib.unescape(title_attr).strip(),
            "url": "https://javdb.com" + url,
            "score": float(sc.group(1)) if sc else None,
            "votes": int(sc.group(2)) if sc else None,
            "cover": cv.group(1) if cv else "",
            "tags": tags,
        })
    return items

all_items = {}
page = 1
while page <= 20:  # hard cap
    html = curl(f"https://javdb.com/rankings/top?page={page}")
    if page == 1 and "TOP250" not in html:
        print("page 1 error (login/rate limit?)"); break
    got = parse(html)
    print(f"page {page}: {len(got)} items", flush=True)
    if not got:
        break
    for it in got:
        all_items[it["rank"]] = it
    if len(got) < 20:
        break
    page += 1
    time.sleep(2.5)

final = [all_items[k] for k in sorted(all_items) if k]
with io.open(OUT, "w", encoding="utf-8") as f:
    f.write("/* JavDB TOP250 排行榜（自动生成） */\n")
    f.write("window.JAVDB_TOP250 = ")
    f.write(json.dumps(final, ensure_ascii=False, indent=1))
    f.write(";\n")
print(f"DONE: {len(final)} ranked entries -> top250.js", flush=True)
