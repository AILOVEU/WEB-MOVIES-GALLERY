# -*- coding: utf-8 -*-
"""Crawl JavDB rankings incl. TOP250 sub-variants (t= all/0/1/2/3/yYYYY) -> top250.js
Structure: window.JAVDB_RANKINGS = { key: {name, updated, variants: {vk: {name, updated, items}}}}
Resume-safe covers. Rate-limit: cooldown + retry."""
import json, io, os, re, subprocess, time, shutil, html as htmllib, datetime

WB   = r"C:/Users/wh/WorkBuddy/2026-10-10-07-52-17/.workbuddy"
ROOT = r"C:/Users/wh/WorkBuddy/2026-10-10-07-52-17"
OUT  = os.path.join(ROOT, "top250.js")
COV  = os.path.join(ROOT, "covers")
BK   = os.path.join(ROOT, "_backup")
CK   = io.open(os.path.join(WB, "javdb_cookie.txt"), encoding="utf-8").read().strip()
UA   = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
TODAY = datetime.date.today().isoformat()

def curl(url, timeout=25):
    r = subprocess.run(["curl","-s","--ssl-no-revoke","--max-time",str(timeout),
                        "-x","http://127.0.0.1:7890","-A",UA,"-H",f"Cookie: {CK}",
                        "-H","Referer: https://javdb.com/rankings/top",url],
                       capture_output=True, text=True, encoding="utf-8", errors="ignore")
    return r.stdout or ""

def curl_retry(url):
    for a in range(3):
        h = curl(url)
        if h and '<div class="item' in h:
            return h
        if '更新，請耐心等待' in h or not h:
            print(f"  rate-limited, cooldown 45s (try {a+1})", flush=True)
            time.sleep(45)
    return h

UPDATED = re.compile(r'更新於:\s*([0-9-]+)')

ITEM = re.compile(r'<div class="item">\s*<a href="(/v/[^"]+)" class="box" title="([^"]*)">', re.S)
RANK = re.compile(r'<span class="ranking">(\d+)</span>')
CODE = re.compile(r'<strong>([^<]+)</strong>')
SCORE = re.compile(r'([\d.]+)分,\s*由(\d+)人評價')
TAGS = re.compile(r'<span class="tag[^"]*">([^<]+)</span>')
COVER = re.compile(r'src="(https://[^"]+\.j?dbstatic\.com[^"]+\.(?:jpg|png|webp))"', re.I)
META = re.compile(r'<div class="meta">\s*([\d-]+)\s*</div>')

def parse_list(html, with_rank):
    anchors = [(m.start(), m.group(1), m.group(2)) for m in ITEM.finditer(html)]
    items = []
    for i, (s, url, tattr) in enumerate(anchors):
        end = anchors[i+1][0] if i+1 < len(anchors) else len(html)
        block = html[s:end]
        if with_rank and 'class="ranking"' not in block:
            continue
        rank = RANK.search(block)
        code = CODE.search(block)
        sc = SCORE.search(block)
        meta = META.search(block)
        tags = [t.strip() for t in TAGS.findall(block) if t.strip() and '可播放' not in t]
        tags = [t for t in tags if t.startswith('含') or t in ('無碼破解','中文','中字')]
        cv = COVER.search(block)
        items.append({
            "rank": int(rank.group(1)) if rank else len(items)+1,
            "id": url.rsplit('/',1)[-1],
            "code": code.group(1).strip() if code else "",
            "title": htmllib.unescape(tattr).strip(),
            "url": "https://javdb.com"+url,
            "score": float(sc.group(1)) if sc else None,
            "votes": int(sc.group(2)) if sc else None,
            "cover": cv.group(1) if cv else "",
            "tags": tags,
            "date": meta.group(1) if meta else "",
        })
    return items

YEAR = re.compile(r'<h4 class="title is-4 divider-title">(\d{4})</h2>')
AWARD = re.compile(r'<div class="video-title">([^<:]+:[^<]+)</div>')
def parse_fanza(html):
    parts = YEAR.split(html)
    items, seen = [], set()
    for j in range(1, len(parts), 2):
        year, seg = parts[j], parts[j+1]
        for m in ITEM.finditer(seg):
            url, tattr = m.group(1), m.group(2)
            nm = AWARD.search(seg[m.end():m.end()+600])
            award = (year + " " + nm.group(1).strip()) if nm else year
            block = seg[max(0,m.start()-50):m.end()+600]
            code = CODE.search(block); cv = COVER.search(block)
            key = (url, award)
            if key in seen: continue
            seen.add(key)
            items.append({
                "rank": len(items)+1, "id": url.rsplit('/',1)[-1],
                "code": code.group(1).strip() if code else "",
                "title": htmllib.unescape(tattr).strip(),
                "url": "https://javdb.com"+url,
                "score": None, "votes": None,
                "cover": cv.group(1) if cv else "",
                "tags": [], "date": "", "award": award,
            })
    return items

def grab_top_variant(vcode, label):
    """Grab one TOP250 variant, paginated. vcode '' or '0'/'1'/'2'/'3'/'y2024'."""
    got, page, updated = {}, 1, TODAY
    while page <= 20:
        q = f"https://javdb.com/rankings/top?page={page}" + (f"&t={vcode}" if vcode else "")
        h = curl_retry(q)
        um = UPDATED.search(h)
        if um: updated = um.group(1)
        items = parse_list(h, True)
        if not items: break
        for it in items: got[it["rank"]] = it
        if len(items) < 20: break
        page += 1; time.sleep(2.2)
    items = [got[k] for k in sorted(got) if k]
    print(f"  top250[{label or 'all'}]: {len(items)} items, updated {updated}", flush=True)
    return {"name": label or "全部", "updated": updated, "items": items}

# ---- discover top250 variants from the select options ----
base = curl("https://javdb.com/rankings/top")
opt_re = re.compile(r'<option value="([^"]*)"\s*[^>]*>([^<]+)</option>')
sel = re.search(r'<select name="t"[^>]*>(.*?)</select>', base, re.S)
variants = []
if sel:
    for val, lab in opt_re.findall(sel.group(1)):
        if val in ("all","actor","series","maker","director","code","list"): continue
        variants.append((val, lab.strip()))
print(f"top250 variants: {[(v,l) for v,l in variants]}", flush=True)

page = 1
hot_items = {}
while page <= 5:
    h = curl_retry(f"https://javdb.com/rankings/playback?page={page}")
    items = parse_list(h, False)
    if not items: break
    for it in items: hot_items[len(hot_items)+1] = it
    if len(items) < 20: break
    page += 1; time.sleep(2.2)

ranks = {}
ranks["hot"] = {"name": "熱播",
                "variants": {"all": {"name": "全部", "updated": TODAY,
                                     "items": [hot_items[k] for k in sorted(hot_items)]}}}
ranks["top250"] = {"name": "TOP250", "variants": {}}
for vcode, lab in variants:
    ranks["top250"]["variants"][vcode or "all"] = grab_top_variant(vcode, lab)
    time.sleep(1.5)

for key, name, url in [
    ("censored",   "有碼", "https://javdb.com/rankings/movies?p=all&t=censored"),
    ("uncensored", "無碼", "https://javdb.com/rankings/movies?p=all&t=uncensored"),
    ("western",    "歐美", "https://javdb.com/rankings/movies?p=all&t=western"),
    ("fc2",        "FC2", "https://javdb.com/rankings/movies?p=all&t=fc2"),
]:
    items = parse_list(curl_retry(url), False)
    print(f"{key}: {len(items)}", flush=True)
    ranks[key] = {"name": name, "variants": {"all": {"name": "全部", "updated": TODAY, "items": items}}}
    time.sleep(2.0)

fh = curl_retry("https://javdb.com/rankings/fanza_award")
fitems = parse_fanza(fh)
print(f"fanza: {len(fitems)}", flush=True)
ranks["fanza"] = {"name": "FANZA(DMM)成人獎",
                  "variants": {"all": {"name": "全部", "updated": TODAY, "items": fitems}}}

with io.open(OUT, "w", encoding="utf-8") as f:
    f.write("/* JavDB 排行榜合集（自动生成，勿手改） */\n")
    f.write("window.JAVDB_RANKINGS = ")
    f.write(json.dumps(ranks, ensure_ascii=False, indent=1))
    f.write(";\n")
    f.write("window.JAVDB_TOP250 = window.JAVDB_RANKINGS.top250.variants.all.items;\n")
total = sum(len(v["items"]) for r in ranks.values() for v in r["variants"].values())
print(f"DONE {total} entries -> top250.js", flush=True)

# ---- covers ----
need = {}
for r in ranks.values():
    for v in r["variants"].values():
        for it in v["items"]:
            if it["code"] and it["cover"] and it["code"] not in need:
                need[it["code"]] = it["cover"]
missing = []
for code, url in need.items():
    dst = os.path.join(COV, code + ".jpg")
    if os.path.exists(dst) and os.path.getsize(dst) > 5000: continue
    bksrc = os.path.join(BK, code + ".jpg")
    if os.path.exists(bksrc) and os.path.getsize(bksrc) > 5000:
        shutil.copyfile(bksrc, dst); continue
    missing.append((code, url))
print(f"covers: total={len(need)} missing={len(missing)}", flush=True)
io.open(os.path.join(WB, "rank_covers_need.json"), "w", encoding="utf-8").write(
    json.dumps(missing, ensure_ascii=False))
