# -*- coding: utf-8 -*-
"""抓 JavDB 影片详情页的磁力链接 -> data/site/magnets.js（断点 _pipeline/magnets.json）。

输出格式（不变）：window.JAVDB_MAGNETS = { code: [{link,name,sizeMB,size,files,date,tags}] }
断点：以 javdb 内部 id 为 key；落盘时经 movies.js 的 id→code 映射转成规范化 code。

★★ 这版重写解决的三个老 bug ★★
  1. 路径写死老机器目录（`C:\\Users\\wh\\WorkBuddy\\...`、`javdb_data/`）→ 现在全部按脚本自身位置走。
  2. 旧版用 `split("=", 1)[1]` 解析 movies.js，而 movies.js 顶部注释里就含 `=` → 直接炸。
     现在统一用 `window.JAVDB_MOVIES = ` 定位 + raw_decode。
  3. **旧版 save_all() 只从断点重建**：断点只有 1452 条、magnets.js 实有 6097 条，
     一跑就把磁力库缩回 1452。现在启动时先把现有 magnets.js 灌回 store，落盘再与旧文件合并，**只增不减**。

用法：
  python magnet_spider.py                        # 默认：补 magnets 里还缺的所有番号（scope=missing）
  python magnet_spider.py --scope watched        # 只补「看过」清单里的（watched|want|all|new|missing）
  python magnet_spider.py --max 500              # 本轮最多抓 500 部（分批跑用）
  python magnet_spider.py --workers 3            # 并发 3（默认 2；代理扛不住就降 1）
  python magnet_spider.py --dry-run              # 只看要抓多少，不发请求
安全：走代理 + cookie（RESUME 第 5 节坑 2/3）；命中登录墙 rc=2 退出；写盘直接写目标文件。
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
import html as htmllib
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import win_utf8        # noqa: F401  重定向/管道下固定 UTF-8，否则 ✅ 会砸死进程
DATA = os.path.join(ROOT, "data")                        # 站点数据与名单
SITE = os.path.join(DATA, "site")    # 页面要读的 *.js（HTML 写死引用）
STATE = os.path.join(DATA, "state")  # 抓取状态：清单 / 终态名单 / 校验基准
ASSETS = os.path.join(ROOT, "assets")                       # 资源目录
WB = os.path.join(ROOT, "_pipeline")     # 过程中文件：断点 / cookie / 临时切片（删了会丢进度）
LOGS = os.path.join(ROOT, "_logs")      # 日志与验证截图：随时可删，不影响任何数据
CKPT = os.path.join(WB, "magnets.json")
LOG = os.path.join(LOGS, time.strftime('%Y-%m-%d') + "_magnets_run.log")  # 强制日期前缀
os.makedirs(LOGS, exist_ok=True)     # _logs/ 是可以随手删空的，脚本要能自己重建
COOKIE = os.path.join(WB, "javdb_cookie.txt")
MOVIES_JS = os.path.join(SITE, "movies.js")
import atomic_io          # noqa: E402  抗撕裂写入（沙盒禁 rename，硬杀会撕碎直接覆写的文件）
from magnets_compact import normalize as _normalize_mag   # noqa: E402  写盘前安全瘦身（去 name==番号/tags去重/删空字段；link/size 原样保留）
from magnet_buckets import rebuild as _rebuild_buckets   # noqa: E402  整轮结束重建 16 分桶 data/site/mag/*.js
import javbus_magnet as _JB   # noqa: E402  磁力源：javbus.com（2026-11-24 起替代 javdb）
WATCHED_JS = os.path.join(SITE, "watched.js")
WANT_JS = os.path.join(SITE, "want.js")
OUT = os.path.join(ROOT, "data", "state", "magnets.js")   # 源文件移出站点目录；前端只按需加载 data/site/mag/*.js
os.makedirs(os.path.dirname(OUT), exist_ok=True)
# 樣品圖像（剧照）地址库：code -> [url, ...]。只记远程地址，**不下载**（用户 2026-11-25 决定），
# 弹窗直接引用 DMM 原图地址。与 magnets.js 分开存，互不影响。
SAMPLES_OUT = os.path.join(ROOT, "data", "state", "samples.js")
SAMPLES_CKPT = os.path.join(WB, "samples.json")
SMP_VAR = "window.JAVDB_SAMPLES"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"
PROXY = "http://127.0.0.1:7890"
MV_VAR = "window.JAVDB_MOVIES = "
MAG_VAR = "window.JAVDB_MAGNETS"

TAG = re.compile(r'<span class="tag[^"]*">([^<]+)</span>')
ITEM = re.compile(r'<div class="item[^"]*"\s+data-rank="\d+"\s+data-size="(\d+)"\s+data-files="(\d+)"\s+data-date="(\d+)">')
LINK = re.compile(r'href="(magnet:\?xt=urn:btih:[^"]+)"')
NAME = re.compile(r'<span class="name">([^<]*)</span>')
META = re.compile(r'<span class="meta">\s*([^<]+?)\s*</span>')

_lock = threading.Lock()
CK = ""
stop_flag = threading.Event()


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with _lock:
        with io.open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def load_js(path, var):
    """按 `window.X` 定位后取 JSON。★ 不能用 split("=",1)：顶部注释里就有 `=`，
    而且 magnet 链接里带 `=`，找下一个 `=` 会解出错的东西。"""
    t = io.open(path, encoding="utf-8").read()
    i = t.index(var) + len(var)
    rest = t[i:].lstrip()
    if rest.startswith("="):                     # var 末尾没写 "=" 的情况（如 window.JAVDB_MAGNETS）
        rest = rest[1:].lstrip()
    d, _ = json.JSONDecoder().raw_decode(rest)
    return d, t[:t.index(var)]


# ---------------- 磁力页解析（沿用已验证过的规则）----------------
def parse(html):
    if "magnets-content" not in html:
        return None                            # 页面异常 / 登录墙
    seg = html.split('id="magnets-content"', 1)[1]
    anchors = [(m.start(), m.group(1), m.group(2), m.group(3)) for m in ITEM.finditer(seg)]
    if not anchors:
        return []
    out = []
    for i, (s, size_mb, files, date) in enumerate(anchors):
        end = anchors[i + 1][0] if i + 1 < len(anchors) else len(seg)
        block = seg[s:end]
        lm = LINK.search(block)
        if not lm:
            continue
        zone = block.split('<div class="magnet-name">', 1)
        zone = zone[1] if len(zone) > 1 else block
        nm = NAME.search(zone)
        tm = re.search(r'<div class="tags">(.*?)</div>', zone, re.S)
        tags = [t.strip() for t in TAG.findall(tm.group(1)) if t.strip()] if tm else []
        mt = META.search(zone)
        out.append({
            "link": htmllib.unescape(lm.group(1)),
            "name": htmllib.unescape(nm.group(1)).strip() if nm else "",
            "sizeMB": int(size_mb),
            "size": (mt.group(1).split(",")[0].strip() if mt else f"{int(size_mb)}MB"),
            "files": int(files),
            "date": date,
            "tags": tags,
        })
    return out


def curl(url, timeout=30):
    r = subprocess.run(["curl", "-s", "--ssl-no-revoke", "--max-time", str(timeout),
                        "-x", PROXY, "-A", UA, "-H", f"Cookie: {CK}",
                        "-H", "Referer: https://javdb.com/", url],
                       capture_output=True, text=True, encoding="utf-8", errors="ignore")
    return r.stdout or ""


def fetch_magnets(vid):
    """返回 (list 或 None)。None = 抓不到（限流/网络），登录墙会置 stop_flag。"""
    for attempt in range(4):
        if stop_flag.is_set():
            return None
        page = curl(f"https://javdb.com/v/{vid}")
        if "sign_in" in page[:3000]:
            log("登录墙 —— cookie 失效，停止")
            stop_flag.set()
            return None
        if "magnets-content" in page or "找不到影片" in page or "not found" in page.lower():
            got = parse(page)
            return [] if got is None else got
        w = 20 + attempt * 15
        log(f"  {vid} 疑似限流，冷却 {w}s（第 {attempt + 1} 次）")
        time.sleep(w)
    return None


def fetch_magnets_javbus(code):
    """从 javbus.com 抓磁力 + 剧照地址（2026-11-24 起的主力源）。

    为什么换源：javdb 现在连 top250 都要登录，cookie 三天失效、CF 质询频繁；
    javbus 只要过年龄验证就稳定，磁力条数还更多（SSIS-001：43 条 vs 1 条）。
    返回 (magnets, samples) 或 None（None = 本轮抓取失败，下轮重试）。
    """
    try:
        return _JB.fetch_detail(code)
    except Exception as e:
        log(f"  {code} javbus 异常：{e}")
        return None


# ---------------- 断点与落盘 ----------------
def load_store(mv, id2code):
    """store: id -> [magnets]。合并两个来源：断点 magnets.json + 现有 magnets.js（防缩水）。"""
    store, _from = atomic_io.read_any(CKPT)
    store = store or {}
    if _from and _from != CKPT:
        log(f"断点主文件不可用，已从 {_from} 回退恢复")
    n_ckpt = len(store)
    if os.path.exists(OUT):
        try:
            cur, _ = load_js(OUT, MAG_VAR)
        except Exception as e:
            cur = {}
            log("现有 magnets.js 读取失败：", e)
        code2id = {v.get("code") or k: k for k, v in mv.items()}
        n_fromjs = 0
        for code, lst in (cur or {}).items():
            key = None
            for cand in (code, code.upper()):
                if cand in code2id and code2id[cand] in mv:
                    key = code2id[cand]
                    break
            key = key or code                       # 对不上 movies.js 就按 code 存（save_all 会原样输出）
            if key not in store or len(store.get(key, [])) < len(lst or []):
                if key not in store:
                    n_fromjs += 1
                store[key] = lst or []
        log(f"载入断点 {n_ckpt} 条 + 现有 magnets.js 补充 {n_fromjs} 条 -> store {len(store)}")
    else:
        log(f"载入断点 {n_ckpt} 条（magnets.js 不存在，稍后生成）")
    return store


def save_all(store, id2code, mv, cap=0):
    """落盘：以规范化 code 为 key，并与现有 magnets.js **合并**（只增不减）。
    ★ 输出用紧凑格式（不缩进）：magnets.js 已到几十 MB 量级，indent=1 会多占 ~35% 体积。
    ★ 先对 store 做快照：本函数在锁外调用，而 worker 线程仍在往 store 里写，
      直接遍历会抛 "dictionary changed size during iteration"（2026-11-25 实测崩溃）。
    """
    snap_store = dict(store)
    data = {}
    if os.path.exists(OUT):
        try:
            data, _ = load_js(OUT, MAG_VAR)
        except Exception:
            data = {}
    for k, lst in snap_store.items():
        code = id2code.get(k, k)
        if not lst:
            data.setdefault(code, [])
            continue
        if len(lst) >= len(data.get(code) or []):
            data[code] = lst
    if cap:
        # 每片最多留 cap 条（按高清标签/体积倒序，与前端排序规则一致），控制文件体积
        for code, lst in data.items():
            if len(lst) > cap:
                hd = lambda m: 1 if "高清" in (m.get("tags") or []) else 0
                data[code] = sorted(lst, key=lambda m: (hd(m), m.get("sizeMB") or 0), reverse=True)[:cap]
    data = _normalize_mag(data)   # 与 magnets.js 就地压缩器同一套规则，防回退成 pretty
    head = "/* 磁力链接库：code -> [{link,name,sizeMB,size,files,date,tags}]（自动生成，紧凑格式，已安全瘦身） */\n"
    atomic_io.write_verified(OUT, head + MAG_VAR + " = "
                             + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n",
                             verify=False)                       # 带 JS 头尾，不做 json 自检
    atomic_io.write_verified(CKPT, json.dumps(store, ensure_ascii=False))
    return data


def load_samples():
    """读剧照地址库（code -> [url]）。不存在返回空。"""
    if not os.path.exists(SAMPLES_CKPT):
        return {}
    try:
        return json.load(io.open(SAMPLES_CKPT, encoding="utf-8")) or {}
    except Exception as e:
        log("剧照断点读取失败（按空处理）：", e)
        return {}


def save_samples(smp):
    """落盘剧照地址库：state/samples.js（给前端）+ _pipeline/samples.json（断点）。
    ★ 同样先快照：锁外调用时 worker 仍在写 smp。"""
    snap = dict(smp)
    if not snap:
        return
    body = ("/* 樣品圖像地址库：code -> [原图URL]（只记地址，不下载；弹窗直接引用） */\n"
            + SMP_VAR + " = " + json.dumps(snap, ensure_ascii=False, separators=(",", ":")) + ";\n")
    atomic_io.write_verified(SAMPLES_OUT, body, verify=False)
    atomic_io.write_verified(SAMPLES_CKPT, json.dumps(snap, ensure_ascii=False))


# ---------------- javbus 侧「无预告」记录（与 javdb 的严格分开）----------------
# ★ 为什么必须分开（用户 2026-11-25 明确要求）：
#   javdb 的 videos_no_preview.json 记的是「javdb 的 m3u8 返回 404」——那是 javdb 这个源没有，
#   不代表 javbus 也没有；反过来 javbus 没有也不代表 javdb 没有。两个源判定标准不同、覆盖范围不同，
#   混在一张表里会互相误跳过：javdb 记了「无」，javbus 就永远不再查；javbus 记了「无」，
#   javdb 的下载器也会跳过。所以各自一张表、各自一套字段。
#   本文件只由 magnet_spider.py（javbus 源）写，trailers_download.py 一律不读不写。
NPJ_OUT = os.path.join(ROOT, "data", "state", "javbus_no_preview.json")
NPJ_CKPT = os.path.join(WB, "javbus_no_preview.json")
NPJ_VAR = "window.JAVBUS_NO_PREVIEW"
NPJ_DESC = ("javbus 源确认无预告片的番号记录（详情页无 sample 视频入口 / 无 gid）。"
            "★ 与 javdb 的 videos_no_preview.json 完全独立，两者不可互相推断、不可合并。")


def load_npj():
    """读 javbus 无预告记录：{code: {code, reason, first_seen, last_seen, source}}。"""
    if not os.path.exists(NPJ_CKPT):
        return {}
    try:
        d = json.load(io.open(NPJ_CKPT, encoding="utf-8"))
        return (d.get("items") or {}) if isinstance(d, dict) else {}
    except Exception as e:
        log("javbus 无预告记录读取失败（按空处理）：", e)
        return {}


def save_npj(items):
    """落盘 javbus 无预告记录。source 字段固定 'javbus'，便于与 javdb 记录区分。"""
    snap = dict(items)
    body = ("/* javbus 源无预告记录：code -> {reason,source}（与 javdb 的 videos_no_preview.json 无关） */\n"
            + NPJ_VAR + " = " + json.dumps(snap, ensure_ascii=False, separators=(",", ":")) + ";\n")
    atomic_io.write_verified(NPJ_OUT, body, verify=False)
    payload = {
        "desc": NPJ_DESC,
        "source": "javbus",
        "version": 1,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(snap),
        "items": snap,
    }
    atomic_io.write_verified(NPJ_CKPT, json.dumps(payload, ensure_ascii=False))


def record_npj(items, code, reason="no_sample_video"):
    """登记一条 javbus 无预告记录（幂等：已存在只刷新 last_seen）。"""
    now = time.strftime("%Y-%m-%d")
    old = items.get(code)
    if old:
        old["last_seen"] = now
        return False
    items[code] = {
        "code": code,
        "reason": reason,
        "source": "javbus",       # ★ 来源标记，防止与 javdb 记录混淆
        "first_seen": now,
        "last_seen": now,
    }
    return True


# ---------------- 目标选择 ----------------
def pick_targets(mv, store, scope, recheck_npj=False, npj=None):
    """挑本轮要抓的番号。

    优先级（用户 2026-11-24 要求）：**先补没有磁力的，再补有磁力的**。
      第 1 档 no_magnet：store 里完全没有记录，或记录为空列表（确认无磁力/从未抓过）
      第 2 档 has_magnet：已有磁力，但本轮 --refresh 时才重抓（补新种子）

    ★ 无预告表跳过（2026-11-26 修）：javbus 无预告表里的番号上一轮已确认过
      「javbus 详情页没有磁力/没有视频入口」，默认不再重复抓。否则补跑时会把
      1.8 万个已确认无磁力的番号再刷一遍，纯浪费还重复写表。
      只有 --recheck-npj 才重查这些（怀疑源站后来补了内容时用）。
    返回 [(vid, code)]，按档位排序；同档内按 movies.js 顺序（新片在前）。
    """
    if scope == "watched":
        codes = list(load_js(WATCHED_JS, "window.JAVDB_WATCHED")[0])
    elif scope == "want":
        codes = load_js(WANT_JS, "window.JAVDB_WANT")[0]
    else:
        codes = list(mv)

    npj = npj or {}
    skip_npj = 0
    no_magnet, has_magnet = [], []
    seen = set()
    for c in codes:
        e = mv.get(c)
        if not e or not e.get("id"):
            continue
        vid = e["id"]
        if vid in seen:
            continue
        seen.add(vid)
        cur = store.get(vid)
        if cur is None:
            cur = store.get(c)
        if cur:                       # 已有非空磁力
            has_magnet.append((vid, c))
        else:                         # 没有 / 空列表
            if c in npj and not recheck_npj:
                skip_npj += 1         # 上轮已确认无磁力，跳过
                continue
            no_magnet.append((vid, c))

    log(f"目标选择：跳过 javbus 无预告表 {skip_npj} 条"
        + ("（--recheck-npj 已开，未跳过）" if recheck_npj else ""))
    if scope == "all":
        return no_magnet + has_magnet
    return no_magnet + (has_magnet if scope == "refresh" else [])


def main():
    global CK
    ap = argparse.ArgumentParser(description="磁力链接抓取（javbus 源，断点续传，只增不减）")
    ap.add_argument("--scope", default="missing",
                    choices=["missing", "all", "refresh", "watched", "want"],
                    help="missing=只补没有磁力的（默认，优先）；refresh=补完再刷新已有磁力的；"
                         "all=全部；watched/want=按清单")
    ap.add_argument("--max", type=int, default=0, help="本轮最多抓多少部（0=不限）")
    ap.add_argument("--workers", type=int, default=3, help="并发数（默认 3；javbus 比 javdb 宽容）")
    ap.add_argument("--sleep", type=float, default=1.0, help="每次请求后的基础间隔秒")
    ap.add_argument("--cap", type=int, default=0, help="每部影片最多保留多少条磁力（0=不限；控制文件体积用）")
    ap.add_argument("--dry-run", dest="dry_run", action="store_true")
    ap.add_argument("--recheck-npj", action="store_true",
                    help="重查 javbus 无预告表里的番号（默认跳过，怀疑源站补内容时用）")
    args = ap.parse_args()

    # javbus 不需要 javdb cookie；这里只做一次连通性提示，不阻断
    if not os.path.exists(COOKIE):
        log("提示：无 javdb cookie（已不需要，磁力改走 javbus.com）")

    mv, _ = load_js(MOVIES_JS, MV_VAR)
    id2code = {e["id"]: k for k, e in mv.items() if e.get("id")}
    store = load_store(mv, id2code)
    npj = load_npj()   # 提前加载：pick_targets 要用它跳过已确认无磁力的番号
    targets = pick_targets(mv, store, args.scope,
                           recheck_npj=args.recheck_npj, npj=npj)
    if args.max:
        targets = targets[:args.max]
    have_mag = sum(1 for v in store.values() if v)
    n_no = sum(1 for vid, c in targets if not (store.get(vid) or store.get(c)))
    log(f"影片库 {len(mv)} | 已有磁力条目 {len(store)}（非空 {have_mag}）| "
        f"本轮目标 {len(targets)}（其中无磁力 {n_no}）| 并发 {args.workers}")
    if args.dry_run:
        print("示例目标:", targets[:10])
        return 0
    if not targets:
        log("没有要抓的，收工")
        return 0

    t0 = time.time()
    cnt = {"done": 0, "fail": 0, "mag": 0, "smp": 0, "npj": 0}
    smp = load_samples()          # code -> [剧照URL]，与磁力分开存
    # npj 已在上面加载（pick_targets 要用）

    def work(pair):
        vid, code = pair
        if stop_flag.is_set():
            return
        got = fetch_magnets_javbus(code)
        # ★ 锁内只做计数，不做写盘：save_all() 内部会调 log()，而 log() 也要拿 _lock，
        #   在锁内调用 = 自死锁（2026-11-25 实测：跑到第 20 条就永久卡死、CPU 增量 0）。
        need_save = False
        with _lock:
            if got is None:
                cnt["fail"] += 1
            else:
                mags = got["magnets"]
                samples = got["samples"]
                store[vid] = mags
                if samples:
                    smp[code] = samples
                    cnt["smp"] += len(samples)
                # ★ javbus 侧「无预告」判定：详情页解析成功、但没有视频入口 → 记 javbus 自己的表。
                #   只写 javbus_no_preview.json，绝不碰 javdb 的 videos_no_preview.json。
                if not got.get("has_video"):
                    reason = "no_video_entry" if samples else "no_sample_at_all"
                    if record_npj(npj, code, reason):
                        cnt["npj"] += 1
                cnt["done"] += 1
                cnt["mag"] += len(mags)
            n = cnt["done"] + cnt["fail"]
            if n % 20 == 0:
                need_save = True
                snap = (n, cnt["done"], cnt["fail"], cnt["mag"], cnt["smp"], cnt["npj"])
        if need_save:
            save_all(store, id2code, mv, args.cap)
            save_samples(smp)
            save_npj(npj)
            log(f"进度 {snap[0]}/{len(targets)} 成功 {snap[1]} 失败 {snap[2]} "
                f"磁力新增 {snap[3]} 剧照 {snap[4]} 张 无预告 {snap[5]} "
                f"用时 {(time.time() - t0) / 60:.0f}min "
                f"| magnets.js {(os.path.getsize(OUT) if os.path.exists(OUT) else 0)/1048576:.1f}MB")
        time.sleep(args.sleep * (0.7 + 0.6 * (hash(code) % 100) / 100.0))

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        try:
            list(ex.map(work, targets))
        except KeyboardInterrupt:
            log("收到 Ctrl-C，保存已抓到的部分后退出")
    data = save_all(store, id2code, mv, args.cap)
    save_samples(smp)
    save_npj(npj)
    total_mag = sum(len(v) for v in data.values())
    total_smp = sum(len(v) for v in smp.values())
    log(f"DONE 本轮成功 {cnt['done']} 失败 {cnt['fail']} | magnets.js 覆盖 {len(data)} 个番号、{total_mag} 条磁力"
        f" | 剧照 {len(smp)} 个番号、{total_smp} 张"
        f" | javbus无预告 {len(npj)} 条（独立于 javdb 的 videos_no_preview.json）"
        f" | 用时 {(time.time() - t0) / 60:.0f}min")
    try:
        _tb, _counts = _rebuild_buckets()
        log(f"已重建磁力分桶 data/site/mag/*.js（{len(_counts)} 桶、{_tb/1048576:.1f}MB）")
    except Exception as e:
        log("分桶重建失败（可手动跑 build/magnet_buckets.py）：", e)
    try:
        from sample_buckets import rebuild as _rebuild_smp
        _st, _sb = _rebuild_smp()
        log(f"已重建剧照分桶 data/site/smp/*.js（{len([b for b in _sb if _sb[b]])} 桶、{_st} 张）")
    except Exception as e:
        log("剧照分桶重建失败（可手动跑 build/sample_buckets.py）：", e)
    return 2 if stop_flag.is_set() else 0


if __name__ == "__main__":
    sys.exit(main())
