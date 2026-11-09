# -*- coding: utf-8 -*-
"""Fill magnets for movies.js entries missing from magnets.js.
Anti-scrape: random 5-9s delay, 60-180s cooldown on rate-limit, long breaks every 50,
login-wall detection, resume-safe checkpoint. Output: magnets.js (JAVDB_MAGNETS)."""
import json, io, os, re, subprocess, time, random, html as htmllib, datetime, sys

WB   = r"C:/Users/wh/WorkBuddy/2026-10-10-07-52-17/.workbuddy"
ROOT = r"C:/Users/wh/WorkBuddy/2026-10-10-07-52-17"
CKPT = os.path.join(WB, "magnets_fill_ckpt.json")
LOG  = os.path.join(WB, "magnet_fill_log.txt")
CK   = io.open(os.path.join(WB, "javdb_cookie.txt"), encoding="utf-8").read().strip()
UA   = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

def load_js(path, var):
    txt = io.open(os.path.join(ROOT, path), encoding="utf-8").read()
    i = txt.index(var) + len(var)
    obj, _ = json.JSONDecoder().raw_decode(txt[i:].lstrip())
    return obj

movies = load_js("movies.js", "window.JAVDB_MOVIES = ")
mags   = load_js("magnets.js", "window.JAVDB_MAGNETS = ")

# ---- checkpoint prefill (id or code keyed) ----
store = {}
if os.path.exists(CKPT):
    old = json.load(io.open(CKPT, encoding="utf-8"))
    for k, lst in old.items():
        code = k if k in movies else next((c for c, m in movies.items() if m.get("id") == k), None)
        if code:
            store[code] = lst
print(f"start: movies={len(movies)} have_mags={len([c for c in mags if mags.get(c)])} ckpt_prefill={len(store)}", flush=True)

def log(msg):
    line = f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with io.open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def curl(url, timeout=30):
    r = subprocess.run(["curl", "-s", "--ssl-no-revoke", "--max-time", str(timeout),
                        "-x", "http://127.0.0.1:7890", "-A", UA, "-H", f"Cookie: {CK}",
                        "-H", "Referer: https://javdb.com/",
                        "-H", "Accept-Language: zh-TW,zh;q=0.9,en;q=0.8", url],
                       capture_output=True, text=True, encoding="utf-8", errors="ignore")
    return r.stdout or ""

TAG  = re.compile(r'<span class="tag[^"]*">([^<]+)</span>')
ITEM = re.compile(r'<div class="item[^"]*"\s+data-rank="(\d+)"\s+data-size="(\d+)"\s+data-files="(\d+)"\s+data-date="(\d+)"', re.S)
LINK = re.compile(r'href="(magnet:\?xt=urn:btih:[^"]+)"')
NAME = re.compile(r'<span class="name">([^<]*)</span>')
META = re.compile(r'<span class="meta">\s*([^<]+?)\s*</span>')

def parse(html):
    """Return (status, list): ok / rate / wall / none"""
    if "magnets-content" in html:
        seg = html.split('id="magnets-content"', 1)[1]
        anchors = [(m.start(), m.group(2), m.group(3), m.group(4)) for m in ITEM.finditer(seg)]  # start, size, files, date
        out, seen = [], set()
        for i, (s, size_mb, files, date) in enumerate(anchors):
            end = anchors[i + 1][0] if i + 1 < len(anchors) else len(seg)
            block = seg[s:end]
            lm = LINK.search(block)
            if not lm:
                continue
            link = htmllib.unescape(lm.group(1))
            key = re.search(r'btih:([0-9a-fA-F]+)', link)
            if key and key.group(1).lower() in seen:
                continue
            if key:
                seen.add(key.group(1).lower())
            zone = block.split('<div class="magnet-name">', 1)
            zone = zone[1] if len(zone) > 1 else block
            nm = NAME.search(zone)
            tm = re.search(r'<div class="tags">(.*?)</div>', zone, re.S)
            tags = [t.strip() for t in TAG.findall(tm.group(1)) if t.strip()] if tm else []
            mt = META.search(zone)
            out.append({
                "link": link,
                "name": htmllib.unescape(nm.group(1)).strip() if nm else "",
                "sizeMB": int(size_mb),
                "size": (mt.group(1).split(",")[0].strip() if mt else f"{int(size_mb)}MB"),
                "files": int(files),
                "date": date,
                "tags": tags,
            })
        return "ok", out
    if "sign_in" in html or "登入" in html and "magnets" not in html:
        return "wall", []
    if "429" in html[:400] or "稍後再試" in html or "Try again later" in html or "臨時出現問題" in html:
        return "rate", []
    if len(html) < 2000:
        return "rate", []
    return "ok", []  # page loaded but no magnet section

def save_ckpt():
    tmp = CKPT + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False)
    os.replace(tmp, CKPT)

def save_magnets():
    data = dict(mags)
    for c, lst in store.items():
        if c not in data or not data[c] or len(lst) > len(data[c]):
            data[c] = lst
    tmp = os.path.join(ROOT, "magnets.js.tmp")
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write("/* 磁力链接库：code -> [{link,name,sizeMB,size,files,date,tags}]（自动生成，勿手改） */\n")
        f.write("window.JAVDB_MAGNETS = ")
        f.write(json.dumps(data, ensure_ascii=False, indent=1))
        f.write(";\n")
    os.replace(tmp, os.path.join(ROOT, "magnets.js"))

# ---- task list ----
todo = [c for c in movies if not mags.get(c) and c not in store]
log(f"todo={len(todo)}")

ok_n = empty_n = fail_n = 0
delay = 2.5  # 自适应间隔：连续成功逐步回落至 1.5s，慢响应/限流时抬高（上限 25s）
since_break = 0
i = 0
while i < len(todo):
    code = todo[i]
    mv = movies[code]
    url = mv.get("url") or ("https://javdb.com/v/" + mv.get("id", ""))
    if not mv.get("id"):
        store[code] = []
        save_ckpt(); i += 1; continue
    for attempt in range(6):
        t0 = time.time()
        h = curl(url)
        dur = time.time() - t0
        status, items = parse(h)
        if status == "wall":
            log(f"WALL detected at {code} — cookie expired, STOPPING. Re-login needed.")
            save_ckpt(); save_magnets(); sys.exit(2)
        if status == "rate":
            wait = 60 * (attempt + 1) + random.uniform(0, 20)
            delay = min(25.0, max(delay * 2.2, 6.0))  # 限流后大幅抬高自适应间隔
            log(f"rate-limit at {code} ({i+1}/{len(todo)}), cooldown {wait:.0f}s, delay->{delay:.1f}s")
            time.sleep(wait)
            continue
        if dur > 8:
            delay = min(25.0, delay * 1.6)  # 响应变慢 = 前兆，提前加大间隔
        break
    else:
        log(f"GIVE UP {code} after 6 attempts")
        fail_n += 1
        i += 1
        continue
    if status == "ok":
        store[code] = items
        if items:
            ok_n += 1
        else:
            empty_n += 1
        save_ckpt()
        since_break += 1
        if since_break >= 150:
            save_magnets()
            log(f"progress: {i+1}/{len(todo)} ok={ok_n} empty={empty_n} fail={fail_n} delay={delay:.1f}s — long break")
            since_break = 0
            time.sleep(random.uniform(15, 30))
        elif (i + 1) % 50 == 0:
            log(f"progress: {i+1}/{len(todo)} ok={ok_n} empty={empty_n} fail={fail_n} delay={delay:.1f}s")
        # 连续成功则逐步回落间隔（最低 1.5s）
        delay = max(1.5, delay * 0.88)
    time.sleep(random.uniform(delay * 0.8, delay * 1.3))
    i += 1

save_magnets()
log(f"DONE ok={ok_n} empty={empty_n} fail={fail_n}")
