# -*- coding: utf-8 -*-
"""
s03_parse.py — 解析文章：正文（滤广告）/ 元信息 / 评论树 / 图片清单
产出：_pipeline/articles.json  {url: {...}}
关键规则：
- 正文容器 article.article-content；兼容旧页面（按 selector 回退）
- 广告剔除：class/id 命中广告特征；正文外广告不取
- 图片还原：sogoucdn 包装 → 真实原图；协议相对 → https
- 评论：#postcomments > ol.commentlist，保留 ul.children 嵌套 → parent_id
"""
import html as htmlmod
import os
import re
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # _scripts/
import common as C

from lxml import html as LH

# 广告特征（大小写不敏感，匹配 class 或 id）
AD_CLASS_RE = re.compile(
    r"(adsbygoogle|pub_\d+x\d+|text[_-]?ad|ad[_-]?text|adsbox|ad[_-]?placement|"
    r"ad[_-]?links|sponsored|advertisement|banner[_-]?ad|doubleclick|ad[_-]?banner|"
    r"google_ads|adsense)", re.I)
AD_ID_RE = re.compile(r"(dux-ads-sentinel|google_ads|adsense|ad-container|ad-wrapper)", re.I)

# 明显属于推广/广告的元素
PROMO_TAG_RE = re.compile(r"^(script|style|noscript)$", re.I)

CONTENT_SELECTORS = [
    ("article", "article-content"),
    ("div", "post-content"),
    ("div", "entry-content"),
    ("div", "article-content"),
    ("div", "single-content"),
    ("div", "content-wrap-inner"),
]

# 站点对部分内容做了二次转码（GBK 文本当 UTF-8 存储），表现为 '姝ｅ簭' 这类字符
MOJIBAKE_MARK = re.compile(r"[\u00a5\u00b7\u00ba\u00c5\u00d4\u00e3\u4eae\u6c5f\u70b9"
                            r"\u9352\u934b\u9435\u9a9e\u9f9b\u95ab\u93b6\u951b\u93d5\u9358"
                            r"\u7e2f\u9519\u934f\u93c9\u941d\u9536\u9510\u951b\u942d]")


def _cjk_ratio(t):
    if not t:
        return 0.0
    cjk = sum(1 for ch in t if 0x4E00 <= ord(ch) <= 0x9FFF)
    kana = sum(1 for ch in t if 0x3040 <= ord(ch) <= 0x30FF)
    return (cjk + kana) / float(len(t))


# 还原失败则回退的原长缓冲：单次尝试的候选长度上限
_MOJI_MAX_SPAN = 64


def _try_decode(seg):
    """把一段疑似被转码的文本按 gb18030->utf8 还原，失败返回 None。"""
    try:
        cand = seg.encode("gb18030").decode("utf-8")
    except Exception:
        return None
    if not cand or _cjk_ratio(cand) <= 0.2:
        return None
    return cand


def fix_mojibake(s):
    """修复站点二次转码造成的中文乱码。

    实测方向：'姝ｅ簭'.encode('gb18030').decode('utf-8') == '正序'
    站点把 GBK 字节按 UTF-8 存了一次，导致原文变乱码，反向即可还原。

    性能：先按「疑似乱码字符」切分出**连续片段**，只对片段做还原，
    而非对整篇文本逐字符尝试（后者需 2.4s/篇，全量 7420 篇要 5 小时）。
    """
    if not s:
        return s
    if not MOJIBAKE_MARK.search(s):
        return s

    # 1) 按连续乱码片段切分
    spans = []          # (start, end)
    i = 0
    n = len(s)
    while i < n:
        if MOJIBAKE_MARK.match(s[i]):
            j = i
            while j < n and (MOJIBAKE_MARK.match(s[j]) or
                             (j > i and s[j] in "锛銆鈥愨紝鐨勭紝锟斤拷")):
                j += 1
            spans.append((i, j))
            i = j
        else:
            i += 1
    if not spans:
        return s

    out = []
    prev = 0
    for (a, b) in spans:
        out.append(s[prev:a])
        seg = s[a:b]
        # 2) 片段内贪心：长片段优先，整段失败再退化为逐字
        done = False
        for L in range(min(len(seg), _MOJI_MAX_SPAN), 0, -1):
            for start in range(0, len(seg) - L + 1, max(1, L // 2)):
                cand = _try_decode(seg[start:start + L])
                if cand:
                    out.append(cand)
                    if start + L < len(seg):
                        out.append(seg[start + L:])
                    done = True
                    break
            if done:
                break
        if not done:
            out.append(seg)
        prev = b
    out.append(s[prev:])
    return "".join(out)


def parse_img_url(src):
    """还原图片真实 URL：sogoucdn 代理 → 原图；协议相对 → https。"""
    if not src:
        return None
    src = src.strip()
    if src.startswith("//"):
        src = "https:" + src
    elif src.startswith("/"):
        src = C.BASE + src
    try:
        q = urllib.parse.urlparse(src)
    except Exception:
        return src
    host = (q.netloc or "").lower()
    if "sogoucdn.com" in host:
        qs = urllib.parse.parse_qs(q.query)
        real = qs.get("url") or []
        if real:
            real = real[0]
            if real.startswith("//"):
                real = "https:" + real
            elif not real.startswith("http"):
                real = "https://" + real
            return real
    return src


def node_has_ad(el):
    cls = el.get("class") or ""
    if AD_CLASS_RE.search(cls):
        return True
    eid = el.get("id") or ""
    if AD_ID_RE.search(eid):
        return True
    return False


def clean_tree(el):
    """就地剔除广告与脚本节点，返回净化后的元素。"""
    for bad in el.xpath(".//script|.//style|.//noscript|.//iframe[@src='']"):
        bad.getparent().remove(bad)
    for bad in el.xpath(".//*[@class or @id]"):
        if bad.getparent() is None:
            continue
        if node_has_ad(bad):
            bad.getparent().remove(bad)
    return el


def inner_html(el):
    parts = []
    if el.text:
        parts.append(el.text)
    for ch in el:
        try:
            parts.append(LH.tostring(ch, encoding="unicode"))
        except Exception:
            pass
        if ch.tail:
            parts.append(ch.tail)
    return "".join(parts)


def html_to_text(h):
    if not h:
        return ""
    s = re.sub(r"<br\s*/?>", "\n", h, flags=re.I)
    s = re.sub(r"</(p|div|li|h[1-6]|blockquote|tr)>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = htmlmod.unescape(s)
    s = re.sub(r"[ \t\u00a0]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _abs_url(href):
    """把 href/src 绝对化。自行实现以避开 lxml 的慢路径与异常开销。

    lxml 的 make_links_absolute 对本站在每个 URL 上调用 urljoin，
    遇到大量畸形 href 会反复抛异常，实测占解析总耗时 96%（2.26s/篇）。
    """
    if not href:
        return href
    h = href.strip()
    if not h:
        return href
    low = h.lower()
    if low.startswith(("http://", "https://", "data:", "javascript:", "mailto:", "tel:", "#")):
        return h
    if h.startswith("//"):
        return "https:" + h
    if h.startswith("/"):
        return C.BASE + h
    return C.BASE + "/" + h


def make_links_absolute_safe(tree):
    """安全绝对化：站点 HTML 中存在形如 '[xxx]:port' 的畸形 href，
    lxml 的 make_links_absolute 会因 IPv6 校验抛 ValueError。
    这里自实现绝对化并跳过非法 URL。"""
    for a in tree.xpath("//a[@href]"):
        h = a.get("href")
        if not h:
            continue
        s = h.strip()
        if s.startswith("[") or "]:" in s or " " in s:
            a.set("href", C.BASE + "/")
            continue
        a.set("href", _abs_url(s))
    for im in tree.xpath("//img[@src]"):
        s = im.get("src")
        if not s:
            continue
        st = s.strip()
        if st.startswith("[") or "]:" in st or " " in st:
            im.set("src", "")
            continue
        im.set("src", _abs_url(st))
    for tag in ("script", "iframe", "source"):
        for el in tree.xpath("//%s[@src]" % tag):
            v = el.get("src")
            if v:
                el.set("src", _abs_url(v.strip()))
    return tree


def find_content(tree):
    for tag, cls in CONTENT_SELECTORS:
        found = tree.xpath("//%s[contains(concat(' ', normalize-space(@class), ' '), ' %s ')]" % (tag, cls))
        if found:
            return found[0]
    arts = tree.xpath("//article")
    if arts:
        return arts[0]
    return None


def extract_meta(tree, url):
    meta = {"date": "", "category": "", "category_url": "", "comment_count": 0,
            "view_count": 0, "tags": []}

    t = tree.xpath("//h1[contains(@class,'article-title')]//text()")
    if not t:
        t = tree.xpath("//h1//text()") or tree.xpath("//title/text()")
    meta["title"] = fix_mojibake("".join(t).strip()) if t else ""

    items = tree.xpath("//div[contains(@class,'article-meta')]//span[contains(@class,'item')]")
    if items:
        for it in items:
            txt = " ".join(x.strip() for x in it.itertext() if x.strip())
            m = re.search(r"(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?)", txt)
            if m and not meta["date"]:
                meta["date"] = m.group(1)
                continue
            c = it.xpath(".//a[contains(@rel,'category')]/text()")
            if c:
                meta["category"] = fix_mojibake(c[0].strip())
                href = it.xpath(".//a[contains(@rel,'category')]/@href")
                if href:
                    meta["category_url"] = href[0]
                continue
            m = re.search(r"评论\s*\((\d+)\)", txt)
            if m:
                meta["comment_count"] = int(m.group(1))
                continue
            m = re.search(r"浏览\s*\((\d+)\)", txt)
            if m:
                meta["view_count"] = int(m.group(1))
                continue
            tg = it.xpath(".//a[contains(@rel,'tag')]/text()")
            if tg:
                meta["tags"] = [x.strip() for x in tg if x.strip()]

    if not meta["date"]:
        tt = tree.xpath("//time/@datetime")
        if tt:
            meta["date"] = tt[0]
    if not meta["date"]:
        body = tree.xpath("//body")
        if body:
            txt = " ".join(body[0].itertext())
            m = re.search(r"(20\d{2}-\d{2}-\d{2})", txt)
            if m:
                meta["date"] = m.group(1)
    return meta


def extract_comments(tree):
    """解析评论树，返回 list。"""
    out = []
    roots = tree.xpath("//div[@id='postcomments']//ol[contains(@class,'commentlist')]")
    if not roots:
        roots = tree.xpath("//ol[contains(@class,'commentlist')]")
    if not roots:
        return out

    def walk(container, parent_id, depth):
        for li in container.xpath("./li[contains(@class,'comment')]"):
            cid = (li.get("id") or "").replace("comment-", "").strip()
            cm = li.xpath(".//div[contains(@class,'comt-main')]")
            if not cm:
                cm = li.xpath(".//div[contains(@class,'comt-main')] | .//div[@id and starts-with(@id,'div-comment')]")
            if not cm:
                continue
            cm = cm[0]
            # 楼层
            fl = li.xpath(".//span[contains(@class,'comt-f')]/text()")
            floor = fl[0].strip() if fl else ""
            # 正文：comt-main 的直接子节点，去掉 comt-meta
            chunks = []
            for ch in cm:
                cls = ch.get("class") or ""
                if "comt-meta" in cls:
                    continue
                if ch.tag == "div" and ("comt-avatar" in cls):
                    continue
                try:
                    chunks.append(LH.tostring(ch, encoding="unicode"))
                except Exception:
                    pass
            chtml = "".join(chunks)
            ctext = html_to_text(chtml)
            # 作者
            au = cm.xpath(".//span[contains(@class,'comt-author')]/text()")
            author = au[0].strip() if au else ""
            # 时间：新版 span[title]；旧版为纯文本 "2012-8-10 11:40"
            tm = cm.xpath(".//span[@title]/@title")
            ctime = tm[0].strip() if tm else ""
            if not ctime:
                mt = cm.xpath(".//div[contains(@class,'comt-meta')]//span[contains(@class,'muted')]//text()")
                mtxt = " ".join(x.strip() for x in mt if x.strip())
                mm = re.search(r"(\d{4}-\d{1,2}-\d{1,2}(?:\s+\d{1,2}:\d{2})?)", mtxt)
                if mm:
                    ctime = mm.group(1)
            # 点赞
            lk = cm.xpath(".//span[contains(@class,'comt-like-num')]/text()")
            likes = int(lk[0].strip()) if lk and lk[0].strip().isdigit() else 0
            # 回复对象
            rp = cm.xpath(".//div[contains(@class,'comt-meta')]//b/text()")
            reply_to = rp[0].strip() if rp else ""
            # 头像
            av = li.xpath(".//div[contains(@class,'comt-avatar')]//img/@src")
            avatar = parse_img_url(av[0]) if av else ""
            # IP / 其他元信息
            extra = cm.xpath(".//div[contains(@class,'comt-meta')]//text()")
            extras = " ".join(x.strip() for x in extra if x.strip())

            node = {
                "id": cid, "floor": floor, "author": fix_mojibake(author), "time": ctime,
                "likes": likes, "reply_to": fix_mojibake(reply_to), "avatar": avatar,
                "content_html": fix_mojibake(chtml), "content": fix_mojibake(ctext),
                "depth": depth, "parent_id": parent_id or "",
                "meta": fix_mojibake(extras),
            }
            out.append(node)

            # 嵌套回复
            for ul in li.xpath("./ul[contains(@class,'children')]"):
                walk(ul, cid, depth + 1)

    walk(roots[0], "", 1)
    return out


def extract_images(content_el, meta):
    """抽取正文图片，保留顺序与属性。"""
    imgs = []
    seen = set()
    for im in content_el.xpath(".//img"):
        src = im.get("src") or im.get("data-src") or im.get("data-original") or ""
        real = parse_img_url(src)
        if not real:
            continue
        if real in seen:
            continue
        seen.add(real)
        item = {
            "url": real,
            "raw_src": src,
            "alt": (im.get("alt") or "").strip(),
            "width": im.get("width") or "",
            "height": im.get("height") or "",
            "data_width": im.get("data-width") or "",
            "data_height": im.get("data-height") or "",
        }
        imgs.append(item)
        # 标记以便后续改写
        im.set("data-fl-origin", real)
    return imgs


def extract_links(content_el):
    links = []
    for a in content_el.xpath(".//a[@href]"):
        href = a.get("href") or ""
        if href.startswith("//"):
            href = "https:" + href
        txt = " ".join(x.strip() for x in a.itertext() if x.strip())
        links.append({"url": href, "text": txt[:120]})
    return links


def parse_subpages(subs, meta):
    """把年度容器里的第 2..N 页子页逐页解析成 pages 片段。

    与站点分页结构一一对应，不合并；返回按页码升序的列表（不含第 1 页）。
    """
    out = []
    for seg in sorted(subs or [], key=lambda s: s.get("n") or 0):
        n = seg.get("n")
        raw = seg.get("html")
        if not n or not raw:
            continue
        try:
            tree = LH.fromstring(raw)
        except Exception:
            continue
        make_links_absolute_safe(tree)
        el = find_content(tree)
        if el is None:
            continue
        el = clean_tree(el)
        ch = fix_mojibake(inner_html(el))
        out.append({
            "n": n,
            "content_html": ch,
            "content_text": html_to_text(ch),
            "images": extract_images(el, meta),
            "links": extract_links(el),
        })
    return out


def main():
    urls = C.read_json(os.path.join(C.SRC, "urls.json"), {}) or {}
    if not urls:
        C.log("[s03] 致命：urls.json 为空")
        return 2
    scope = C.scope_read(C.scope_arg())

    # 原料在年度容器里（_pipeline/material/<年>/<key>.jsonl）：一篇一次读齐 raw + 子页
    if scope:
        # 范围模式不开全树 meta 扫（stats() 要打开七千个容器，只为开一句日志）；
        # 容器总数走 listdir（不读文件内容），仍然能看出「本轮前后库有多大」
        C.log("[s03] 范围模式 %d 篇 | 全树容器 %d 个（未逐篇扫描）"
              % (len(scope), sum(C.Y.counts().values())))
    else:
        st = C.Y.stats()
        C.log("[s03] 年度容器 %d 个（含原始 HTML %d），待解析 URL %d"
              % (st["containers"], st["with_raw"], len(urls)))

    total_target = 0
    no_content = 0
    done = 0
    skipped = 0
    no_raw = 0
    per_year = {}
    t0 = time.time()

    for url, meta_sitemap in urls.items():
        if scope and url not in scope:
            continue
        key = C.url_key(url)
        if C.Y.has_parsed(url=url, key=key):
            skipped += 1
            continue
        parts = C.Y.read_all(url=url, key=key)
        raw = parts["raw"]
        if not raw:
            no_raw += 1
            continue
        total_target += 1
        try:
            tree = LH.fromstring(raw)
        except Exception:
            continue
        make_links_absolute_safe(tree)

        meta = extract_meta(tree, url)
        content_el = find_content(tree)
        if content_el is None:
            no_content += 1
            content_html = ""
            ctext = ""
            imgs = []
            links = []
        else:
            content_el = clean_tree(content_el)
            content_html = fix_mojibake(inner_html(content_el))
            ctext = html_to_text(content_html)
            imgs = extract_images(content_el, meta)
            links = extract_links(content_el)

        comments = extract_comments(tree)

        # 组装分页：第 1 页 + 子页（不合并，保留与站点一致的分页结构）。
        pages = [{"n": 1, "content_html": content_html,
                  "content_text": ctext, "images": imgs, "links": links}]
        pages.extend(parse_subpages(parts["subs"], meta))
        page_count = len(pages)
        # 顶层 images/links 取各页并集（按 url 去重，保留同一 dict 引用，
        # 以便 s04d 设 local 后各页同步生效）；s04i 据此下载子页图。
        all_imgs, seen_u = [], set()
        all_links, seen_l = [], set()
        for pg in pages:
            for im in pg["images"]:
                if im.get("url") and im["url"] not in seen_u:
                    seen_u.add(im["url"]); all_imgs.append(im)
            for lk in pg["links"]:
                if lk.get("url") and lk["url"] not in seen_l:
                    seen_l.add(lk["url"]); all_links.append(lk)
        full_text = ctext if page_count == 1 else "\n\n".join(pg["content_text"] for pg in pages)

        art = {
            "url": url,
            "title": meta.get("title", ""),
            "date": meta.get("date", ""),
            "category": meta.get("category", ""),
            "category_url": meta.get("category_url", ""),
            "tags": meta.get("tags", []),
            "comment_count": meta.get("comment_count", 0),
            "view_count": meta.get("view_count", 0),
            "lastmod": meta_sitemap.get("lastmod", ""),
            "sitemap": meta_sitemap.get("sitemap", ""),
            "content_html": content_html,
            "content_text": full_text,
            "images": all_imgs,
            "links": all_links,
            "comments": comments,
            "comments_parsed": len(comments),
        }
        if page_count > 1:
            art["page_count"] = page_count
            art["pages"] = pages
        slot, _p = C.Y.write_parsed(url, art)
        done += 1
        per_year[slot] = per_year.get(slot, 0) + 1
        if done % 200 == 0:
            el = time.time() - t0
            C.log("[s03] 已解析 %d 篇 (跳过 %d) %.1f 篇/秒" % (done, skipped, done / el if el else 0))

    # 统计：直接取 meta 行汇总（不再全量读回，也不再写 350MB 的 _pipeline/articles.json）
    if scope:
        # 范围模式不重跑全树 stats：本轮结果已经在下面三行里说清了，
        # 全树回归（已解析数/评论数）由 _verify 与 rebuild.py --check 负责，它们仍是全树的
        C.log("[s03] 完成（范围模式 %d 篇）：本次解析 %d，跳过 %d，无原料 %d，无正文 %d 篇"
              % (len(scope), done, skipped, no_raw, no_content))
    else:
        st2 = C.Y.stats()
        total_c = st2["comments"]
        C.log("[s03] 完成：已解析 %d 篇（本次 %d，跳过 %d，无原料 %d），评论 %d 条，无正文 %d 篇"
              % (st2["with_parsed"], done, skipped, no_raw, total_c, no_content))
    if per_year:
        C.log("[s03] 本次落年 %s" % sorted(per_year.items()))
    C.state_append("parse", "__summary__", "DONE",
                   parsed_this_run=done, skipped=skipped, scope=len(scope) if scope else 0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
