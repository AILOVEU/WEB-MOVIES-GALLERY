# -*- coding: utf-8 -*-
"""T66Y 版面列表抓取器（fid 可配，默认 25 國產原創區）。只抓列表页，不抓帖子详情。

设计要点（沿用 JAVBUS 那套踩过的坑）：
- 代理必须：直连不通，curl -x 127.0.0.1:7890 --ssl-no-revoke。
- 自适应限速 Pace：起步 1.2s，站点吃力自动加、命中限流长冷却。串行最稳。
- 响应分层：ok=有列表行；end=完整页但无行（末页/超范围）；login=登录墙；
  blocked=限流/CF；truncated=截断（短退避重试）。
- **登录墙是硬边界**：游客只能翻到第 100 页（2026-11-24 实测，101 页起返回
  「您沒有登錄或者您沒有權限」）。要抓全 4957 页必须在 _pipeline/cookie.txt 放登录 cookie。
  没有 cookie 时脚本自动只抓游客可见范围，并在日志里明确写出「已到游客上限」。
- 断点续传：_pipeline/crawl_done.txt 记已抓页码；HTML 已存在且有效则跳过。
- 幂等：重复跑只补缺页，不重抓。
- 沙盒守卫：直接写目标文件，不用 tmp/os.replace/.part。

用法：
  python _scripts/inc/crawl.py                    # 增量：翻到水位线即停（默认）
  python _scripts/inc/crawl.py --full             # 全量：翻到底（游客上限内）
  python _scripts/inc/crawl.py --pages 200        # 指定抓前 200 页
  python _scripts/inc/crawl.py --fid 25 --start 1 --end 100
  python _scripts/inc/crawl.py --force            # 重抓已存在的页（配合 --full 全量刷新）
环境变量：T66Y_PROXY（默认 http://127.0.0.1:7890）

增量模式（默认）：
  列表按**发帖时间降序**（置顶除外），新帖插在最前，旧帖整体后移。
  所以从 p1 往下翻，**遇到上次记录的"最新一条"（水位线）就停** —— 它之后的内容
  上一轮已经全部拿到。水位线存在 _pipeline/records.json 的 watermark 字段。
  首次运行没有水位线时，自动退回 --full 行为（翻到底），并建立水位线。
"""
import argparse
import io
import os
import random
import re
import subprocess
import sys
import time

_SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_SCRIPTS, "common"))
import paths as P
import store as S
import t66y as T

PROXY = os.environ.get("T66Y_PROXY", "http://127.0.0.1:7890")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
LIST_URL = "https://t66y.com/thread0806.php?fid=%d&search=&page=%d"

LOGIN_MARKS = ("您沒有登錄或者您沒有權限", "您还没有登录", "您需要先登录")
RATE_MARKS = ("Too Many Requests", "请求过于频繁", "請稍後再試", "稍後再試",
              "訪問過於頻繁", "访问过于频繁", "Just a moment", "cf-browser-verification")
ROW_MARK = 'class="tr3 t_one'


def log(*a):
    print("[%s]" % time.strftime("%H:%M:%S"), *a, flush=True)


def curl(url, cookie="", to_file=None, timeout=45):
    cmd = ["curl", "-sL", "--ssl-no-revoke", "--max-time", str(timeout),
           "-x", PROXY, "-A", UA]
    if cookie:
        cmd += ["-H", "Cookie: " + cookie]
    cmd += ["-H", "Referer: https://t66y.com/thread0806.php?fid=25"]
    if to_file:
        cmd += ["-o", to_file]
    cmd.append(url)
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="ignore")
    if to_file:
        return r.returncode == 0
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


def classify(text):
    """-> 'ok' | 'end' | 'login' | 'blocked' | 'truncated'
    内容锚点优先：有列表行就是 ok（登录态页面壳里也可能出现登录字样，不能先判登录）。"""
    if not text or len(text) < 800:
        return "truncated"
    if ROW_MARK in text:
        return "ok"
    if any(m in text for m in LOGIN_MARKS):
        return "login"
    if any(m in text for m in RATE_MARKS):
        return "blocked"
    if "</html>" in text:
        return "end"        # 渲染完整但无列表行：超出末页
    return "truncated"


def fetch(path, url, cookie, pace, tries=6):
    """抓取并直接写 path。返回 verdict。"""
    for attempt in range(tries):
        t0 = time.time()
        ok = curl(url, cookie, to_file=path)
        dur = time.time() - t0
        text = ""
        if ok and os.path.exists(path):
            text = io.open(path, encoding="utf-8", errors="ignore").read()
        v = classify(text)
        if v in ("ok", "end"):
            pace.ok(dur)
            return v
        if v == "login":
            return "login"
        if v == "blocked":
            pace.blocked()
            continue
        if attempt < 2:
            time.sleep(random.uniform(5, 10))
    return "fail"


def load_cookie():
    if not os.path.exists(P.COOKIE):
        return ""
    txt = io.open(P.COOKIE, encoding="utf-8-sig").read().strip()
    # 允许文件里写注释行
    lines = [ln.strip() for ln in txt.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    return " ".join(lines)


def done_pages():
    if not os.path.exists(P.CRAWL_DONE):
        return set()
    return {int(x) for x in io.open(P.CRAWL_DONE, encoding="utf-8-sig").read().split() if x.isdigit()}


def mark_done(page):
    P.ensure(P.WORK)
    with io.open(P.CRAWL_DONE, "a", encoding="utf-8") as f:
        f.write("%d\n" % page)


def page_path(fid, page):
    return os.path.join(P.RAW, "fid%d_p%04d.html" % (fid, page))


def probe_total(fid, cookie, pace):
    """抓第 1 页拿总页数，同时确认登录态。返回 (total_pages, first_html)。"""
    p = page_path(fid, 1)
    if not (os.path.exists(p) and os.path.getsize(p) > 8000):
        v = fetch(p, LIST_URL % (fid, 1), cookie, pace)
        log("probe page1 -> %s" % v)
        if v == "login":
            return 0, ""
    html = io.open(p, encoding="utf-8", errors="ignore").read() if os.path.exists(p) else ""
    return T.page_count(html), html


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fid", type=int, default=25, help="版面 id（默认 25 國產原創區）")
    ap.add_argument("--pages", type=int, default=0, help="只抓前 N 页（0=全部）")
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--end", type=int, default=0, help="抓到此页（0=自动探测总页数）")
    ap.add_argument("--force", action="store_true", help="重抓已存在的页")
    ap.add_argument("--full", action="store_true",
                    help="全量模式：翻到底，不在水位线停（首次建库/定期深扫用）")
    a = ap.parse_args()

    P.ensure(P.RAW, P.WORK, P.log_dir())
    cookie = load_cookie()
    pace = Pace()

    # 水位线：上次抓到的最新一条。增量模式下遇到它就停。
    data = S.load(a.fid)
    wm = data.get("watermark") or {}
    wm_tid = wm.get("tid") or ""
    incremental = not a.full and bool(wm_tid)
    if not a.full and not wm_tid:
        log("没有水位线（首次运行或 records.json 缺失）→ 本轮按全量翻到底，并建立水位线")

    total, first = probe_total(a.fid, cookie, pace)
    if not total:
        log("无法读取总页数（第 1 页抓取失败或登录墙）")
        sys.exit(2)
    log("fid=%d 总页数=%d  登录态=%s  模式=%s"
        % (a.fid, total, "有 cookie" if cookie else "游客",
           "增量(水位线 %s)" % wm_tid if incremental else "全量"))

    end = a.end or total
    if a.pages:
        end = min(end, a.start + a.pages - 1)
    end = min(end, total)

    done = done_pages()
    todo = []
    for pg in range(a.start, end + 1):
        p = page_path(a.fid, pg)
        # 增量模式必须重抓前几页：新帖插在最前，旧页内容已整体后移，
        # 断点里"已抓过"的页现在是不同内容，跳过就永远看不到新帖。
        # 只有全量模式才认断点（那里页码是稳定的，跳过已抓页是安全的）。
        if not a.force and not incremental and pg in done \
                and os.path.exists(p) and os.path.getsize(p) > 8000:
            continue
        todo.append(pg)
    log("待抓 %d 页（%d..%d，已完成 %d）" % (len(todo), a.start, end, len(done)))

    hit_login = 0
    ok_n = 0
    hit_wm = False
    for i, pg in enumerate(todo, 1):
        p = page_path(a.fid, pg)
        v = fetch(p, LIST_URL % (a.fid, pg), cookie, pace)
        if v == "login":
            hit_login += 1
            if hit_login == 1:
                log("第 %d 页遇到登录墙 —— 游客可见范围到此为止" % pg)
            # 登录墙后连续 3 页都撞墙就整体停（不是偶发）
            if hit_login >= 3:
                log("连续 %d 页登录墙，停止。要抓更深页请在 %s 放登录 cookie" % (hit_login, P.COOKIE))
                break
            continue
        hit_login = 0
        if v == "ok":
            html = io.open(p, encoding="utf-8", errors="ignore").read()
            recs = T.parse_list_page(html, pg)
            mark_done(pg)
            ok_n += 1
            log("page %d/%d -> ok (%d 帖) 累计 %d 页" % (pg, end, len(recs), ok_n))
            # 增量模式：本页出现水位线 = 它之后的内容上轮已全有，可以停
            if incremental and any(r["tid"] == wm_tid for r in recs):
                log("page %d 命中水位线 tid=%s（上一轮最新一条）→ 之后的内容已获取，停止" % (pg, wm_tid))
                hit_wm = True
                break
        elif v == "end":
            log("page %d -> end（超出末页），停止" % pg)
            break
        else:
            log("page %d -> fail（留待下轮重试）" % pg)

    log("抓取结束：成功 %d 页，登录墙 %d 次%s"
        % (ok_n, hit_login, "，水位线停止" if hit_wm else ""))
    if hit_login:
        log("提示：游客上限约 100 页。把浏览器里 t66y 的 Cookie 整行写进 %s 后可继续。" % P.COOKIE)
    if incremental and not hit_wm and not hit_login:
        log("提示：翻到 %d 页仍未命中水位线 tid=%s —— 新帖量超过本轮翻页范围，"
            "或水位线帖已被删除/移出版面。建议再跑一次或 --full 核对。" % (end, wm_tid))


if __name__ == "__main__":
    main()
