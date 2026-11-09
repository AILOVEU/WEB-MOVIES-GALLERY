# -*- coding: utf-8 -*-
"""Crawl JavDB user collections: 收藏的演員 (actors) + 收藏的系列 (series),
and every movie under each item (ALL pages).

Phase A : /users/collection_actors?page=N , /users/collection_series?page=N  (probe until empty)
Phase B : /actors/{id}?page=N , /series/{id}?page=N                          (probe until empty)
          actors: total from <span class="section-meta">N 部影片</span>
          series: total from collection list "(N)"
Outputs : actors.js / series.js  (item -> ordered code list)
          movie info merged into movies.js by a separate script (merge_collection.py)
Resume-safe checkpoint + adaptive rate limit.

增量用法（路径已改为跟脚本自身位置走，不再写死机器目录）：
  python collection_spider.py                       # 首次/续跑：Phase A（空才抓）+ Phase B（只跑 done=False）
  python collection_spider.py --refresh-lists       # 只刷新收藏清单，发现新收藏的演員/系列（~8 请求）
  python collection_spider.py --recheck             # 复查已收藏的条目：第 1 页无新增就跳过；有新增才继续翻，连续 2 页无新增即收
  python collection_spider.py --refresh-lists --recheck   # 常规增量同步的组合拳
  python collection_spider.py --new-only                  # 只抓本轮新发现的条目（不碰历史欠账）
写盘统一直接写目标文件（不用 tmp+os.replace，否则会被本机沙盒当危险操作中断，见 RESUME 第 5 节坑 1）。
"""
# __JDB_BOOT__ 统一 ROOT/sys.path 引导：脚本可从 _script 任意子目录运行、跨目录彼此 import
import os as _os, sys as _sys
ROOT = _os.path.dirname(_os.path.abspath(__file__))
while not _os.path.isdir(_os.path.join(ROOT, "data")) and ROOT != _os.path.dirname(ROOT):
    ROOT = _os.path.dirname(ROOT)
SR = _os.path.join(ROOT, "_script")
for _d in [SR] + [_os.path.join(SR, _x) for _x in _os.listdir(SR)
               if _os.path.isdir(_os.path.join(SR, _x)) and _x not in ("legacy", "__pycache__") and not _x.startswith(".")]:
    if _d not in _sys.path:
        _sys.path.insert(0, _d)
# __JDB_BOOT_END__
import argparse
import json, io, os, re, subprocess, time, random, html as htmllib, datetime, math, sys

import win_utf8        # noqa: F401  重定向/管道下固定 UTF-8，否则 ✅ 会砸死进程
DATA = os.path.join(ROOT, "data")                        # 站点数据与名单
SITE = os.path.join(DATA, "site")    # 页面要读的 *.js（HTML 写死引用）
STATE = os.path.join(DATA, "state")  # 抓取状态：清单 / 终态名单 / 校验基准
ASSETS = os.path.join(ROOT, "assets")                        # 资源目录
WB = os.path.join(ROOT, "_pipeline")     # 过程中文件：断点 / cookie / 临时切片（删了会丢进度）
LOGS = os.path.join(ROOT, "_logs")      # 日志与验证截图：随时可删，不影响任何数据
CKPT = os.path.join(WB, "collection_ckpt.json")
LOG  = os.path.join(LOGS, time.strftime('%Y-%m-%d') + "_collection_log.txt")  # 强制日期前缀
os.makedirs(LOGS, exist_ok=True)     # _logs/ 是可以随手删空的，脚本要能自己重建
CK   = io.open(os.path.join(WB, "javdb_cookie.txt"), encoding="utf-8").read().strip()
import atomic_io          # noqa: E402  抗撕裂写入（沙盒禁 rename，硬杀会撕碎直接覆写的文件）
UA   = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"
PAGE_SIZE = 40


def log(msg):
    line = f"[{datetime.datetime.now():%H:%M:%S}] {msg}"
    print(line, flush=True)
    with io.open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def curl(url, timeout=45):
    r = subprocess.run(["curl", "-s", "--ssl-no-revoke", "--max-time", str(timeout),
                        "-x", "http://127.0.0.1:7890", "-A", UA, "-H", f"Cookie: {CK}",
                        "-H", "Referer: https://javdb.com/",
                        "-H", "Accept-Language: zh-TW,zh;q=0.9,en;q=0.8", url],
                       capture_output=True, text=True, encoding="utf-8", errors="ignore")
    return r.stdout or ""


def norm(c):
    return re.sub(r"\s+", " ", (c or "").strip()).upper()


def strip_code_prefix(code, title):
    """JavDB 少数条目的 title 属性自带番号（如 "SDSS-661 本体…"），卡片 tooltip 会番号重复；
    解析时就剔掉，不然后面洗了数据下次抓取又被 incoming 覆盖回来。"""
    t, c = (title or "").strip(), (code or "").strip()
    if c and t.upper().startswith(c.upper()):
        rest = t[len(c):].lstrip(" ：:｜|-—\t")
        if rest and rest[0] not in "0123456789":
            return rest.strip()
    return t


# ---------------- movie list parsers (proven, from rankings_spider.py) ----------------
ITEM  = re.compile(r'<div class="item">\s*<a href="(/v/[^"]+)" class="box" title="([^"]*)">', re.S)
CODE  = re.compile(r"<strong>([^<]+)</strong>")
SCORE = re.compile(r"([\d.]+)分,\s*由(\d+)人評價")
TAGS  = re.compile(r'<span class="tag[^"]*">([^<]+)</span>')
COVER = re.compile(r'src="(https://[^"]+\.j?dbstatic\.com[^"]+\.(?:jpg|png|webp))"', re.I)
META  = re.compile(r'<div class="meta">\s*([\d-]+)\s*</div>')


def parse_movies(html):
    anchors = [(m.start(), m.group(1), m.group(2)) for m in ITEM.finditer(html)]
    out = []
    for i, (s, url, tattr) in enumerate(anchors):
        end = anchors[i + 1][0] if i + 1 < len(anchors) else len(html)
        block = html[s:end]
        cm = CODE.search(block)
        if not cm:
            continue
        code = norm(cm.group(1))
        if not code:
            continue
        sc = SCORE.search(block)
        meta = META.search(block)
        tags = [htmllib.unescape(t.strip()) for t in TAGS.findall(block) if t.strip()]
        cv = COVER.search(block)
        out.append({
            "id": url.rsplit("/", 1)[-1],
            "code": code,
            "title": strip_code_prefix(code, htmllib.unescape(tattr).strip()),
            "url": "https://javdb.com" + url,
            "img": cv.group(1) if cv else "",
            "score": float(sc.group(1)) if sc else None,
            "votes": int(sc.group(2)) if sc else None,
            "tags": tags,
            "date": meta.group(1) if meta else "",
        })
    return out


# ---------------- collection list parsers ----------------
ACTOR_BOX = re.compile(
    r'<div class="box actor-box" id="actor-([^"]+)".*?'
    r'<a href="/actors/([^"]+)" title="([^"]*)">.*?'
    r'<img class="avatar" src="([^"]+)"[^>]*>.*?'
    r"<strong>([^<]*)</strong>", re.S)
SERIES_BOX = re.compile(
    r'<div class="box" id="series-([^"]+)".*?'
    r'<a title="([^"]*)" href="/series/([^"]+)">.*?'
    r"<strong>([^<]*)</strong><span>\((\d+)\)</span>", re.S)
ACTOR_ID  = re.compile(r'<div class="box actor-box" id="actor-([^"]+)"')
SERIES_ID = re.compile(r'<div class="box" id="series-([^"]+)"')
TOTAL_MOVIE = re.compile(r'<span class="section-meta">\s*([\d,]+)\s*部影片')


def parse_collection(html, kind):
    items, seen = [], set()
    if kind == "actor":
        for bid, href, title, avatar, name in ACTOR_BOX.findall(html):
            if bid in seen:
                continue
            seen.add(bid)
            items.append({"id": bid, "name": (name.strip() or title.strip()),
                          "avatar": avatar, "count": 0})
    else:
        for bid, title, href, name, cnt in SERIES_BOX.findall(html):
            if bid in seen:
                continue
            seen.add(bid)
            items.append({"id": bid, "name": htmllib.unescape(name.strip() or title.strip()),
                          "avatar": "", "count": int(cnt)})
    return items


def count_items(html, kind):
    pat = ACTOR_ID if kind == "actor" else SERIES_ID
    return len(set(pat.findall(html)))


# ---------------- checkpoint ----------------
store = {"actors": {}, "series": {}, "movies": {}, "phase": "init"}
_loaded, _from = atomic_io.read_any(CKPT)
if _loaded:
    store.update(_loaded)
    if _from != CKPT:
        log(f"注意：主断点不可用，已从 {_from} 回退恢复")
elif os.path.exists(CKPT):
    log("断点无法解析且无可用槽位副本（将从 _backup 手动恢复后重试）")
log(f"resume: actors={len(store['actors'])} series={len(store['series'])} movies={len(store['movies'])}")


def save():
    # 写槽位 + json 自检 + 复制主文件：被硬杀时永远还有一份完整副本可读
    atomic_io.write_verified(CKPT, json.dumps(store, ensure_ascii=False))


def write_js():
    """actors.js / series.js 一律交给 build_collection_js.rebuild()：字段与 count 口径只有一份定义。
    以前这里自己拼 schema（只写 id/name/avatar/count/codes/done），会把 avatar_local / cover / page_total 冲掉，
    下一次增量同步就静默退回「演员没头像、系列没封面」。"""
    import build_collection_js
    build_collection_js.rebuild(store, quiet=True)


# ---------------- adaptive rate limit ----------------
delay = 2.5
nreq = 0


def pace(dur=None, rate=False):
    global delay, nreq
    nreq += 1
    if rate:
        delay = min(25.0, max(delay * 2.2, 6.0))
        w = random.uniform(60, 120)
        log(f"  rate-limit -> cooldown {w:.0f}s, delay={delay:.1f}s")
        time.sleep(w)
    else:
        if dur and dur > 8:
            delay = min(25.0, delay * 1.6)
        else:
            delay = max(1.5, delay * 0.88)
        time.sleep(random.uniform(delay * 0.8, delay * 1.3))
    if nreq % 150 == 0:
        w = random.uniform(15, 30)
        log(f"  long break {w:.0f}s after {nreq} requests")
        time.sleep(w)


CF_MARK = ("Just a moment", "cf-browser-verification", "Checking if the site connection",
           "Attention Required", "enable JavaScript and cookies")
RATE_MARK = ("Too Many Requests", "稍後再試", "請求過於頻繁", "請稍後再試")


def fetch(url):
    """Return html (may be an empty movie list!) or None on login wall.
    IMPORTANT: a real HTML page with zero movies is a VALID response (end of pagination),
    not a rate-limit - only Cloudflare / throttle pages trigger a cooldown."""
    for a in range(6):
        t0 = time.time()
        h = curl(url)
        dur = time.time() - t0
        if "sign_in" in h[:3000] or (len(h) < 2000 and "登入" in h):
            log("LOGIN WALL - cookie expired, STOP")
            return None
        if any(s in h for s in CF_MARK) or any(s in h for s in RATE_MARK):
            log(f"  DBG cf/rate page url={url} len={len(h)}")
            pace(rate=True)
            continue
        if "</html>" in h and len(h) > 5000:
            pace(dur)
            return h
        # 其它异常（空响应 / 超时截断 / 短页）：先短退避重试，连续失败才升级长冷却
        log(f"  DBG resp try{a} url={url} len={len(h)} dur={dur:.1f}s")
        if a < 2:
            time.sleep(random.uniform(6, 12))
        else:
            pace(rate=True)
    return ""


# ================= PHASE A =================
def key_of(kind):
    return "actors" if kind == "actor" else "series"


def phase_a(refresh=False):
    """刷收藏清单。refresh=False 时保持原行为（已有数据就 skip）；
    refresh=True 时重新翻一遍清单页，**只新增没见过的条目、不动已有条目的 done 标记**。"""
    for kind, url in (("actor", "https://javdb.com/users/collection_actors"),
                      ("series", "https://javdb.com/users/collection_series")):
        key = key_of(kind)
        if store[key] and not refresh:
            log(f"phaseA {key}: already have {len(store[key])}, skip")
            continue
        items, page = [], 1
        while page <= 30:
            u = url if page == 1 else f"{url}?page={page}"
            h = fetch(u)
            if h is None:
                save(); write_js(); sys.exit(2)
            got = parse_collection(h, kind)
            if not got:
                log(f"phaseA {key}: page{page} empty -> last={page-1}")
                break
            items += got
            log(f"phaseA {key} page{page}: +{len(got)}")
            page += 1
        before, seen = len(store[key]), set()
        for it in items:
            if it["id"] in seen:
                continue
            seen.add(it["id"])
            if it["id"] in store[key]:          # 老条目：只刷新名称/头像/影片数，不碰 codes/done
                old = store[key][it["id"]]
                if it["name"]:
                    old["name"] = it["name"]
                if it["avatar"]:
                    old["avatar"] = it["avatar"]
                if it["count"] and not old.get("codes"):
                    old["count"] = it["count"]
                continue
            store[key][it["id"]] = {"name": it["name"], "avatar": it["avatar"],
                                    "count": it["count"], "codes": [], "done": False}
        save()
        log(f"phaseA {key}: 站点清单 {len(seen)} 个 | 本地原 {before} | 新增 {len(store[key]) - before} | 现共 {len(store[key])}")
    write_js()


# ================= PHASE B =================
def crawl_item(kind, iid, it, probe_first_only=False):
    base = f"https://javdb.com/{'actors' if kind == 'actor' else 'series'}/{iid}"
    codes = list(it.get("codes", []))
    seen = set(codes)
    total = it.get("count", 0) or 0
    pages = None
    new_total = 0
    zero_streak = 0
    page = 1
    while page <= 60:
        u = base if page == 1 else f"{base}?page={page}"
        h = fetch(u)
        if h is None:
            return None
        if page == 1:
            tm = TOTAL_MOVIE.search(h)
            if tm:
                total = int(tm.group(1).replace(",", ""))
                it["count"] = total
            # 系列详情页没有 section-meta（已用 _pipeline/samples/sample_series.html 实测），
            # 退回 Phase A 清单里的 (N)；否则 pages 恒为 None，只能靠「空页 / 60 页硬顶」收尾。
            if total:
                pages = max(1, math.ceil(total / PAGE_SIZE))
        got = parse_movies(h)
        if not got:
            break
        new = 0
        for m in got:
            c = m["code"]
            if c not in seen:
                seen.add(c)
                codes.append(c)
                new += 1
            prev = store["movies"].get(c)
            if prev is None:
                store["movies"][c] = m
            else:
                for f2 in ("title", "url", "img", "score", "votes", "date", "tags", "id"):
                    if not prev.get(f2) and m.get(f2):
                        prev[f2] = m[f2]
        new_total += new
        zero_streak = 0 if new else zero_streak + 1
        # 复查模式：第 1 页没新增就说明该条目没上新片，直接收工（每个条目只花 1 个请求）
        if probe_first_only and page == 1 and new == 0:
            break
        # 复查时连续 2 页零新增就收：旧逻辑是「单页 0 新增就停」，会被整页重复番号截断丢后段；
        # 首次/补漏抓取（probe_first_only=False）仍翻满 pages，保证不漏页。
        if probe_first_only and zero_streak >= 2:
            break
        # 不要因为「本页 0 新增」提前收工：某页恰好全是已见过的番号（JavDB 同一作品多版本）时，
        # 提前 break 会把后面的页整段丢掉（2026-11-04 篠田優 少 31 部的教训）。只有真拿到空页才停。
        if pages and page >= pages:
            break
        page += 1
    it["codes"] = codes
    # 抓到 0 部（多半是网络故障/登录墙）不算完成，否则断点续传会永久跳过该条目
    if codes:
        it["done"] = True
    if total:
        it["count"] = total
    it["last_check"] = datetime.date.today().isoformat()
    it["new_found"] = new_total
    return len(codes)


def phase_b(recheck=False, new_only=False):
    """三种互斥选择（别把历史欠账误拖进来）：
    默认          ：只跑 done=False 的条目（首次拓 / 历史欠账会一起上来）
    new_only=True ：只跑本轮 --refresh-lists 新发现的条目（条目标记 new=True），跑完清标记
    recheck=True  ：**只**跑 done=True 的条目做复查（第 1 页差异比对，发现新片才往深翻）"""
    for kind in ("actor", "series"):
        key = key_of(kind)
        if new_only:
            todo = [(i, v) for i, v in store[key].items() if v.get("new") and not v.get("done")]
        elif recheck:
            todo = [(i, v) for i, v in store[key].items() if v.get("done")]
        else:
            todo = [(i, v) for i, v in store[key].items() if not v.get("done")]
        probe_only = recheck
        log(f"phaseB {key}: todo={len(todo)}（recheck={recheck} new_only={new_only}）")
        for n, (iid, it) in enumerate(todo, 1):
            was = len(it.get("codes", []))
            r = crawl_item(kind, iid, it, probe_first_only=probe_only)
            if r is None:
                save(); write_js(); sys.exit(2)
            gained = r - was
            if new_only:
                it.pop("new", None)
            log(f"  {kind} {iid} ({it['name'][:18]}) -> 共 {r} 部，本次新增 {gained} 部  [{n}/{len(todo)}]")
            if n % 2 == 0:
                save()
            if n % 10 == 0:
                write_js()
            if n % 30 == 0:
                log(f"  == {key} progress {n}/{len(todo)} movies_total={len(store['movies'])}")
        save(); write_js()


def main():
    ap = argparse.ArgumentParser(description="JavDB 收藏清单（演員/系列）抓取与增量同步")
    ap.add_argument("--refresh-lists", dest="refresh_lists", action="store_true",
                    help="重翻收藏清单页，发现新收藏的演員/系列（不动已有条目的 done）")
    ap.add_argument("--recheck", dest="recheck", action="store_true",
                    help="只复查 done=True 的条目：第 1 页无新片就过，有新片才继续翻页（不碰未完成的欠账）")
    ap.add_argument("--new-only", dest="new_only", action="store_true",
                    help="只抓本轮 --refresh-lists 新发现的条目（不碰历史未完成的欠账）")
    ap.add_argument("--lists-only", dest="lists_only", action="store_true",
                    help="只跑 Phase A（刷清单），不抓条目下的影片")
    args = ap.parse_args()
    log(f"START collection spider (refresh_lists={args.refresh_lists} recheck={args.recheck} "
        f"lists_only={args.lists_only} new_only={args.new_only})")
    phase_a(refresh=args.refresh_lists)
    if not args.lists_only:
        phase_b(recheck=args.recheck, new_only=args.new_only)
    save()
    write_js()
    log(f"DONE actors={len(store['actors'])} series={len(store['series'])} movies={len(store['movies'])} reqs={nreq}")


if __name__ == "__main__":
    main()
