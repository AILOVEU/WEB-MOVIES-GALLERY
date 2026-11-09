# -*- coding: utf-8 -*-
"""JavDB 个人清单（看过 / 想看）增量同步器 —— 直接更新本项目当前布局的数据文件。

抓什么：
  /users/watched_videos?page=N                     看过：全集 membership + 影片信息
  /users/watched_videos?star=1..5&page=N           看过：**我自己打的星级**（列表页上那个「x分, 由x人評價」
                                                   是大众评分，不是我的；我的评分只能靠 star 过滤器拿）
  /users/want_watch_videos?page=N                  想看：番号列表

写哪里（全部是本项目根目录的现有文件，格式不变）：
  watched.js   window.JAVDB_WATCHED  { code: 我的评分 }          —— 由 star 过滤结果生成
  want.js      window.JAVDB_WANT     [ code, ... ]               —— 规范化大写番号数组
  movies.js    window.JAVDB_MOVIES   { code: {...} }             —— **字段级合并**，保留 local/video/talk 等已有字段
  assets/covers/  assets/covers/{CODE}.jpg                        —— 只为新片补封面（--skip-covers 可跳过）
                                                    判定看 data/state/covers_manifest.json：已经下过但被手动搬走的不会重下

★ 与既有规则的配合 ★
  - 「记录只增不删」：movies.js 里已有的 `video`/`talk`/`local` 字段一律保留；影片从清单里消失时
    **不删** watched.js / want.js 的条目（除非显式 --prune），只是报告出来。
  - 沙盒：所有写盘都直接写目标文件（不 tmp+os.replace，见 RESUME 第 5 节坑 1）；HTML 不落盘，只存解析结果断点。
  - 断点：`_script/users_ckpt.json`（按 页key 存已解析条目 + 页尾标记），中断后重跑自动跳过已抓页。
  - 限速：自适应（起步 2.5s/请求，慢响应加倍、连续快响应回落），命中 Cloudflare/限流页就长冷却。
  - 登录墙：命中 sign_in 直接 rc=2 退出（要重抽 cookie，见 RESUME 第 5 节坑 3）。

用法：
  python users_spider.py                    # 全量同步（看过 membership + 星级 + 想看）
  python users_spider.py --dry-run          # 只报差异，不写任何文件（也不下封面）
  python users_spider.py --skip-covers      # 同步数据但不补封面
  python users_spider.py --only watched     # 只看过（watched|want）
  python users_spider.py --only want
  python users_spider.py --no-stars         # 跳过 star=1..5（老条目评分保持不变，新条目按 0 处理）
  python users_spider.py --prune            # ★会把清单里已消失的番号从 watched.js/want.js 删掉（默认不删）
  python users_spider.py --reset            # 清掉断点重新抓（等于从头再来）
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
import datetime
import html as htmllib
import io
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time

DATA = os.path.join(ROOT, "data")                        # 站点数据与名单
SITE = os.path.join(DATA, "site")    # 页面要读的 *.js（HTML 写死引用）
STATE = os.path.join(DATA, "state")  # 抓取状态：清单 / 终态名单 / 校验基准
ASSETS = os.path.join(ROOT, "assets")                     # 资源目录
WB = os.path.join(ROOT, "_pipeline")     # 过程中文件：断点 / cookie / 临时切片（删了会丢进度）
LOGS = os.path.join(ROOT, "_logs")      # 日志与验证截图：随时可删，不影响任何数据
CKPT = os.path.join(WB, "users_ckpt.json")
LOG = os.path.join(LOGS, time.strftime('%Y-%m-%d') + "_users_sync_log.txt")  # 强制日期前缀
os.makedirs(LOGS, exist_ok=True)     # _logs/ 是可以随手删空的，脚本要能自己重建
COOKIE = os.path.join(WB, "javdb_cookie.txt")
COVERS = os.environ.get("JDB_COVERS_DIR") or os.path.join(ASSETS, "covers")   # 可用环境变量演练“已搬走”
BK = os.path.join(ROOT, "_backup")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"
PROXY = "http://127.0.0.1:7890"
BASE = "https://javdb.com"
MAX_PAGES = 80

CF_MARK = ("Just a moment", "cf-browser-verification", "Checking if the site connection",
           "Attention Required", "enable JavaScript and cookies")
RATE_MARK = ("Too Many Requests", "稍後再試", "請求過於頻繁", "請稍後再試")

# ---- 用户清单页的 item 结构（与 parse_build_ww.py 同源，做了收紧）----
VID = re.compile(r'^([A-Za-z0-9]+)"?\s+data-batch-id', re.S)
HREF = re.compile(r'<a href="(/v/[A-Za-z0-9]+)"')
TITLE_BLOCK = re.compile(r'<div class="video-title">(?:<strong>([^<]*)</strong>)?\s*(.*?)</div>', re.S)
SCORE = re.compile(r'<span class="value">.*?([\d.]+)\s*分,\s*由\s*(\d+)\s*人評價', re.S)
DATE = re.compile(r'<div class="meta">\s*([\d]{4}-[\d]{2}-[\d]{2})')
COVER = re.compile(r'src="(https://[^"]+jdbstatic\.com[^"]+\.(?:jpg|jpeg|png|webp))"', re.I)
TAGS_BLOCK = re.compile(r'<div class="tags[^"]*">(.*?)</div>', re.S)
TAG = re.compile(r'<span class="tag[^"]*">([^<]+)</span>')
# 清单总数线索：`<h1>我看過的影片 (583)</h1>` 这类括号计数，或 section-meta 里的数字
TOTAL_HINT = re.compile(r'(?:<h1[^>]*>.*?<\/h1>|<span class="section-meta">[^<]*)\D{0,12}(\d{2,6})\s*[）)]', re.S)


def log(*a):
    line = f"[{datetime.datetime.now():%H:%M:%S}] " + " ".join(str(x) for x in a)
    print(line, flush=True)
    with io.open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def norm(c):
    return re.sub(r"\s+", " ", (c or "").strip()).upper()


def path_ok(rel):
    """本地文件字段是否指向一个真实存在、体积正常的文件。"""
    return bool(rel) and os.path.exists(os.path.join(ROOT, rel.replace("/", os.sep))) \
        and os.path.getsize(os.path.join(ROOT, rel.replace("/", os.sep))) > 2000


def local_path(rel):
    """把 movies.js 里的相对路径字段（assets/covers|videos|水果派）解成实际路径；
    封面目录受 JDB_COVERS_DIR 覆盖（演练“已搬走”）。"""
    return cm.local_to_path(rel)


def is_valid_cover(rel):
    """封面必须按文件头判（不能用体积，CF 拦截页也有几 KB）。"""
    return bool(rel) and is_valid_image(local_path(rel))


def cover_missing(code, entry):
    """这张封面是不是还**需要**下。
    ★ 目录里有真图片 或 covers_manifest.json 有 valid 记录（=曾下成功，现已搬到别处）都算已有，
      不重下。跟 videos_manifest 同一条规矩，避免「搬走→目录空→当成未下→重复下载」。"""
    return not (is_valid_cover((entry or {}).get("local")) or cm.have(code))


def strip_code_prefix(code, title):
    """剔掉 title 里重复的番号前缀（与 collection_spider.py 同一规则）。"""
    t, c = (title or "").strip(), (code or "").strip()
    if c and t.upper().startswith(c.upper()):
        rest = t[len(c):].lstrip(" ：:｜|-—\t")
        if rest and rest[0] not in "0123456789":
            return rest.strip()
    return t


from covers_check import fetch_cover, is_valid_image   # noqa: E402  统一用真图片校验
import covers_manifest as cm                           # noqa: E402  封面「已下载清单」：搬走后不重下的判定依据


# ---------------- 抓取 ----------------
def load_cookie():
    if not os.path.exists(COOKIE):
        log("缺少 cookie：", COOKIE)
        sys.exit(2)
    return io.open(COOKIE, encoding="utf-8").read().strip()


CK = None
delay = 2.5
nreq = 0


def curl(url, timeout=45):
    r = subprocess.run(["curl", "-s", "--ssl-no-revoke", "--max-time", str(timeout),
                        "-x", PROXY, "-A", UA, "-H", f"Cookie: {CK}",
                        "-H", f"Referer: {BASE}/",
                        "-H", "Accept-Language: zh-TW,zh;q=0.9,en;q=0.8", url],
                       capture_output=True, text=True, encoding="utf-8", errors="ignore")
    return r.stdout or ""


def pace(dur=None, rate=False):
    global delay, nreq
    nreq += 1
    if rate:
        delay = min(25.0, max(delay * 2.2, 6.0))
        w = random.uniform(60, 120)
        log(f"  限流/CF -> 冷却 {w:.0f}s, delay={delay:.1f}s")
        time.sleep(w)
    else:
        if dur and dur > 8:
            delay = min(25.0, delay * 1.6)
        else:
            delay = max(1.5, delay * 0.88)
        time.sleep(random.uniform(delay * 0.8, delay * 1.3))


def fetch(url):
    """返回完整 HTML；空页（末页）返回 ""；登录墙返回 None。"""
    for a in range(6):
        t0 = time.time()
        h = curl(url)
        dur = time.time() - t0
        if "sign_in" in h[:3000] or (len(h) < 2000 and "登入" in h):
            log("登录墙 —— cookie 失效，停止。请按 RESUME 第 5 节坑 3 重抽 cookie")
            return None
        if any(s in h for s in CF_MARK) or any(s in h for s in RATE_MARK):
            log(f"  命中限流/CF 页 len={len(h)}")
            pace(rate=True)
            continue
        if "</html>" in h and len(h) > 5000:
            pace(dur)
            return h
        log(f"  异常响应 try{a} len={len(h)} dur={dur:.1f}s url={url}")
        if a < 2:
            time.sleep(random.uniform(6, 12))
        else:
            pace(rate=True)
    return ""


# 页头导航里的清单总数：`想看 (880)` / `看過 (583)` —— 用来定页数，不靠“碰到空页”猜
NAV_WANT = re.compile(r'href="/users/want_watch_videos">\s*想看\s*[（(](\d+)[）)]')
NAV_WATCHED = re.compile(r'href="/users/watched_videos">\s*看過\s*[（(](\d+)[）)]')


def site_totals(html):
    w = NAV_WANT.search(html)
    k = NAV_WATCHED.search(html)
    return (int(k.group(1)) if k else 0, int(w.group(1)) if w else 0)


def parse_items(html):
    """/users/*?page=N 的 item -> [movie dict]（顺序即页面顺序）"""
    out = []
    for blk in re.split(r'<div class="item" id="video-', html)[1:]:
        vm = VID.match(blk)
        vid = vm.group(1) if vm else None
        hm = HREF.search(blk)
        if not vid and hm:
            vid = hm.group(1).rsplit("/", 1)[-1]
        if not vid:
            continue
        tm = TITLE_BLOCK.search(blk)
        code = norm(tm.group(1)) if (tm and tm.group(1)) else ""
        sc = SCORE.search(blk)
        dt = DATE.search(blk)
        cv = COVER.search(blk)
        tags = []
        tb = TAGS_BLOCK.search(blk)
        if tb:
            tags = [htmllib.unescape(x.strip()) for x in TAG.findall(tb.group(1)) if x.strip()]
        title = htmllib.unescape(re.sub(r"\s+", " ", tm.group(2) or "")).strip() if tm else ""
        out.append({
            "id": vid,
            "code": code or htmllib.unescape(title)[:20],
            "raw_code": code,
            "title": strip_code_prefix(code, title),
            "url": BASE + (hm.group(1) if hm else f"/v/{vid}"),
            "img": cv.group(1) if cv else "",
            "score": float(sc.group(1)) if sc else None,
            "votes": int(sc.group(2)) if sc else None,
            "date": dt.group(1) if dt else "",
            "tags": tags,
        })
    return out


# ---------------- 断点 ----------------
def load_ckpt():
    if os.path.exists(CKPT):
        try:
            return json.load(io.open(CKPT, encoding="utf-8"))
        except Exception as e:
            log("断点读取失败，重建：", e)
    return {"updated": "", "pages": {}}


def save_ckpt(ck):
    ck["updated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with io.open(CKPT, "w", encoding="utf-8") as f:
        json.dump(ck, f, ensure_ascii=False)


def crawl_list(kind, star=None, ckpt=None, page_size=20):
    """抓完整个清单（页数按页头总数算，不靠“整页无新”提前停，否则会漏掉中间插入的片）。
    返回 (有序 code, [movie], 总数或None, 错误标记)。kind: watched|want"""
    url_base = f"{BASE}/users/watched_videos" if kind == "watched" else f"{BASE}/users/want_watch_videos"
    key = f"{kind}:star{star}" if star else kind
    pages = ckpt["pages"].get(key, {})
    seen = set(pages.get("codes_order", []))
    order = list(pages.get("codes_order", []))
    got = [{"code": k2, **v} for k2, v in pages.get("items", {}).items()]
    page = int(pages.get("next_page", 1))
    total = int(pages.get("total", 0)) or None
    expected = int(pages.get("expected_pages", 0)) or None
    if pages.get("done"):
        log(f"[{key}] 本轮断点已完成（{len(order)} 条），跳过")
        return order, got, total, None
    log(f"[{key}] 从 page{page} 开始抓" + (f"（已知总数 {total}）" if total else ""))
    while page <= MAX_PAGES:
        u = f"{url_base}?star={star}&page={page}" if star else f"{url_base}?page={page}"
        h = fetch(u)
        if h is None:
            return order, got, total, "login"
        if page == 1 and not total:
            t_w, t_a = site_totals(h)
            total = t_w if kind == "watched" else t_a
            if total:
                expected = max(1, -(-total // page_size))      # 向上取整
                log(f"[{key}] 页头总数 {total} -> 应翻 {expected} 页")
        items = parse_items(h)
        if not items:
            log(f"[{key}] page{page} 空 -> 到底")
            break
        new = 0
        for it in items:
            c = norm(it["code"])
            if c and c not in seen:
                seen.add(c)
                order.append(c)
                got.append(it)
                new += 1
        log(f"[{key}] page{page}: {len(items)} 条（计新 {new}）累计 {len(order)}")
        ckpt["pages"][key] = {"codes_order": order, "items": {norm(x["code"]): x for x in got},
                              "next_page": page + 1, "done": False,
                              "total": total or 0, "expected_pages": expected or 0}
        save_ckpt(ckpt)
        page += 1
        if expected and page > expected:
            break
    ckpt["pages"][key] = {"codes_order": order, "items": {norm(x["code"]): x for x in got},
                          "next_page": page + 1, "done": True,
                          "total": total or 0, "expected_pages": expected or 0}
    save_ckpt(ckpt)
    log(f"[{key}] 完成：{len(order)} 条（页头总数 {total}）")
    return order, got, total, None


# ---------------- 本地数据读写 ----------------
def load_win(fname, var):
    t = io.open(os.path.join(SITE, fname), encoding="utf-8").read()
    i = t.index("=", t.index(var)) + 1
    d, _ = json.JSONDecoder().raw_decode(t[i:].lstrip())
    return d, t[:t.index(var)]


def dump_win(fname, head, var, obj, indent=None, separators=None):
    p = os.path.join(SITE, fname)
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(head + var + " = ")
        f.write(json.dumps(obj, ensure_ascii=False, indent=indent, separators=separators))
        f.write(";\n")


def backup(fname):
    dst = os.path.join(BK, datetime.date.today().isoformat(), fname + ".before_users_sync")  # 当日日期子目录
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    src = os.path.join(SITE, fname)
    if os.path.exists(src) and not os.path.exists(dst):
        shutil.copyfile(src, dst)


# ---------------- 封面 ----------------
def download_covers(mv, codes, workers=8):
    """只给缺封面的新片下 assets/covers/{CODE}.jpg（8 线程，直接写目标文件，不用 tmp+replace）。
    「缺不缺」由 cover_missing() 定（目录 or 已下载清单），已搬到别处的封面不会重下。"""
    from concurrent.futures import ThreadPoolExecutor
    cand = [c for c in codes if mv.get(c, {}).get("img")]
    need = [c for c in cand if cover_missing(c, mv.get(c, {}))]
    have = len(cand) - len(need)
    log(f"封面已存在 {have} 张（含凭清单判定已搬走的，不重下）| 封面待补 {len(need)} 张")
    if not need:
        return 0, 0
    ok = [0]
    fail = [0]

    def one(c):
        # 必须按文件头判真伪：封面 CDN 被 CF 拦时会写回 ~4.5KB 的 HTML 拦截页，
        # 只判体积会当成“下载成功”（详 _script/covers_check.py 头注）。
        if fetch_cover(c, mv[c]["img"]):
            ok[0] += 1
            mv[c]["local"] = cm.local_field(c + ".jpg")
            cm.mark_downloaded(c)          # 当场登记：以后文件搬走也不会被当成未下载
        else:
            fail[0] += 1

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(one, need))
    cm.flush()
    log(f"封面完成 {ok[0]} / 失败 {fail[0]}（已落盘 covers_manifest.json）")
    return ok[0], fail[0]


# ---------------- 合并 movies.js（字段级，保留本地字段）----------------
SCALAR = ("id", "code", "title", "url", "img", "score", "votes", "date")


def merge_into_movies(mv, incoming):
    """同字段取最新（本次抓取覆盖）、缺字段增量补、tags 并集；local/video/talk 等一律保留。"""
    added = updated = 0
    for it in incoming:
        c = norm(it["code"])
        if not c:
            continue
        prev = mv.get(c)
        if prev is None:
            e = {k: it[k] for k in SCALAR if it.get(k) is not None and it[k] != ""}
            e["tags"] = list(it.get("tags") or [])
            mv[c] = e
            added += 1
            continue
        changed = False
        for f in SCALAR:
            if f == "code":
                continue
            if it.get(f) not in (None, "") and prev.get(f) != it[f]:
                prev[f] = it[f]
                changed = True
        if not prev.get("code"):
            prev["code"] = c
        merged = list(prev.get("tags") or [])
        for t in (it.get("tags") or []):
            if t not in merged:
                merged.append(t)
                changed = True
        if merged:
            prev["tags"] = merged
        if changed:
            updated += 1
    return added, updated


# ---------------- 主流程 ----------------
def main():
    global CK
    ap = argparse.ArgumentParser(description="JavDB 看过/想看清单增量同步")
    ap.add_argument("--only", default="all", choices=["all", "watched", "want"])
    ap.add_argument("--dry-run", action="store_true", help="只报差异，不写文件不下封面")
    ap.add_argument("--skip-covers", dest="skip_covers", action="store_true")
    ap.add_argument("--no-stars", dest="no_stars", action="store_true", help="跳过 star=1..5（不更新我的评分）")
    ap.add_argument("--prune", action="store_true", help="★把站点清单里已消失的番号从 watched.js/want.js 删除（默认不删）")
    ap.add_argument("--reset", action="store_true", help="清掉断点重新抓")
    args = ap.parse_args()

    CK = load_cookie()
    today = datetime.date.today().isoformat()
    if args.reset and os.path.exists(CKPT):
        os.remove(CKPT)
        log("已清掉断点")
    ckpt = load_ckpt()
    # 断点是“本轮”语义：跳天必须重抓（否则 done 标记会把老快照当成今天的清单，看不出新片）
    if ckpt.get("run") != today:
        log(f"开新一轮（run {ckpt.get('run') or '无'} -> {today}）")
        ckpt = {"run": today, "updated": "", "pages": {}}
        save_ckpt(ckpt)

    watched_now, want_now = set(), set()
    site_total = {}
    star_map = {}
    incoming = []

    if args.only in ("all", "watched"):
        order, items, total, stop = crawl_list("watched", None, ckpt)
        if stop:
            return 2
        watched_now = set(order)
        site_total["watched"] = total
        incoming += items
        if not args.no_stars:
            for s in range(5, 0, -1):
                o2, it2, _t, stop = crawl_list("watched", s, ckpt)
                if stop:
                    return 2
                for c in o2:
                    star_map[c] = s
                incoming += it2
            log(f"我的评分解析：{len(star_map)} 条有星级")
        else:
            log("跳过 star=1..5（--no-stars）")

    if args.only in ("all", "want"):
        order, items, total, stop = crawl_list("want", None, ckpt)
        if stop:
            return 2
        want_now = set(order)
        site_total["want"] = total
        incoming += items

    # ---------- 与本地对账 ----------
    mv, head_m = load_win("movies.js", "window.JAVDB_MOVIES")
    watched_js, head_w = load_win("watched.js", "window.JAVDB_WATCHED")
    want_js, head_t = load_win("want.js", "window.JAVDB_WANT")
    by_code = {norm(it["code"]): it for it in incoming if it.get("code")}

    new_watched = sorted(watched_now - set(watched_js)) if args.only != "want" else []
    gone_watched = sorted(set(watched_js) - watched_now) if args.only != "want" else []
    new_want = sorted(want_now - set(want_js)) if args.only != "watched" else []
    gone_want = sorted(set(want_js) - want_now) if args.only != "watched" else []
    rescored = []
    if star_map:
        for c in watched_now:
            v = int(star_map.get(c, 0) or 0)
            if c in watched_js and watched_js[c] != v:
                rescored.append((c, watched_js[c], v))

    print("=" * 62)
    print(f"看过：站点 {site_total.get('watched', '-')} / 抓到 {len(watched_now)} | 本地 {len(watched_js)} | "
          f"新增 {len(new_watched)} | 已移出清单 {len(gone_watched)}")
    print(f"想看：站点 {site_total.get('want', '-')} / 抓到 {len(want_now)} | 本地 {len(want_js)} | "
          f"新增 {len(new_want)} | 已移出清单 {len(gone_want)}")
    print(f"我的评分变更 {len(rescored)} 条 | 抓到影片信息 {len(by_code)} 条")
    if new_watched[:10]:
        print("  新增看过示例:", ", ".join(new_watched[:10]))
    if new_want[:10]:
        print("  新增想看示例:", ", ".join(new_want[:10]))
    if gone_watched[:10]:
        print("  已从看过清单消失(默认保留本地记录):", ", ".join(gone_watched[:10]))
    if rescored[:10]:
        print("  改分示例:", ", ".join(f"{c}:{a}→{b}" for c, a, b in rescored[:10]))
    print("=" * 62)

    if args.dry_run:
        log("--dry-run：不写文件")
        return 0

    # ---------- 落盘 ----------
    for f in ("movies.js", "watched.js", "want.js"):
        backup(f)

    # 1) 影片信息合并（只处理本次抓到的那些；movies.js 已有的 local/video/talk 一律保留）
    added, updated = merge_into_movies(mv, list(by_code.values()))
    dump_win("movies.js", head_m, "window.JAVDB_MOVIES", mv, separators=(",", ":"))
    log(f"movies.js: {len(mv)} 部（本次新增 {added}、更新 {updated}）")

    def date_key(c):
        return mv.get(c, {}).get("date") or ""

    # 2) watched.js：code -> 我的星级（0 = 真的没打星）。
    #    star=1..5 没命中的就是未打星，**不能沿用旧值**（旧值是大众评分，语义不同）。
    #    已移出清单的条目默认保留（记录只增不删）。
    if args.only != "want":
        w = dict(watched_js)
        if star_map:
            for c in watched_now:
                w[c] = int(star_map.get(c, 0) or 0)
        elif args.no_stars:
            for c in watched_now:
                w.setdefault(c, 0)       # --no-stars：只补新条目，不动老值
        if args.prune:
            for c in gone_watched:
                w.pop(c, None)
        n0 = sum(1 for c in watched_now if not w.get(c))
        ordered = sorted(w, key=lambda c: (date_key(c), c), reverse=True)
        dump_win("watched.js", head_w, "window.JAVDB_WATCHED", {c: w[c] for c in ordered}, indent=1)
        log(f"watched.js: {len(w)} 条（其中未打星 0 分 {n0} 条；已移出清单但保留 {len(gone_watched)} 条）"
            if not args.prune else f"watched.js: {len(w)} 条（--prune 已删 {len(gone_watched)} 条）")

    # 3) want.js：规范化 code 数组，按发行日期倒序（老条目不删）
    if args.only != "watched":
        want_set = set(want_now) if args.prune else (set(want_js) | want_now)
        dated = sorted(want_set, key=lambda c: (date_key(c), c), reverse=True)
        dump_win("want.js", head_t, "window.JAVDB_WANT", dated, indent=1)
        log(f"want.js: {len(dated)} 条（站点 {len(want_now)}；保留本地已有且不在站点的 "
            f"{len(set(want_js) - want_now)} 条）" if not args.prune else f"want.js: {len(dated)} 条（--prune）")

    # 4) 封面：只给「看过/想看里本地封面确实缺失」的片子补（清单有记录=已下过，不重下）
    if not args.skip_covers:
        need = [c for c in (watched_now | want_now) if c in mv and cover_missing(c, mv[c])]
        download_covers(mv, need)
        dump_win("movies.js", head_m, "window.JAVDB_MOVIES", mv, separators=(",", ":"))
        log("movies.js 已回写封面字段")

    log(f"完成。请求 {nreq} 次。")
    log("提示：接着跑 python _script/merge_collection.py 与 _script/build_collection_js.py（若本次也更新了收藏清单），"
        "再跑 python _script/media_scan.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
