# -*- coding: utf-8 -*-
"""
s02b_fetch_pagenav.py — 文章内部分页（WordPress nextpage）逐页抓取

背景：一批「福利汇总（1024）」等长文被站点用 nextpage 拆成
  /{slug}.html, /{slug}.html/2, /{slug}.html/3 …（正文底部分页条 .article-paging）。
sitemap 与列表页分页（s01b）给的都只是第 1 页 → 第 2 页及以后的正文与图片全部漏抓。
实测（严格签名：本文 slug 的 .article-paging 链接）：1435 篇带内部分页，最深 13 页，需补 2285 子页。

原则：**按页分开存**，与站点的分页结构一一对应（离线站据此分页展示，不合并成一页）。
  - 第 1 页仍是 _rawdata/html/<key>.html（s02 抓的）。
  - 第 N(>=2) 页存到 _pipeline/subpages/<key[:2]>/<key>__p<N>.html。
  - 幂等：子页文件已存在则不重抓（--redo 覆盖）。
  - 溢出兜底：正文分页条给出最大页码 N；若某子页内容与上一页完全相同则判为溢出并停止。

用法：
  _scripts\\py.cmd _scripts\\collect\\s02b_fetch_pagenav.py --scan        只统计受影响文章与子页数，零网络
  _scripts\\py.cmd _scripts\\collect\\s02b_fetch_pagenav.py --limit=5      试跑前 5 篇
  _scripts\\py.cmd _scripts\\collect\\s02b_fetch_pagenav.py                全量抓取子页（~2285 请求，串行限速）
  _scripts\\py.cmd _scripts\\collect\\s02b_fetch_pagenav.py --only=<slug>  只处理指定文章（调试）
  _scripts\\py.cmd … --keys-file=<路径>                                只处理本轮增量范围那几篇

为什么范围模式重要：不带任何参数时，本脚本会对 urls.json 的七千篇**逐篇读原始 HTML**
（每篇 ~150KB）去找正文分页条，实测 84.8 秒里只有 8 秒是真请求。新文章必然带着
分页条、旧文章的分页结构又不会自己变，所以增量轮只需要探本轮新增的那几篇。
例外：旧文章真的多出一页时，本脚本会抓到新子页并 drop_parsed，此时必须把该篇
**追加回范围**（C.scope_add），否则 s03/s04d/s06 不会重做它，库里就半新半旧。
"""
import os
import re
import sys
import time
import glob
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # _scripts/
import common as C

SUBPAGE_DIR = None      # 年度化后子页不再单文件，存进年度容器的 sub 段（years.py）


def own_max_page(html_text, slug):
    """从正文分页条取本文 slug 的最大页码；<2 表示不分页。"""
    if 'class="article-paging"' not in html_text:
        return 0
    pgs = set(int(x) for x in re.findall(re.escape(slug) + r"/(\d+)", html_text))
    pgs.discard(1)
    return max(pgs) if pgs else 0


def stored_subs(url, key):
    """已缓存的子页：{页码: 正文}（从年度容器读）。"""
    return dict((s.get("n"), s.get("html") or "") for s in C.Y.read_subpages(url=url, key=key))


def existing_max_stored(url, key):
    """已缓存的最大子页号（用于断点续跑与统计）。"""
    mx = 1
    for n in stored_subs(url, key):
        if n:
            mx = max(mx, int(n))
    return mx


def scan_all():
    urls = C.read_json(os.path.join(C.SRC, "urls.json"), {}) or {}

    def one(kv):
        u, _m = kv
        if not C.Y.has_raw(url=u, key=C.url_key(u)):
            return None
        t = C.Y.read_raw(url=u)
        if not t:
            return None
        mx = own_max_page(t, u.rstrip("/").split("/")[-1])
        return (u, mx) if mx >= 2 else None

    hits = []
    with ThreadPoolExecutor(max_workers=16) as ex:
        for r in ex.map(one, urls.items()):
            if r:
                hits.append(r)
    need = sum(mx - 1 for _u, mx in hits)
    C.log("[s02b] 受影响文章 %d 篇 | 最深 %d 页 | 需抓子页 %d 个（已缓存的会跳过）"
          % (len(hits), max((m for _u, m in hits), default=0), need))
    return hits


def fetch_article(sess, pacer, key, url, redo=False):
    """抓一篇文章的第 2..N 页；返回 (抓到的新子页数, 已存最大页号)。

    已存页号顺带带出，免得 main() 再读一遍容器（每篇读一次就够）。
    """
    page1 = C.Y.read_raw(url=url, key=key)
    if not page1:
        return (0, 1)
    slug = url.rstrip("/").split("/")[-1]
    mx = own_max_page(page1, slug)
    if mx < 2:
        return (0, 1)

    have = {} if redo else stored_subs(url, key)
    prev_sig = None
    got = 0
    for n in range(2, mx + 1):
        sub_url = "%s/%d" % (url.rstrip("/"), n)
        if n in have:
            # 已缓存：读取以维持「与下一页比较」的签名链
            prev_sig = _content_sig(have[n])
            continue
        try:
            r, txt = C.fetch(sess, sub_url, pacer, tries=3, ref=url, timeout=45)
        except C.CircuitBreaker:
            raise
        if not r or not txt:
            break
        sig = _content_sig(txt)
        if prev_sig is not None and sig == prev_sig:
            # 溢出：站点返回了与上一页相同的内容，说明没有第 n 页
            break
        C.Y.write_subpage(url, n, txt)
        have[n] = txt
        prev_sig = sig
        got += 1
    if got:
        # 新抓到子页 → 只抹 parsed 段（raw 与子页保留），强制 s03 重解析组装 pages
        C.Y.drop_parsed(url, key=key)
    return (got, max(have) if have else 1)


_CONTENT_RE = re.compile(r'class="article-content[^"]*"[^>]*>(.*?)</article>', re.S)


def _content_sig(html_text):
    """取正文容器内容的规整签名，用于溢出判重（只比正文，忽略动态页头/推荐位）。"""
    m = _CONTENT_RE.search(html_text)
    body = m.group(1) if m else html_text
    # 去掉脚本/样式与多余空白，压成一行
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", body, flags=re.S | re.I)
    body = re.sub(r"\s+", " ", body)
    import hashlib
    return hashlib.md5(body.encode("utf-8", "replace")).hexdigest()


def main():
    args = sys.argv[1:]
    flag = lambda n: ("--" + n) in args
    opt = lambda n, d=None: next((a.split("=", 1)[1] for a in args if a.startswith("--" + n + "=")), d)

    if flag("scan"):
        scan_all()
        return 0

    limit = int(opt("limit", 0)) if opt("limit") else 0
    only = opt("only")
    redo = flag("redo")

    urls = C.read_json(os.path.join(C.SRC, "urls.json"), {}) or {}
    items = sorted(urls.items())
    if only:
        items = [(u, m) for u, m in items if u.rstrip("/").split("/")[-1] == only.strip("/")]
        if not items:
            C.log("[s02b] 没有 slug=%s" % only)
            return 2
    scope_path = C.scope_arg()
    scope = C.scope_read(scope_path)
    if scope:
        items = [it for it in items if it[0] in scope]

    use_proxy = not flag("no-proxy")
    sess = C.make_session(proxy=use_proxy)
    pacer = C.Pacer(base=1.2, jitter=0.8, rest_every=50, rest_secs=3.0, name="pagenav")

    C.log("[s02b] 开始逐页抓子页（候选 %d 篇，只处理带分页的%s）"
          % (len(items), "，范围模式" if scope else ""))
    arts = subs = skip = 0
    t0 = time.time()
    processed = 0
    for url, _meta in items:
        if limit and processed >= limit:
            break
        key = C.url_key(url)
        try:
            got, existing = fetch_article(sess, pacer, key, url, redo=redo)
        except C.CircuitBreaker as e:
            C.log("[s02b] 熔断停止：%s（重跑可续，已抓子页不重请）" % e)
            break
        if existing >= 2 or got:
            arts += 1
            subs += got
            processed += 1
            if got:
                C.state_append("pagenav", url, "SUBPAGES", new=got)
                # 抓到新子页 → 本篇的 parsed 已被作废，必须回到本轮范围里
                # 让 s03/s04d/s06 重做它（全树模式下这行什么都不做）
                if C.scope_add(scope_path, url):
                    C.log("[s02b] 旧文章 %s 新增 %d 个子页，已追加进本轮范围" % (url, got))
        if arts and arts % 100 == 0:
            C.log("[s02b] 分页文章累计 %d | 本次新抓子页累计 %d (%.0fs)"
                  % (arts, subs, time.time() - t0))

    total_sub = None if scope else C.Y.total_subpages()
    C.log("[s02b] 完成：分页文章 %d 篇，本次新抓子页 %d 个%s"
          % (arts, subs,
             "（范围模式：全树累计子页未统计）" if total_sub is None
             else "，累计已存子页 %d 个" % total_sub))
    print("S02B_OK %d %d" % (arts, subs))
    if subs:
        C.log("[s02b] 下一步：_scripts\\py.cmd _scripts\\build\\update.py 走 s03→s04i→…→s06 组装分页并建站")
    return 0


if __name__ == "__main__":
    sys.exit(main())
