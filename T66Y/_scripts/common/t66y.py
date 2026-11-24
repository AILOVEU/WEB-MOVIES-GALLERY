# -*- coding: utf-8 -*-
"""T66Y (草榴) 版面列表页解析 + 标题打标签。

实测形态（2026-11-24，fid=25 國產原創區，老版 Discuz 模板）：

  <table id="ajaxtable">
    <tr class="tr2 tac"> 表头：贊 | 文章 | 作者 | 回復 | 下载 | 最後發表
    <tr align="middle" class="tr3 t_one tac">  <- 論壇公告（notice.php，跳过）
    <tbody id="tbody">
      <tr class="tr3 t_one tac">
        <td>赞数 或 .::</td>
        <td class="tal" id="">
          [↑N 置顶标记]
          <h3><a href="/htm_data/2611/25/7523245.html" target="_blank" id="t7523245">标题</a></h3>
          [<I class="sred" title="有新回帖">new</I>]
          [<span class="mark_red" title="帖子已锁定">锁</span>]
          [<span class="thread_page">[<a>1</a><a>2</a>..<a>881</a>]</span>  多页标记]
        </td>
        <td><a href="/thread0806.php?fid=25&search=629944" class="bl">作者</a>
            <div class="f12"><span class="s3" title="昨天 16:43" data-timestamp="1795423438s">15 小時</span></div></td>
        <td>回复数</td>
        <td>下载数 或 --</td>
        <td><a href="/read.php?tid=7523245&page=e&fpage=2#a" class="f10" data-timestamp="1795433838"> 11-23 19:37 </a><br />最后回复人</td>
      </tr>

关键点：
- 行锚点必须用 `class="tr3 t_one tac"`（含公告行），不能按 `<tr` 切 —— 正文 td 里可能嵌套表格。
- 时间一律取 data-timestamp（epoch 秒），页面上的「昨天 16:43」「15 小時」是相对文本，不能用来排序/筛选。
  「最後發表」的 data-timestamp 没有 s 后缀，作者那栏有，两种都要认。
- 下载数为 `--` 表示没有附件，记 0。
- 标题里的 HTML 实体（&amp; &lt; 等）要反转义；`<b>` 等内联标签要剥掉。
- 列表页不含分类字段（该版无 typeid 筛选），分类靠标题关键词打标签。
"""
import html as _html
import re
import time

BASE = "https://t66y.com"

# 行锚点：公告行带 align="middle"，普通行不带，两者都有 class="tr3 t_one tac"
ROW_RE = re.compile(r'<tr[^>]*class="tr3 t_one tac"[^>]*>', re.I)
TAG_RE = re.compile(r"<[^>]+>")
TS_RE = re.compile(r'data-timestamp="(\d+)s?"')


def unescape(s):
    return _html.unescape(s or "").replace("\r", "").strip()


def strip_tags(s):
    return unescape(TAG_RE.sub("", s or ""))


def _int(s, default=0):
    m = re.search(r"\d[\d,]*", s or "")
    return int(m.group(0).replace(",", "")) if m else default


def fmt_ts(ts):
    """epoch 秒 -> 'YYYY-MM-DD HH:MM'（本地时区；站点时间戳是 UTC+8，本机也在 +8）。"""
    if not ts:
        return ""
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(int(ts)))
    except (ValueError, OSError):
        return ""


# ---------------- 列表页解析 ----------------

def parse_list_page(page_html, page=0):
    """返回该页的记录列表（按页面顺序）。page 是 1-based 页码，写进每条记录便于溯源。"""
    out = []
    anchors = [m.start() for m in ROW_RE.finditer(page_html)]
    for i, s in enumerate(anchors):
        e = anchors[i + 1] if i + 1 < len(anchors) else len(page_html)
        blk = page_html[s:e]

        # 公告行：链接指向 notice.php，不是帖子
        if "notice.php" in blk:
            continue

        # 标题 + tid + 详情页地址
        mt = re.search(
            r'<h3>\s*<a\s+href="([^"]+)"[^>]*?(?:id="t(\d+)")?[^>]*>(.*?)</a>\s*</h3>',
            blk, re.S)
        if not mt:
            continue
        href, tid, raw_title = mt.group(1), mt.group(2), mt.group(3)
        title = strip_tags(raw_title)
        if not title:
            continue
        if not tid:                                   # 兜底：从 href 尾部的数字取
            m = re.search(r"/(\d+)\.html", href)
            tid = m.group(1) if m else ""
        if not tid:
            continue

        # 置顶：h3 之前有 ↑N
        stick = 0
        pre = blk[:mt.start()]
        ms = re.search(r"↑\s*(\d+)", pre)
        if ms:
            stick = int(ms.group(1))

        # 标记：有新回帖 / 锁帖 / 多页
        is_new = bool(re.search(r'title="有新回帖"', blk))
        locked = bool(re.search(r'title="帖子已锁定"', blk))
        mp = re.search(r'<span class="thread_page">(.*?)</span>', blk, re.S)
        pages = 0
        if mp:
            nums = [int(x) for x in re.findall(r">(\d+)</a>", mp.group(1))]
            pages = max(nums) if nums else 0

        # 赞数（第一列）
        mz = re.search(r"<td>\s*(?:<span[^>]*>)?\s*([\d.]+[KkMm]?|\.::)\s*", blk)
        praise = 0
        if mz and mz.group(1) != ".::":
            v = mz.group(1)
            mult = 1
            if v[-1] in "Kk":
                mult, v = 1000, v[:-1]
            elif v[-1] in "Mm":
                mult, v = 1000000, v[:-1]
            try:
                praise = int(float(v) * mult)
            except ValueError:
                praise = 0

        # 作者 + 发帖时间
        ma = re.search(r'<a href="/thread0806\.php\?fid=\d+&(?:amp;)?search=[^"]*"\s+class="bl">(.*?)</a>', blk, re.S)
        author = strip_tags(ma.group(1)) if ma else ""
        # 作者栏的时间戳：第一个 data-timestamp（带 s 后缀）
        mts = TS_RE.search(blk)
        post_ts = int(mts.group(1)) if mts else 0

        # 回复 / 下载：作者栏之后的两个 <td>
        # 注意：下载列可能是空的（<td></td>，没有附件），不能按位置硬取 ——
        # 空列会让索引滑到下一列，把 read.php 链接里的 tid 当成下载数（实测 tid=7472647 的帖）。
        # 所以先把作者栏之后的 td 全部取出，再按内容判定：纯数字才算，空/含标签的跳过。
        tail = blk[ma.end():] if ma else blk
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tail, re.S)
        nums = []
        for td in tds:
            plain = re.sub(r"<[^>]+>", "", td).strip()
            if re.fullmatch(r"[\d,]+", plain):
                nums.append(int(plain.replace(",", "")))
            elif plain in ("", "--"):
                nums.append(0)
            else:
                break          # 遇到非数字列（read.php 链接那列）就停
        replies = nums[0] if len(nums) > 0 else 0
        downloads = nums[1] if len(nums) > 1 else 0

        # 最后发表：read.php 链接的 data-timestamp + <br /> 后的人名
        last_ts, last_by = 0, ""
        ml = re.search(
            r'<a href="/read\.php\?tid=\d+[^"]*"\s+class="f10"[^>]*?data-timestamp="(\d+)"[^>]*>(.*?)</a>'
            r'(?:\s*<br\s*/?>\s*([^<]*))?', blk, re.S)
        if ml:
            last_ts = int(ml.group(1))
            last_by = strip_tags(ml.group(3) or "")

        out.append({
            "tid": tid,
            "title": title,
            "url": href if href.startswith("http") else BASE + href,
            "author": author,
            "post_ts": post_ts,
            "post_date": fmt_ts(post_ts),
            "replies": replies,
            "downloads": downloads,
            "praise": praise,
            "last_ts": last_ts,
            "last_date": fmt_ts(last_ts),
            "last_by": last_by,
            "pages": pages,
            "stick": stick,
            "is_new": is_new,
            "locked": locked,
            "page": page,
        })
    return out


def page_count(page_html):
    """从分页输入框读总页数：value="1/4957"。读不到返回 0。"""
    m = re.search(r'value="\d+/(\d+)"', page_html or "")
    return int(m.group(1)) if m else 0


def is_login_wall(page_html):
    return "您沒有登錄或者您沒有權限" in (page_html or "")


# ---------------- 标题打标签 ----------------

def load_tags(path):
    """读 tags.json -> [{key,label,color,words:[...]}]。文件坏了返回空表，不阻断构建。"""
    import io
    import json
    import os
    if not os.path.exists(path):
        return []
    try:
        d = json.load(io.open(path, encoding="utf-8"))
    except Exception:
        return []
    return d.get("tags") or []


def tag_record(rec, tags):
    """按关键词给一条记录打标签，返回 tag key 列表（按 tags.json 顺序，去重）。

    匹配范围是标题（大小写不敏感）。一个标签命中任一关键词即算命中。
    结构化标签（置顶/锁帖/多页/新回复）由 build.py 另行附加，不在这里。
    """
    t = (rec.get("title") or "").lower()
    if not t:
        return []
    hits = []
    for tg in tags:
        for w in tg.get("words") or []:
            if w and w.lower() in t:
                hits.append(tg["key"])
                break
    return hits
