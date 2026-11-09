# -*- coding: utf-8 -*-
"""covers/ 封面文件的**真伪校验与修复**工具。

为什么需要它：以往下载封面只判「文件存在且 > 2000 字节」，但被 Cloudflare 拦下时
`curl -o` 写回来的是**拦截页 HTML**（体积 6~9KB，照样通过 >2000 的检查），于是
`movies.js` 里挂了一堆 `local: "covers/XXX.jpg"`，页面上全是加载失败的占位图。
本工具按**文件头的 magic bytes** 判真伪，不再相信体积。

判定规则（唯一真相 = 文件内容）：
  - JPEG `\xff\xd8\xff` / PNG `\x89PNG` / WEBP `RIFF????WEBP` / GIF `GIF8` -> 有效
  - 开头是 `<!DOCTYPE` / `<html` / `<?xml`，或体积 < 1KB -> **损坏**（拦截页/残缺）

★★ 与 covers_manifest.json 的配合（搬目录后不重下，见 RESUME 第 10 节）★★
  「这张封面下没下过」不再只看 assets/covers/ 里有没有文件，而是按 _script/covers_manifest.py：
  - 目录里有真图片 -> 已下载（顺手登记）；
  - 目录里没有、但清单有 valid 记录 -> **视为已搬走，不重下**，`local` 字段也照样挂/保留；
  - 目录里的文件不是图片 -> 清单改 valid=false，允许重下。
  想强行按目录实况重下（比如怀疑归档坏了）：加 `--ignore-manifest`。

命令：
  python covers_check.py                 # 只体检：损坏 / 有效但没挂 local / 缺失需要下载 / 已搬走
  python covers_check.py --repair        # 重下「损坏」与「缺失」的封面（逐个校验 magic，重试 3 次 + 退避）
  python covers_check.py --mount         # 只把已有效的文件挂进 movies.js 的 local（不下载）
  python covers_check.py --codes A,B     # 只处理指定番号（配合 --repair 做小范围补下；隐含 --ignore-manifest）
  python covers_check.py --scope want    # 范围：want|watched|all|collect|new（默认 all=整个 movies.js）
  python covers_check.py --dry-run       # 不写盘不下载

安全：只读 covers/ 与 movies.js，只写 movies.js 与 covers/ 下的同名文件（curl -o 直接覆盖，
      不使用 tmp+os.replace，遵守 RESUME 第 5 节坑 1）；不删除任何文件，也不删已下载记录。
限速：默认 4 线程 + 每次请求后 0.6~1.2s 抖动；被拦就退避重试，最终失败会列出来。
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
import io
import json
import os
import random
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

DATA = os.path.join(ROOT, "data")                        # 站点数据与名单
SITE = os.path.join(DATA, "site")    # 页面要读的 *.js（HTML 写死引用）
STATE = os.path.join(DATA, "state")  # 抓取状态：清单 / 终态名单 / 校验基准
ASSETS = os.path.join(ROOT, "assets")             # 资源目录（covers / videos / 水果派）
WB = os.path.join(ROOT, "_pipeline")     # 过程中文件：断点 / cookie / 临时切片（删了会丢进度）
LOGS = os.path.join(ROOT, "_logs")      # 日志与验证截图：随时可删，不影响任何数据
MOVIES_JS = os.path.join(SITE, "movies.js")
# 封面目录可用 JDB_COVERS_DIR 临时指到别处（用来演练「封面已整体搬走」的判定，不动真实数据）
COVERS = os.environ.get("JDB_COVERS_DIR") or os.path.join(ASSETS, "covers")
import covers_manifest as cm     # noqa: E402  「已下载清单」= 搬走封面后不重下的判定依据
is_valid_image = cm.is_valid_image    # 真伪判定统一走清单模块（merge_collection / users_spider 仍从这里导入）
VAR = "window.JAVDB_MOVIES = "
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
PROXY = "http://127.0.0.1:7890"
MIN_BYTES = cm.MIN_BYTES
MAGIC = cm.MAGIC
CURL_TIMEOUT = 20           # 单张封面给 20s 够了；再长就是死链/掉线，不值得等
MIN_OK_SIZE = 2000000       # 进度日志间隔（张）

_lock = threading.Lock()
stat = {"ok": 0, "bad": 0, "fixed": 0, "failed": [], "mounted": 0,
        "blocked": 0, "transient": 0, "blocked_codes": [], "n": 0, "t0": 0.0}


def log(*a):
    print(*a, flush=True)


def load_movies():
    t = io.open(MOVIES_JS, encoding="utf-8").read()
    i = t.index(VAR) + len(VAR)
    d, _ = json.JSONDecoder().raw_decode(t[i:].lstrip())
    return d, t[:t.index(VAR)]


def save_movies(head, d):
    with io.open(MOVIES_JS, "w", encoding="utf-8") as f:
        f.write(head + VAR + json.dumps(d, ensure_ascii=False, separators=(",", ":")) + ";\n")


def cover_path(code):
    d = cm.cover_dir(code)
    for ext in (".jpg", ".png", ".webp", ".jpeg"):
        p = os.path.join(d, code + ext)
        if is_valid_image(p):
            return p, ext
    p = os.path.join(d, code + ".jpg")
    return (p if os.path.exists(p) else None), ".jpg"


def pick_codes(mv, scope, only, ckpt_path):
    if only:
        return [c.strip().upper() for c in only.split(",") if c.strip()]
    if scope == "all":
        return list(mv)
    def load_var(fname, var):
        p = os.path.join(SITE, fname)
        if not os.path.exists(p):
            return []
        t = io.open(p, encoding="utf-8").read()
        i = t.index("=", t.index(var)) + 1
        d, _ = json.JSONDecoder().raw_decode(t[i:].lstrip())
        return list(d)
    if scope == "want":
        return load_var("want.js", "window.JAVDB_WANT")
    if scope == "watched":
        return load_var("watched.js", "window.JAVDB_WATCHED")
    if scope == "collect":
        ck = json.load(io.open(ckpt_path, encoding="utf-8"))
        return list(ck.get("movies", {}))
    return list(mv)


def nap(x):
    time.sleep(x)


UNAVAIL = os.path.join(STATE, "covers_unavailable.json")


def url_candidates(url):
    """同一个封面试多个地址：优先 covers/（大图），拿不到再试 thumbs/（缩略图）。
    实测若干老片，covers/{x}/{id}.jpg 在 CDN 上已 403，而 thumbs/{x}/{id}.jpg 还能拿到。"""
    out = [url]
    if "/covers/" in url:
        out.append(url.replace("/covers/", "/thumbs/"))
    return out


def _curl_code(url, dst, proxy_on, cookie):
    cmd = ["curl", "-s", "--ssl-no-revoke", "--max-time", str(CURL_TIMEOUT), "-A", UA,
           "-H", "Referer: https://javdb.com/", "-H", "Accept-Language: zh-TW,zh;q=0.9",
           "-H", "Accept: image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
           "-o", dst, "-w", "%{http_code}"]
    if proxy_on:
        cmd += ["-x", PROXY]
    if cookie:
        cmd += ["-H", f"Cookie: {cookie}"]
    cmd += [url]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return (r.stdout or "").strip()[-3:]


def fetch_cover2(code, url, tries=2, use_proxy=None, cookie=None):
    """返回 (是否拿到真图片, 状态)。
    状态：ok / blocked（CDN 上确实没有：403/404 或返回的不是图片 —— **不再重试**）/ fail（网络瞬时问题）。
    ★ 旧版对死链要付 4 次重试 × 2 个地址 × 40s 超时 ≈ 2 分钟，2.4 万张里混上千个死链时纯粹空耗。"""
    dst = os.path.join(cm.cover_dir(code), code + ".jpg")
    os.makedirs(os.path.dirname(dst), exist_ok=True)   # 确保分桶子目录存在
    modes = [False, True] if use_proxy is None else [bool(use_proxy)]
    last = "fail"
    for a in range(tries):
        proxy_on = modes[min(a, len(modes) - 1)]
        blocked = True
        for u in url_candidates(url):
            code_http = _curl_code(u, dst, proxy_on, cookie)
            if is_valid_image(dst):
                return True, "ok"
            if code_http in ("403", "404", "410"):
                last = "blocked"          # 该地址确实没图；试完下一个候选地址就收工
                continue
            blocked = False               # 000/超时/5xx 之类，当瞬时失败
            last = "fail"
        if blocked:
            return False, "blocked"
        if a + 1 < tries:
            nap((1.0 + 1.5 * a) * random.uniform(0.8, 1.3))
    return False, last


def fetch_cover(code, url, tries=2, use_proxy=None, cookie=None):
    """bool 包装（给 users_spider / merge_collection 用，保持旧行为）。"""
    ok, _ = fetch_cover2(code, url, tries=tries, use_proxy=use_proxy, cookie=cookie)
    return ok


def load_unavailable():
    if os.path.exists(UNAVAIL):
        try:
            return set(json.load(io.open(UNAVAIL, encoding="utf-8")).get("codes", []))
        except Exception:
            return set()
    return set()


def save_unavailable(codes, note=""):
    payload = {
        "version": 1,
        "desc": "CDN 上确实拿不到的封面（covers/ 与 thumbs/ 都 403），covers_check 默认不再重试；"
                "页面上这些片子会退回远程图或占位图",
        "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(codes),
        "codes": sorted(codes),
    }
    if note:
        payload["note"] = note
    with io.open(UNAVAIL, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def download(code, url, cookie=None, tries=2, use_proxy=None):
    """fetch_cover2 的计数包装（供 --repair 使用）；blocked 当场记入「取不到」名单，不再重试。
    成功=登记进 covers_manifest（以后文件搬走也不重下）；失败且盘上留下垃圾文件=标 valid=false。"""
    ok, status = fetch_cover2(code, url, tries=tries, use_proxy=use_proxy, cookie=cookie)
    with _lock:
        if ok:
            cm.mark_downloaded(code, os.path.join(cm.cover_dir(code), code + ".jpg"))
            stat["fixed"] += 1
        else:
            stat["failed"].append(code)
            junk = os.path.join(cm.cover_dir(code), code + ".jpg")
            if os.path.exists(junk) and not is_valid_image(junk):
                cm.mark_invalid(code)          # 拦页冒充成品，下次体检还能发现它
            if status == "blocked":
                stat["blocked"] += 1
                stat["blocked_codes"].append(code)
            else:
                stat["transient"] += 1
    return ok


def time_sleep(x):   # 兼容旧命名（内部统一用 nap）
    nap(x)


def main():
    ap = argparse.ArgumentParser(description="covers/ 封面真伪校验与修复")
    ap.add_argument("--repair", action="store_true", help="重下损坏/缺失的封面")
    ap.add_argument("--bad-only", dest="bad_only", action="store_true",
                    help="只修「内容不是图片」的损坏文件，不管缺失的（避开 2 万张的历史欠账）")
    ap.add_argument("--mount", dest="mount_only", action="store_true", help="只把有效文件挂进 local")
    ap.add_argument("--codes", default="", help="只处理这些番号（逗号分隔）")
    ap.add_argument("--scope", default="all", choices=["all", "want", "watched", "collect", "new"])
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--use-cookie", dest="use_cookie", action="store_true",
                    help="下载时带上 javdb_cookie.txt（CF 持续拦截时再开）")
    ap.add_argument("--proxy", action="store_true",
                    help="强制只走 127.0.0.1:7890 代理下封面（默认先直连、不通再自动试代理）")
    ap.add_argument("--retry-unavailable", dest="retry_unavail", action="store_true",
                    help="重试 covers_unavailable.json 里记着的「CDN 上拿不到」的封面")
    ap.add_argument("--tries", type=int, default=2, help="每张封面的重试次数（403/404 会立刻定性为取不到，不占用重试）")
    ap.add_argument("--ignore-manifest", dest="ignore_manifest", action="store_true",
                    help="不信 covers_manifest.json，全按目录实况判定（=目录空了就会全量重下，慎用）")
    args = ap.parse_args()
    # 手动点名番号就是要重下，不必再让清单拦住
    if args.codes:
        args.ignore_manifest = True

    mv, head = load_movies()
    cookie = None
    if args.use_cookie:
        cp = os.path.join(WB, "javdb_cookie.txt")
        cookie = io.open(cp, encoding="utf-8").read().strip() if os.path.exists(cp) else None

    if args.codes:
        codes = [c.strip().upper() for c in args.codes.split(",") if c.strip()]
    elif args.scope == "new":
        broot = os.path.join(ROOT, "_backup")
        bn = "movies.js.before_users_sync"
        # 备份改为按日期存子目录：取最新日期那份作基准；没有则回退旧平铺路径
        dated = sorted(os.path.join(broot, d, bn) for d in os.listdir(broot)
                       if os.path.exists(os.path.join(broot, d, bn)))
        bak = dated[-1] if dated else os.path.join(broot, bn)
        old, _ = load_movies_from(bak) if os.path.exists(bak) else ({}, "")
        codes = [c for c in mv if c not in old]
    else:
        codes = pick_codes(mv, args.scope, "", os.path.join(WB, "collection_ckpt.json"))

    # ---------- 体检 ----------
    unavail = load_unavailable()
    skip_known = unavail if not args.retry_unavail else set()
    bad, missing, valid_unmounted, valid_mounted, noimg, skipped = [], [], [], [], [], 0
    moved = []                       # 清单有记录但文件不在 covers/ —— 已搬走，**不重下**
    for c in codes:
        e = mv.get(c)
        if e is None:
            continue
        p, _ext = cover_path(c)
        if p and is_valid_image(p):
            cm.put(c, p, valid=True, origin="disk")          # 顺手登记，搬走前先把在册状态落好
            if mv[c].get("local"):
                valid_mounted.append(c)
            else:
                valid_unmounted.append(c)
            continue
        # 盘上没文件（p=None）或文件不是图片：先问清单
        if not args.ignore_manifest and cm.is_downloaded(c) and p is None:
            moved.append(c)
            continue
        if not args.ignore_manifest and cm.is_downloaded(c) and p:
            cm.mark_invalid(c)                                # 清单说有效、盘上却是垃圾 -> 以盘为准
        if c in skip_known:
            skipped += 1
            continue
        if p:
            bad.append(c)                      # 文件在但内容不是图片（多为 CF 拦截页）
        elif mv.get(c, {}).get("img"):
            missing.append(c)                  # 没文件、清单也没记录，可下载
        else:
            noimg.append(c)
    cm.flush()
    log(f"体检范围 {len(codes)} 个番号：有效已挂 {len(valid_mounted)} | 有效未挂 {len(valid_unmounted)} | "
        f"已搬走(凭清单不重下) {len(moved)} | 损坏(非图片) {len(bad)} | 缺失待下 {len(missing)} | "
        f"无远程地址 {len(noimg)}"
        + (f" | 已知取不到（跳过）{skipped}" if skipped else "")
        + (" | --ignore-manifest 已启用" if args.ignore_manifest else ""))
    if moved[:10]:
        log("  已搬走示例（不会重下）:", ", ".join(moved[:10]))
    if bad[:10]:
        log("  损坏示例:", ", ".join(bad[:10]))
    if missing[:10]:
        log("  缺失示例:", ", ".join(missing[:10]))

    # ---------- 挂载 ----------
    for c in valid_unmounted:
        p, ext = cover_path(c)
        # 用盘上的实际大小写（有少量老封面叫 `n0406.jpg`），否则拷到区分大小写的系统上建站会找不到
        mv[c]["local"] = cm.local_field(cm.true_name(p))
        stat["mounted"] += 1
    # 已搬走的也要有 local：记录不能因为“文件不在本地”就丢（别处建站拷全 covers/ 后这些图能用）
    for c in moved:
        if not mv[c].get("local"):
            rec = cm.get(c) or {}
            mv[c]["local"] = cm.local_field(rec.get("file") or f"{c}.jpg")
            stat["mounted"] += 1
    if stat["mounted"] and not args.dry_run:
        save_movies(head, mv)
        log(f"已挂载 {stat['mounted']} 条封面到 local（含凭清单补挂的已搬走条目）")

    if not args.repair:
        if bad or missing:
            log("下一步：python covers_check.py --repair   （重下损坏与缺失的封面）")
        return 0

    # ---------- 修复 ----------
    todo = [c for c in bad + ([] if args.bad_only else missing) if mv.get(c, {}).get("img")]
    stat["t0"] = time.time()
    log(f"待重下 {len(todo)} 张（损坏 {len(bad)}"
        + (f" + 缺失 {len(missing)}" if not args.bad_only else "，--bad-only 已忽略缺失") + f"），并发 {args.workers}")
    if not todo:
        return 0
    if args.dry_run:
        log("--dry-run：不下载")
        return 0

    # 损坏的文件先从 local 里摘下来（数据要诚实）；修好了再挂回去
    # 注意：moved（已搬走）不在 bad 里，local 一律保留，符合「记录只增不删」
    for c in bad:
        mv[c].pop("local", None)

    def work(c):
        ok = download(c, mv[c]["img"], cookie, use_proxy=(True if args.proxy else None), tries=args.tries)
        if ok:
            mv[c]["local"] = cm.local_field(c + ".jpg")
        with _lock:
            stat["n"] += 1
            n = stat["n"]
        if n % 200 == 0:                      # 阶段性存盘：崩了/被中断也不白跑
            save_movies(head, mv)
            cm.flush()                        # 已下载清单同步落盘（别下完了却没登记 -> 下次重下）
            save_unavailable(set(unavail) | set(stat["blocked_codes"]),
                             note=f"本轮新判定 {len(stat['blocked_codes'])} 张 CDN 上取不到")
            el = time.time() - stat["t0"]
            rate = n / el * 3600 if el else 0
            left = max(0, len(todo) - n)
            log(f"  进度 {n}/{len(todo)} | 成功 {stat['fixed']} 死链 {stat['blocked']} 网络问题 {stat['transient']}"
                f" | {rate:.0f} 张/小时 | 剩余 {left} 约 {left / max(rate, 1):.1f} 小时")
        nap(random.uniform(0.05, 0.25))

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(work, todo))
    save_movies(head, mv)
    cm.flush()
    log(f"修复成功 {stat['fixed']} / 失败 {len(stat['failed'])}"
        f"（其中 CDN 上确实没有 {stat['blocked']}、网络瞬时问题 {stat['transient']}）")
    if stat["failed"]:
        log("  失败番号:", ", ".join(stat["failed"][:30]) + (" ..." if len(stat["failed"]) > 30 else ""))
        # 只把「CDN 上确实没有」(403/404/410) 记为终态，下次不再重试；
        # 网络瞬时失败不能记（否则代理/短暂断线会把好片子永久当成死链）。
        if stat["blocked"]:
            merged = set(unavail) | set(stat["blocked_codes"])
            save_unavailable(merged, note=f"本轮新判定 {len(stat['blocked_codes'])} 张 CDN 上取不到")
            log(f"  已记入 covers_unavailable.json（共 {len(merged)} 条），下次默认跳过")
            log("  若怀疑是临时拦截：python covers_check.py --repair --bad-only --retry-unavailable")
        if stat["transient"]:
            log(f"  其中 {stat['transient']} 张是网络瞬时失败（未记入终态），直接重跑 --repair 会再试")
    log(f"[{datetime.datetime.now():%H:%M:%S}] covers_check 完成")
    return 0


def load_movies_from(path):
    t = io.open(path, encoding="utf-8").read()
    i = t.index(VAR) + len(VAR)
    d, _ = json.JSONDecoder().raw_decode(t[i:].lstrip())
    return d, t[:t.index(VAR)]


if __name__ == "__main__":
    sys.exit(main())
