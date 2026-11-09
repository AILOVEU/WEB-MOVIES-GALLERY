# -*- coding: utf-8 -*-
"""javbus forum fid=2 抓取器（列表 1-10 页 + 每帖前 5 页评论，断点续传）。

设计要点（沉淀自 skill 形态 C / wall-gallery）：
- 代理必须（直连 HTTP:000）：curl --ssl-no-revoke -x 127.0.0.1:7890。
- 自适应限速 Pace：起步 1.2s，站点吃力自动加、命中限流冷却。串行翻页最稳。
- 响应分层：valid 帖子页含 nthread_postbox、valid 列表页含 normalthread_；
  登录墙标记 -> 存 checkpoint 后 exit(2)（上层监管循环据此停并提示重抽 cookie）。
- 分页钳制：请求超末页返回末页（PID 全重复）-> 据 seen-pid 终止该帖翻页。
- 幂等：HTML 文件存在即复用；thread 完成记入 done 清单，续传整体跳过。
- 沙盒守卫：所有落盘直接写目标文件，不用 tmp/os.replace/.part。
- 与 `_scripts/common/spider_kit.py` 是**两套并行实现**（curl/Pace/classify/fetch 同名不同参，
  且差异有意：本站先认内容锚点、页更小、限速更激进）：调限速、限流短语、登录墙特征时两边要一起改。

用法：
  python _scripts/inc/crawl.py list                 # 只抓列表 1-10
  python _scripts/inc/crawl.py threads              # 按已抓列表抓帖子详情（1-5 页）
  python _scripts/inc/crawl.py refs                 # 补抓“帖内引用”：正文/评论里提到的本站 tid，逐层闭包最多 --max-depth 层
  python _scripts/inc/crawl.py all                  # 全流程（refs 需先有 parsed，新库第一轮要跑两次 all）
  python _scripts/inc/crawl.py threads --only=177938,178021   # 指定 tid
可选环境变量：JAVBUS_PROXY（默认 http://127.0.0.1:7890）
目录布局见 _scripts/common/paths.py：原始 HTML 落 _rawdata/，cookie 与断点落 _pipeline/，日志（你重定向的
*.log）落 _logs/；整个 _logs/ 删掉不丢任何数据。
"""
import argparse
import io
import json
import os
import re
import subprocess
import sys
import time
import random

_SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # _scripts
sys.path.insert(0, os.path.join(_SCRIPTS, "common"))                      # 公共库：paths/discuz/pannet
import discuz as D
import paths as P

ROOT = P.ROOT
RAW_DIR = P.RAW                 # 原始抓取件：list_pN.html + posts/<tid>_pN.html（删了要重爬）
POSTS_DIR = P.RAW_POSTS         # 帖子页原始 HTML（与交付层 data/posts/*.js 是两回事）
WORK_DIR = P.WORK               # 过程与状态：cookie.txt / crawl_done.txt 断点
PARSED_DIR = P.PARSED           # 过程区：refs 模式从这里的正文/楼层提引用
COOKIE_PATH = P.COOKIE
DONE_PATH = P.CRAWL_DONE
PROXY = os.environ.get("JAVBUS_PROXY", "http://127.0.0.1:7890")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
LIST_URL = "https://www.javbus.com/forum/forum.php?mod=forumdisplay&fid=2&page=%d"
THREAD_URL = "https://www.javbus.com/forum/forum.php?mod=viewthread&tid=%s&page=%d"

LOGIN_MARKS = ("id=\"loginform\"", "action=login&amp;infloat", "您需要先登录",
               "請先登錄", "請先登入後", "您需要登录")
RATE_MARKS = ("Too Many Requests", "请求过于频繁", "請稍後再試", "稍後再試",
              "訪問过于频繁", "訪問過於頻繁")


def log(*a):
    print("[%s]" % time.strftime("%H:%M:%S"), *a, flush=True)


def curl(url, cookie, referer=None, to_file=None, timeout=45):
    cmd = ["curl", "-sL", "--ssl-no-revoke", "--max-time", str(timeout),
           "-x", PROXY, "-A", UA, "-H", "Cookie: " + cookie]
    if referer:
        cmd += ["-H", "Referer: " + referer]
    cmd.append(url)
    if to_file:
        cmd += ["-o", to_file]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
        return r.returncode == 0
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    return r.stdout or ""


class Pace:
    def __init__(self, start=1.2, lo=0.8, hi=20.0, every=120, rest=(10, 20)):
        self.delay, self.lo, self.hi = start, lo, hi
        self.every, self.rest = every, rest
        self.n = 0

    def ok(self, dur):
        self.n += 1
        self.delay = min(self.hi, self.delay * 1.6) if dur > 8 else max(self.lo, self.delay * 0.88)
        time.sleep(random.uniform(self.delay * 0.8, self.delay * 1.3))
        if self.n and self.n % self.every == 0:
            time.sleep(random.uniform(*self.rest))

    def blocked(self):
        self.n += 1
        self.delay = min(self.hi, max(self.delay * 2.2, 6.0))
        time.sleep(random.uniform(50, 100))


def classify(kind, text):
    """kind='list'|'thread' -> 'ok'|'end'|'truncated'|'blocked'|'login'。
    内容标记优先：有楼层/列表即 ok（登录态 nav 也含 logging&action=logout，不可据此判墙）。"""
    if not text or len(text) < 800:
        return "truncated"
    if kind == "list":
        if "normalthread_" in text or "stickthread_" in text:
            return "ok"
    else:
        if "nthread_postbox" in text or 'id="postmessage_' in text:
            return "ok"
    if any(m in text for m in RATE_MARKS):
        return "blocked"
    if any(m in text for m in LOGIN_MARKS):
        return "login"
    if "</html>" in text:
        return "end"  # 渲染完整但无内容锚点：列表空页=正常终止
    return "truncated"


def fetch(path, url, kind, cookie, referer=None, pace=None, tries=6):
    """抓取并直接写 path。返回 (verdict)。verdict=login 上层应存盘 exit(2)。"""
    for attempt in range(tries):
        t0 = time.time()
        ok = curl(url, cookie, referer=referer, to_file=path)
        dur = time.time() - t0
        text = io.open(path, encoding="utf-8", errors="ignore").read() if (ok and os.path.exists(path)) else ""
        v = classify(kind, text)
        if v == "ok":
            (pace or Pace()).ok(dur)
            return "ok"
        if v == "end":
            (pace or Pace()).ok(dur)
            return "end"
        if v == "login":
            return "login"
        if v == "blocked":
            if pace:
                pace.blocked()
            else:
                time.sleep(random.uniform(40, 80))
            continue
        # truncated
        if attempt < 2:
            time.sleep(random.uniform(5, 10))
    return "fail"


def load_cookie():
    return io.open(COOKIE_PATH, encoding="utf-8").read().strip()


def crawl_list(cookie, pace, pages=10, force=False):
    P.ensure(RAW_DIR)
    for p in range(1, pages + 1):
        path = os.path.join(RAW_DIR, "list_p%d.html" % p)
        if not force and os.path.exists(path) and os.path.getsize(path) > 8000:
            log("list p%d exists, skip" % p)
            continue
        v = fetch(path, LIST_URL % p, "list", cookie, referer="https://www.javbus.com/forum/", pace=pace)
        if v == "login":
            log("LOGIN wall at list p%d -> exit(2)" % p); sys.exit(2)
        n = len(D.parse_list_page(io.open(path, encoding="utf-8", errors="ignore").read())) if v in ("ok", "end") and os.path.exists(path) else 0
        log("list p%d -> %s (%d threads)" % (p, v, n))


def collect_tids(pages=10):
    order, seen = [], set()
    for p in range(1, pages + 1):
        path = os.path.join(RAW_DIR, "list_p%d.html" % p)
        if not os.path.exists(path):
            continue
        for r in D.parse_list_page(io.open(path, encoding="utf-8", errors="ignore").read()):
            if r["tid"] not in seen:
                seen.add(r["tid"]); order.append(r["tid"])
    return order


def thread_done_set():
    return set(io.open(DONE_PATH, encoding="utf-8").read().split()) if os.path.exists(DONE_PATH) else set()


def collect_refs_from_parsed():
    """扫 _pipeline/parsed/*.json 的正文+评论，提被引用的本站 tid。

    只从 parsed 的 content_html 提，不能扫 _rawdata 的整页 HTML：页壳里的
    “上一主题/下一主题”导航带的是不相干 tid（实测 126 vs 真实引用 88）。
    """
    refs = set()
    for fn in sorted(os.listdir(PARSED_DIR)) if os.path.isdir(PARSED_DIR) else []:
        if not fn.endswith(".json"):
            continue
        try:
            d = json.load(io.open(os.path.join(PARSED_DIR, fn), encoding="utf-8"))
        except Exception:
            continue
        own = str(d.get("tid"))
        blob = [d.get("article", {}).get("content_html") or ""]
        blob += [c.get("content_html") or "" for c in d.get("comments") or []]
        for h in blob:
            refs |= {t for t in D.extract_thread_refs(h) if t != own}
    return refs


def mark_done(tid, pace=None):
    P.ensure(WORK_DIR)
    with io.open(DONE_PATH, "a", encoding="utf-8") as f:
        f.write(tid + "\n")


def crawl_threads(cookie, pace, max_pages=5, only=None, pages=10):
    P.ensure(POSTS_DIR, WORK_DIR)
    tids = only or collect_tids(pages)
    done = thread_done_set()
    todo = [t for t in tids if t not in done]
    log("threads total=%d done=%d todo=%d" % (len(tids), len(done), len(todo)))
    dp = DONE_PATH
    for idx, tid in enumerate(todo, 1):
        seen_pids = set()
        stop = False
        for page in range(1, max_pages + 1):
            path = os.path.join(POSTS_DIR, "%s_p%d.html" % (tid, page))
            if os.path.exists(path) and os.path.getsize(path) > 8000:
                html = io.open(path, encoding="utf-8", errors="ignore").read()
                pids = set(re.findall(r'id="postmessage_(\d+)"', html))
                if page > 1 and not (pids - seen_pids):
                    stop = True
                seen_pids |= pids
                continue
            v = fetch(path, THREAD_URL % (tid, page), "thread", cookie,
                      referer="https://www.javbus.com/forum/forum.php?mod=forumdisplay&fid=2", pace=pace)
            if v == "login":
                log("LOGIN wall at thread %s p%d -> exit(2)" % (tid, page)); sys.exit(2)
            if v in ("end", "fail"):
                if v == "fail" and page == 1:
                    log("  tid %s page1 FAIL (leave unfinished)" % tid)
                    break
                stop = True
            html = io.open(path, encoding="utf-8", errors="ignore").read() if os.path.exists(path) else ""
            pids = set(re.findall(r'id="postmessage_(\d+)"', html))
            if page > 1 and not (pids - seen_pids):
                stop = True
            seen_pids |= pids
            if stop:
                break
        with io.open(dp, "a", encoding="utf-8") as f:
            f.write(tid + "\n")
        if idx % 10 == 0 or idx == len(todo):
            log("thread %d/%d done (last tid=%s, pages+pid=%d)" % (idx, len(todo), tid, len(seen_pids)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", nargs="?", default="all", choices=["list", "threads", "refs", "all"])
    ap.add_argument("--pages", type=int, default=10,
                    help="列表页数；list 模式抓这么多页，threads 模式也从这么多页里取待抓 tid（两种模式都要传，别只传 list）")
    ap.add_argument("--max-pages", type=int, default=5)
    ap.add_argument("--max-depth", type=int, default=10,
                    help="refs 模式的逐层闭包上限（默认 10 层；收敛就提前停，不收敛就停在第 10 层）")
    ap.add_argument("--only", type=str, default="")
    ap.add_argument("--force", action="store_true", help="强制重抓列表页(增量更新用,列表随热度随机刷新)")
    a = ap.parse_args()
    cookie = load_cookie()
    pace = Pace()
    only = [x.strip() for x in a.only.split(",") if x.strip()] or None
    if a.mode in ("list", "all"):
        crawl_list(cookie, pace, a.pages, a.force)
    if a.mode in ("threads", "all"):
        crawl_threads(cookie, pace, a.max_pages, only, a.pages)
    if a.mode in ("refs", "all"):
        # 帖内引用补抓（逐层闭包）：每层从 parsed 的正文/评论提本站 tid，减去 crawl_done
        # 就是本层要抓的；抓完再 parse 一轮，新帖带出的引用下一层继续。
        # parsed 是上一轮 parse 的产物，不重跑 parse 第二层永远提不到新 tid。
        if not only:
            done = thread_done_set()
            new_total = 0
            last_layer = []
            converged = True
            for depth in range(1, a.max_depth + 1):
                todo = sorted(collect_refs_from_parsed() - done)
                log("refs 第%d层 待抓 %d 个" % (depth, len(todo)))
                if not todo:
                    log("refs 已收敛，共抓 %d 层 / %d 帖" % (depth - 1, new_total))
                    converged = True   # 前面几层会把标志抹成 False，break 时要复位
                    break
                converged = False
                last_layer = todo
                crawl_threads(cookie, pace, a.max_pages, todo, a.pages)
                new_total += len(todo)
                done |= set(todo)
                subprocess.run([sys.executable or "python", os.path.join(_SCRIPTS, "inc", "parse.py"),
                                "--pages=%d" % a.pages], cwd=ROOT)
            else:
                log("refs 到达 --max-depth=%d 上限停止（共抓 %d 帖），未收敛部分下轮续抓" % (a.max_depth, new_total))
            P.ensure(WORK_DIR)
            io.open(P.PENDING_REFS, "w", encoding="utf-8").write(
                "%s\n# 本次 refs 最后一层实际抓的 tid（共 %d 帖）\n%s\n"
                % ("# 已收敛" if converged else "# 未收敛，下轮 update 会继续补抓", new_total,
                   "\n".join(last_layer)))
        else:
            todo = [t for t in only if t not in thread_done_set()]
            log("refs 指定 %d 个 tid（其中待抓 %d）" % (len(only), len(todo)))
            if todo:
                crawl_threads(cookie, pace, a.max_pages, todo, a.pages)
    log("crawl finished: mode=%s force=%s" % (a.mode, a.force))


if __name__ == "__main__":
    main()
