# -*- coding: utf-8 -*-
"""
s01b_collect_pages.py — 分页列表页补抓（sitemap 之外的遗漏文章）

为什么要单独一步：s01 只吃 sitemap 的「文章月分片」，而分页列表页
（首页 `/page/N`、分类 `/xxx/` 与其 `/xxx/page/N`）是另一套发现通道。
实测两边集合不一致：首页/分类第 1~3 页里就有 sitemap 没有的文章，
深层归档页里同样有（侧栏「热点/特别推荐」也会带出早年文章）。

入口（entry）：
  1) `/`                     —— 全站时间线，实测「共 372 页」
  2) `/xxx/`                 —— 28 个分类归档页，取自 parsed 分片的 category_url

抓取约定：
  - 页数上限取自分页条的「共 N 页」；N+1 页只请求一次做溢出确认
    （实测 `/page/99999`、`/fuli/page/161` 返回 200 + 末页同内容，
      不能靠 404 判停，否则永远停不下来）。
  - 列表页 HTML 缓存到 `_rawdata/pages/<分区>/<页码>.html`，命中即不再请求；
    `--refresh-days=N` 把 N 天前的缓存改名成 .old 再重抓（.old 天然不参与命中）。
  - 只发现 URL，不抓正文；正文仍由 s02→s03→s04i 流水线处理（幂等跳过）。

用法：
  _scripts\\py.cmd _scripts\\collect\\s01b_collect_pages.py --stats            只看规模，零网络
  _scripts\\py.cmd _scripts\\collect\\s01b_collect_pages.py --migrate-cache     缓存改名（哈希桶→分区/页码）试算
  _scripts\\py.cmd _scripts\\collect\\s01b_collect_pages.py --migrate-cache --apply   真改名
  _scripts\\py.cmd _scripts\\collect\\s01b_collect_pages.py --dry              扫描并打印差集，不写 urls.json
  _scripts\\py.cmd _scripts\\collect\\s01b_collect_pages.py --quick           只翻各归档第 1 页（增量更新用，~30 请求）
  _scripts\\py.cmd _scripts\\collect\\s01b_collect_pages.py                   全部分页穷举（首次补漏用，~860 请求）
  _scripts\\py.cmd _scripts\\collect\\s01b_collect_pages.py --refresh-days=7  重抓 7 天前的列表页
  _scripts\\py.cmd _scripts\\collect\\s01b_collect_pages.py --prefix=dytj --max-pages=5   只翻指定前缀

合并进 urls.json 的条目形如：
  {"lastmod":"", "sitemap":"", "source":"pages",
   "first_seen_pages":["/","/fuli/"], "found_at":"2026-10-25"}
"""
import os
import re
import sys
import time
import hashlib
from urllib.parse import unquote

sys.stdout.reconfigure(errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # _scripts/
import common as C

PAGES_DIR = C.PAGES_DIR          # 列表页发现缓存：_rawdata/pages/<分区>/<页码>.html
URLS = os.path.join(C.SRC, "urls.json")
CATS_CACHE = os.path.join(C.SRC, "categories.json")
PENDING_NAME = "urls_pages_pending.json"   # 逐轮累积的待补差集（滚动快照，写当天日期目录）

# 列表页里的文章链接：形如 href="/xxx.html"（相对路径），可带 ?query 或 #frag
ART_HREF = re.compile(r'href="(/[a-z0-9A-Z_%.\-]+\.html)(?:[?#][^"]*)?"')
TOTAL_PAGES = re.compile(r'共\s*(\d+)\s*页')
NON_ARTICLE = re.compile(r'/(?:page|tag|category|feed|wp-|xmlrpc|app\.php|member\.php|plugin\.php)', re.I)
# 溢出页自延伸硬上限：列表页含轮播/推荐位等动态元素，整页哈希几乎不可能
# 完全相同，不设上限会「共 N 页 + 1」无限翻下去（实测曾把 372 页跑到越收越多）。
MAX_EXTEND = 5


def norm(path):
    """列表页 URL 归一：补 BASE、分类页统一带尾斜杠、分页页去掉多余后缀。"""
    if not path.startswith("/"):
        path = "/" + path
    return C.BASE + path


def page_url(prefix, n):
    """prefix 归一后拼第 n 页；n<=1 返回归一后的首页 URL（分类页带尾斜杠）。"""
    p = prefix if prefix.startswith("/") else "/" + prefix
    p = p.split("?")[0].rstrip("/")
    if n <= 1:
        return C.BASE + p + ("/" if p else "")
    return C.BASE + p + "/page/%d" % n


def cache_parts(url):
    """列表页 URL → (分区名, 页码)。

    分区名就是归档前缀（`fuli`/`动漫影视`…），首页时间线用 `_home`；先 unquote 再清洗，
    所以 `/公告/page/3` 与 `/%E5%85%AC%E5%91%8A/page/3` 归到同一个位置（旧缓存里两种形状都有）。
    旧方案是 `pages/<sha1[:2]>/<sha1[:16]>.html`：为 14 万个图设计的 256 个哈希桶
    用在 4 千个文件上（平均每桶 17 个），既白付 256 个目录，又让人看不出
    “哪个归档翻到哪一页、缓存新不新”（2026-10-31 改）。
    """
    path = url[len(C.BASE):] if url.startswith(C.BASE) else url
    m = re.search(r"/page/(\d+)/?$", path)
    n = int(m.group(1)) if m else 1
    head = ((path[:m.start()] if m else path) or "").strip("/")
    if head in ("", "page"):
        part = "_home"
    else:
        head = unquote(head)
        part = re.sub(r"[^0-9A-Za-z_.\-\u4e00-\u9fff]+", "_", head.replace("/", "__"))
    return part or "_home", n


def page_cache_path(url):
    part, n = cache_parts(url)
    d = os.path.join(PAGES_DIR, part)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "%d.html" % n)


def legacy_cache_path(url):
    """旧哈希命名位置（仅诊断用：排查某个 URL 当年落在哪个旧桶）。"""
    key = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    return os.path.join(PAGES_DIR, key[:2], key + ".html")


def is_expired(path, refresh_days):
    """缓存超过 refresh_days 天视为过期（需重抓）。文件不存在不算过期。"""
    if not os.path.exists(path):
        return False
    if refresh_days is None:
        return False
    return time.time() - os.path.getmtime(path) > refresh_days * 86400


def cached_body(path, refetch=False):
    """命中则返回页面文本；需要重抓时把旧文件改名 .old 后返回 None。

    refetch=True（--quick 的第 1 页）无视缓存直接重抓 —— 新文章只会出现在
    各归档第 1 页，缓存着的第 1 页等于盲区；.old 不参与命中，留作回溯。
    """
    if not os.path.exists(path):
        return None
    if refetch:
        try:
            os.replace(path, path + ".old")
        except OSError:
            pass
        return None
    if os.path.getsize(path) < 500:
        return None
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def article_links(body):
    out = set()
    for m in ART_HREF.finditer(body or ""):
        href = m.group(1)
        if NON_ARTICLE.search(href):
            continue
        out.add(C.BASE + href)
    return out


def known_urls():
    urls = set(C.read_json(URLS, {}) or {})
    gone_p = os.path.join(C.SRC, "urls_gone.json")
    for it in (C.read_json(gone_p, []) or []):
        u = (it or {}).get("url")
        if u:
            urls.add(u)
    return urls


def discover_categories():
    """从年度容器的解析产物收集 (名称, 归档页路径)，缓存到 _pipeline/categories.json。"""
    cached = C.read_json(CATS_CACHE)
    if cached:
        return [(c["name"], c["path"]) for c in cached]
    pairs = {}
    for _url, _key, _slot, art in C.Y.iter_parsed():
        cu = art.get("category_url") or ""
        if not cu:
            continue
        path = cu.replace(C.BASE, "")
        if not path.startswith("/"):
            continue
        pairs[path] = art.get("category") or path.strip("/")
    items = [{"name": v, "path": k} for k, v in sorted(pairs.items())]
    if items:      # 无解析产物时不写缓存，否则下次拿到空清单还以为已收集
        C.write_json(CATS_CACHE, items)
    C.log("[s01b] 分类归档页 %d 个 → %s" % (len(items), CATS_CACHE))
    return [(i["name"], i["path"]) for i in items]


def looks_like_list(body):
    """拓到的必须真是列表页：带 canonical 且有文章链接/分页条。

    实测（2026-10-31）：代理不通时 Discuz 会回 **200 + 14,007 字节的「提示信息」页**，
    旧代码照单全收，旧缓存里积了 29 个这种垃圾页（它们没有任何文章链接，
    等于那个归档页“拓过了但没有新文”，会静默弄断发现通道）。
    """
    if not body or len(body) < 2000:
        return False
    if 'rel="canonical"' not in body:
        return False
    return bool(article_links(body)) or bool(TOTAL_PAGES.search(body)) \
        or "page-numbers" in body


def collect_prefix(sess, pacer, prefix, max_pages, refresh_days, quick=False):
    """翻完一个归档的前缀，返回 (发现的文章 URL 集合, 实际抓取页数, 失败页数, 总页数)。"""
    found = set()
    fetched = failed = 0
    first = page_url(prefix, 1)
    fpath = page_cache_path(first)
    # 第 1 页：--quick 或缓存过期时重抓 —— 新文章只会出现在各归档第 1 页，
    # 吃缓存的第 1 页等于盲区；深层归档页相对静态，过期才重抓。
    body = cached_body(fpath, refetch=quick or is_expired(fpath, refresh_days))
    if body is None:
        body = get(sess, first, pacer)
        fetched += 1
        if body is None:
            failed += 1
            return found, fetched, failed, 0
        if not looks_like_list(body):
            C.log("[s01b] %s 返回的不是列表页（无 canonical/文章链接），不写缓存、计为失败" % first)
            return found, fetched, failed + 1, 0
        C.write_text(fpath, body)
    total = None
    m = TOTAL_PAGES.search(body)
    if m:
        total = int(m.group(1))
    if max_pages and total:
        total = min(total, max_pages)
    found |= article_links(body)
    if quick or total in (None, 1):
        return found, fetched, failed, total or 1

    overflow_done = False
    declared = total or 1
    last_hash = hashlib.sha1(body.encode("utf-8")).hexdigest()
    n = 2
    while total and n <= total + 1:
        beyond = n > declared
        if beyond and overflow_done:
            break
        url = page_url(prefix, n)
        path = page_cache_path(url)
        b = cached_body(path, refetch=is_expired(path, refresh_days))
        if b is None:
            b = get(sess, url, pacer)
            fetched += 1
            if b is None:
                failed += 1
                if beyond:
                    overflow_done = True
                n += 1
                continue
            if not looks_like_list(b):
                C.log("[s01b] %s 返回的不是列表页，不写缓存、计为失败" % url)
                failed += 1
                if beyond:
                    overflow_done = True
                n += 1
                continue
            C.write_text(path, b)
        if beyond:
            # 溢出页且已翻到自延伸上限 → 无条件到底（不靠内容比较）。
            # 溢出页与末页同内容 → 确认到底；确实不同才把上限抬一页。
            if n >= declared + MAX_EXTEND:
                overflow_done = True
            else:
                h = hashlib.sha1(b.encode("utf-8")).hexdigest()
                overflow_done = (h == last_hash)
                if not overflow_done:
                    total = n
        else:
            last_hash = hashlib.sha1(b.encode("utf-8")).hexdigest()
        found |= article_links(b)
        n += 1
    return found, fetched, failed, total or 1


def get(sess, url, pacer):
    try:
        r, txt = C.fetch(sess, url, pacer, tries=3, ref=C.BASE + "/", timeout=45)
    except C.CircuitBreaker as e:
        C.log("[s01b] 熔断：%s" % e)
        raise
    if r is None:
        C.log("[s01b] 获取失败 %s" % url)
        return None
    return txt


def merge(new_urls, sources, dry):
    """把新发现 URL 合并进 urls.json；dry 时只写待补台账。

    台账是**逐轮累积的滚动快照**：读最近一份（通常已含全量），合并后写今天的
    日期目录 _pipeline/<YYYYMMDD>/urls_pages_pending.json（update.py 靠 pipeline_find 读最新一份）。
    """
    urls = C.read_json(URLS, {}) or {}
    _prev = C.pipeline_find(PENDING_NAME)
    pend = (C.read_json(_prev, {}) or {}) if _prev else {}
    today = time.strftime("%Y-%m-%d")
    for u in sorted(new_urls):
        entry = {"lastmod": "", "sitemap": "", "source": "pages",
                 "first_seen_pages": sorted(sources.get(u, []))[:4],
                 "found_at": today}
        pend[u] = entry
        if not dry and u not in urls:
            urls[u] = entry
    pend_path = C.pipeline_rolling_path(PENDING_NAME)
    C.write_json(pend_path, pend)
    if not dry:
        C.write_json(URLS, urls)
    C.log("[s01b] 待补 %d 条 → %s%s" % (len(new_urls), pend_path,
                                       "" if dry else "；已合并 → %s（现共 %d 条）" % (URLS, len(urls))))
    return len(new_urls)


# 枚举上限：首页 372 页 + 分类最长归档页 + 溢出自延伸（MAX_EXTEND），800 足够覆盖
MAX_ENUM_PAGES = 800


def canonical_url(path):
    """从缓存页面里取它自己的 canonical（当时拓下来的原始 URL）。

    旧文件名是 sha1(url)，不可逆；而枚举前缀×页码回推不靠谱（旧缓存里既有
    `/公告/page/3` 又有 `/%E5%85%AC%E5%91%8A/page/3` 两种形状），所以直接读页面自带的
    rel="canonical" —— 它是唯一可靠、逐文件精确的来源。
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            head = f.read(8000)
    except OSError:
        return None
    m = re.search(r'rel="canonical"\s+href="([^"]+)"', head) or \
        re.search(r'href="([^"]+)"\s+rel="canonical"', head)
    return m.group(1).strip() if m else None


def migrate_cache(dry=False):
    """把旧哈希命名的列表页缓存改名为 `<分区>/<页码>.html`，并清掉顶包副本。

    三步：
    1) 枚举「归档前缀 × 页码」算出 URL（与拓取时同一个 page_url），按**请求 URL** 搬迁；
    2) 剩下的用页面自带的 canonical 定位（编码/解码两种 URL 形状同一个页），目标已存在则视为重复；
    3) 仍留在旧桶里的（重复副本 / 顶包页）直接删——它们已不可能被新的按 URL 定名的查找命中。

    实测发现：旧缓存 4,486 个里有 3,467 个是**同一页**（canonical 均为 /dytj/page/40），
    即当年 --deep 后期站点把同一内容回给了所有后续请求，旧代码无校验地全存了下来。
    """
    buckets = [d for d in sorted(os.listdir(PAGES_DIR))
               if os.path.isdir(os.path.join(PAGES_DIR, d)) and re.match(r"^[0-9a-f]{2}$", d)]
    total_old = 0
    for b in buckets:
        total_old += len([f for f in os.listdir(os.path.join(PAGES_DIR, b))
                         if f.endswith(".html")])

    def move(old, url):
        part, page = cache_parts(url)
        new = os.path.join(PAGES_DIR, part, "%d.html" % page)
        if os.path.exists(new):
            return None
        if not dry:
            os.makedirs(os.path.dirname(new), exist_ok=True)
            os.rename(old, new)
        return new

    # 1) 按请求 URL 枚举
    by_url = 0
    for prefix in ["/"] + [p for _n, p in discover_categories()]:
        for n in range(1, MAX_ENUM_PAGES + 1):
            u = page_url(prefix, n)
            old = legacy_cache_path(u)
            if os.path.exists(old) and move(old, u) is not None:
                by_url += 1
    # 2) 按页面自带 canonical 定位
    by_canon = dup = junk = 0
    for b in buckets:
        d = os.path.join(PAGES_DIR, b)
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if not f.endswith(".html"):
                continue
            old = os.path.join(d, f)
            cu = canonical_url(old)
            if cu and cu.startswith(C.BASE) and move(old, cu) is not None:
                by_canon += 1
                continue
            # 目标已存在 = 同一页的重复副本；无 canonical = 顶包页。两者都是垃圾。
            junk += 1
            if not dry:
                try:
                    os.remove(old)
                except OSError:
                    pass
        if not dry:
            rest = os.listdir(d)
            if not rest:
                try:
                    os.rmdir(d)
                except OSError:
                    pass
                else:
                    dup += len(rest)
            else:
                dup += len(rest)
    kept = len([p for p in os.listdir(PAGES_DIR) if os.path.isdir(os.path.join(PAGES_DIR, p))
                and not re.match(r"^[0-9a-f]{2}$", p)])
    files_new = 0
    for p in os.listdir(PAGES_DIR):
        q = os.path.join(PAGES_DIR, p)
        if os.path.isdir(q):
            files_new += len([f for f in os.listdir(q) if f.endswith(".html")])
    C.log("[s01b] %s：旧缓存 %d 个 / %d 个旧桶 → 按 URL 搬迁 %d，按 canonical 归位 %d，"
          "重复副本与顶包页删除 %d；现有分区 %d 个 / %d 个文件"
          % ("试算" if dry else "完成", total_old, len(buckets), by_url, by_canon, junk,
             kept, files_new))
    left = total_old - by_url - by_canon - junk
    if left > 0:
        C.log("[s01b] 提示：旧桶里还有 %d 个文件因目标名已占用而未动" % left)
    if dry:
        C.log("[s01b] 试算结束，加 --apply 才真改名/真删除")
    return 0


def stats():
    urls = C.read_json(URLS, {}) or {}
    cats = discover_categories()
    n_pages = 0
    for path in os.listdir(PAGES_DIR) if os.path.isdir(PAGES_DIR) else []:
        d = os.path.join(PAGES_DIR, path)
        if os.path.isdir(d):
            n_pages += len([f for f in os.listdir(d) if f.endswith(".html")])
    C.log("[s01b] urls.json %d 条 | 分类归档 %d 个 + 首页 1 个 | 列表页缓存 %d 个"
          % (len(urls), len(cats), n_pages))
    C.log("[s01b] 穷举预估：首页分页 + 28 个分类分页，实测 878 次请求 / 约 25 分钟（缓存命中后重跑只几个溢出页）")
    return 0


def main():
    args = sys.argv[1:]
    flag = lambda name: ("--" + name) in args
    opt = lambda name, dflt=None: next((a.split("=", 1)[1] for a in args
                                        if a.startswith("--" + name + "=")), dflt)
    if flag("stats"):
        return stats()
    if flag("migrate-cache"):
        C.log("[s01b] 列表页缓存改名：哈希桶 → `<分区>/<页码>.html`")
        return migrate_cache(dry=not flag("apply"))

    refresh_days = 30
    rv = opt("refresh-days")
    if rv is not None:
        refresh_days = None if rv in ("0", "never") else float(rv)
    max_pages = None
    if opt("max-pages") is not None:
        max_pages = int(opt("max-pages"))
    only_prefix = opt("prefix")
    quick = flag("quick")

    C.log("[s01b] 开始分页补抓 quick=%s max_pages=%s refresh_days=%s proxy=%s"
          % (quick, max_pages, refresh_days, "on" if not flag("no-proxy") else "off"))

    use_proxy = not flag("no-proxy")
    sess = C.make_session(proxy=use_proxy)   # 代理不通时 make_session 自动降级直连
    pacer = C.Pacer(base=1.2, jitter=0.8, rest_every=50, rest_secs=3.0, name="pages")

    known = known_urls()
    entries = [("/", "首页（全站时间线）")]
    for name, path in discover_categories():
        entries.append((path, name))
    if only_prefix:
        entries = [e for e in entries if e[0].strip("/") == only_prefix.strip("/%")]
        if not entries:
            C.log("[s01b] 没有前缀 %s（--stats 可看全部归档）" % only_prefix)
            return 2

    os.makedirs(PAGES_DIR, exist_ok=True)
    logp = C.log_path("s01b_pages.log")     # 本轮扫描进度：_logs/<今天>/

    def progress(line):
        try:
            with open(logp, "w", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    all_found = set()
    sources = {}
    tot_fetch = tot_fail = 0
    t0 = time.time()
    for i, (prefix, name) in enumerate(entries, 1):
        try:
            found, nf, nfail, total = collect_prefix(
                sess, pacer, prefix, max_pages, refresh_days, quick=quick)
        except C.CircuitBreaker:
            C.log("[s01b] 熔断退出，保留已发现部分（重跑可续，缓存命中的页不再请求）")
            break
        tot_fetch += nf
        tot_fail += nfail
        new = found - known
        for u in new:
            sources.setdefault(u, set()).add(prefix if prefix != "/" else "/")
        all_found |= new
        progress("[%s] %d/%d %-16s 页数=%s 抓到=%d 失败=%d 累计发现新文章=%d"
                 % (time.strftime("%H:%M:%S"), i, len(entries), name, total, nf,
                    nfail, len(all_found)))
        if nf or i == len(entries):
            C.log("[s01b] %d/%d %-12s 共%s页 本次抓%d 失败%d 新增%d 累计%d (%.0fs)"
                  % (i, len(entries), name, total, nf, nfail, len(new),
                     len(all_found), time.time() - t0))

    C.log("[s01b] 扫描完成：请求 %d 页，失败 %d 页，新发现 %d 篇（urls.json 之外）"
          % (tot_fetch, tot_fail, len(all_found)))
    for u in sorted(all_found)[:40]:
        C.log("[s01b]   + %s  ← %s" % (u, ",".join(sorted(sources.get(u, set())))))
    if len(all_found) > 40:
        C.log("[s01b]   ... 其余 %d 篇见 %s" % (len(all_found) - 40,
                                                C.pipeline_find(PENDING_NAME) or "（待补台账未生成）"))

    n = merge(all_found, {k: list(v) for k, v in sources.items()}, flag("dry"))
    C.state_append("pages", "all", "DONE", found=n, fetched=tot_fetch, failed=tot_fail,
                   quick=quick, secs=int(time.time() - t0))
    if not flag("dry") and n:
        C.log("[s01b] 下一步：_scripts\\py.cmd _scripts\\build\\update.py 走 s02→s06 落地这些文章")
    return 0


if __name__ == "__main__":
    sys.exit(main())
