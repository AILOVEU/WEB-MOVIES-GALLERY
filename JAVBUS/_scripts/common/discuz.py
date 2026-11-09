# -*- coding: utf-8 -*-
"""Discuz! (javbus forum fid=2) 解析核心：列表页 + 帖子详情页（楼主 + 回复）。

实测形态（2026-10，javbus 定制模板）：
- 列表行 <tbody id="normalthread_TID|stickthread_TID">：
    标题 <a href="...viewthread&tid=TID..." class="s">TITLE</a>
    分类 <em>[<a href="...filter=typeid&typeid=7"><font ...>交流</font></a>]</em>
    楼主头像 <div class="post_avatar"><a ...uid=U><img src="AVATAR"></a>
    作者 <span class="author"> <a href="...uid=U" c="1">NAME</a></span>（前有空格）
    浏览 <span class="views">N</span>  回复 <span class="reply">N</span>
    发帖日 <span class="dateline"><span title="YYYY-MM-DD">
- 帖子页页头 nthread_info：楼主 <div class="authi mb5"><a ...uid=U class="au...">NAME</a>發表於<span class="mr10">DATETIME</span>
- 楼主正文块 <div id="post_PID" class="nthread_postbox nthread_firstpostbox"> -> <td class="t_f" id="postmessage_PID">
- 回复块 <div id="post_PID" class="nthread_postbox ">：
    作者卡 id="userinfoPID" ... uid=数字 class="xi2">NAME
    时间 <em id="authorpostonPID">...<span title="DATETIME">
    头像 id="favatarPID" ... class="avtm"><img src="AVATAR">
    正文 <td class="t_f" id="postmessage_PID">
    点评 <div id="comment_PID" class="cm">（可无）
- 内容原图 <img class="zoom" src="https://forum.javcdn.cc/i.imgur.com/XXX">（src 即原图，可直接下）。
- 点评区 Discuz 二级功能、量少且解析脆弱，主「评论」即回复楼层，故不单独提取点评。
- 分页 viewthread&tid=X&page=N，每页 15 楼；超末页会钳制返回末页（PID 全重复 -> 上层据此终止翻页）。
"""
import html as _html
import re
from urllib.parse import urljoin

BASE = "https://www.javbus.com/forum/"

EXCLUDE_IMG_MARKS = (
    "/ads/", "smiley", "avatar", "/uc/", "uc.javbus",
    "template/javbus", "static/image", "/common/", "noavatar",
    "star_level", "rank", "checkcode", "seccode", "thread-prev", "thread-next",
    "hot_", "forumlink", "userinfo.gif",
)


def unescape(s):
    return _html.unescape(s or "").replace("\r", "").strip()


def _text(fragment):
    t = re.sub(r"<[^>]+>", "", fragment or "")
    return unescape(t)


def _abs(u):
    return urljoin(BASE, u.strip()) if u else ""


# ---------------- 列表页 ----------------

def parse_list_page(page_html):
    out = []
    matches = list(re.finditer(r'id="(?:normal|stick)thread_(\d+)"', page_html))
    for i, m in enumerate(matches):
        s = m.start()
        e = matches[i + 1].start() if i + 1 < len(matches) else len(page_html)
        blk = page_html[s:e]
        tid = m.group(1)
        mt = re.search(r'viewthread&[^"]*tid=%s[^"]*"[^>]*class="s"[^>]*>(.*?)</a>' % tid, blk, re.S) \
            or re.search(r'class="s"[^>]*>(.*?)</a>', blk, re.S)
        title = _text(mt.group(1)) if mt else ""
        mc = re.search(r'filter=typeid[&;amp=]*typeid=(\d+)"[^>]*><font[^>]*>(.*?)</font>', blk, re.S)
        category = _text(mc.group(2)) if mc else ""
        typeid = mc.group(1) if mc else ""
        ma = re.search(r'<span class="author">\s*<a href="home\.php\?mod=space&amp;uid=(\d+)"[^>]*>(.*?)</a>', blk)
        author = _text(ma.group(2)) if ma else ""
        uid = ma.group(1) if ma else ""
        mav = re.search(r'<div class="post_avatar">.*?<img src="([^"]+)"', blk, re.S)
        avatar = _abs(mav.group(1)) if mav else ""
        md = re.search(r'<span class="dateline"><span title="([^"]+)"', blk)
        date = md.group(1) if md else ""
        mv = re.search(r'<span class="views">(\d+)</span>', blk)
        mr = re.search(r'<span class="reply">(\d+)</span>', blk)
        if not title:
            continue
        out.append({"tid": tid, "title": title, "category": category, "typeid": typeid,
                    "author": author, "uid": uid, "avatar": avatar, "date": date,
                    "views": int(mv.group(1)) if mv else 0,
                    "replies": int(mr.group(1)) if mr else 0})
    return out


# ---------------- t_f 内容平衡提取（抗正文内嵌套表格）----------------

def _extract_td(html, start_pos):
    depth, i, n = 1, start_pos, len(html)
    seg_re = re.compile(r"<(/?)td[\s>]", re.I)
    last = n
    while i < n:
        m = seg_re.search(html, i)
        if not m:
            break
        depth += -1 if m.group(1) == "/" else 1
        i = m.end()
        if depth == 0:
            last = m.start()
            break
    return html[start_pos:last].rstrip()


# ---------------- 内容原图提取 ----------------

def extract_content_images(content_html):
    urls, seen = [], set()
    for tag in re.findall(r"<img\b[^>]*>", content_html or "", re.I):
        m = re.search(r'(?:zoomfile|file|src)\s*=\s*"([^"]+)"', tag, re.I)
        if not m:
            continue
        raw = m.group(1)
        if raw.startswith("data:"):
            continue
        u = _abs(raw)
        low = u.lower()
        if any(x in low for x in EXCLUDE_IMG_MARKS):
            continue
        if not re.search(r"\.(jpe?g|png|gif|webp|bmp)(\?|$)", low) and "javcdn" not in low and "attachment" not in low:
            continue
        if u not in seen:
            seen.add(u); urls.append(u)
    for m in re.finditer(r'href="([^"]*mod=attachment[&;](?:amp;)?aid=[^"]*)"', content_html or "", re.I):
        u = _abs(unescape(m.group(1)))
        if u not in seen:
            seen.add(u); urls.append(u)
    # 兜底：图片以 <a href> 链接 / [img]BBCode / 裸文本 URL 形式出现（未渲染为 <img>）
    for m in re.finditer(r'https?://[^\s"\'<>\]]+?\.(?:jpe?g|png|gif|webp|bmp)', content_html or "", re.I):
        u = _html.unescape(m.group(0))
        low = u.lower()
        if any(x in low for x in EXCLUDE_IMG_MARKS):
            continue
        if u not in seen:
            seen.add(u); urls.append(u)
    return urls


def _op_header_meta(page_html):
    m = re.search(r'class="authi mb5">\s*<a href="home\.php\?mod=space&amp;uid=(\d+)"[^>]*class="au[^>]*">(.*?)</a>.*?<span class="mr10">(.*?)</span>', page_html, re.S)
    if not m:
        return {"author": "", "uid": "", "date": "", "avatar": ""}
    return {"author": _text(m.group(2)), "uid": m.group(1), "date": unescape(m.group(3)), "avatar": ""}


# ---------------- 帖内引用链接 / 帖子页元信息 ----------------

# 站内引用有两种写法：相对 href="/forum/forum.php?mod=viewthread&tid=N" 和绝对
# href="https://www.javbus.com/forum/forum.php?mod=viewthread&tid=N"，两种都认。
#
# 2026-11-06 起**只看域名**，站外一律不计（旧实现不看，出过事故）：正文/楼层里除了本站
# 链接，还有镜像站（seedmm.lol / javsee.bond / busdmm.bond / fanbus.bond / javsee.cyou /
# buscdn.casa）和**别家论坛**（实测 www.52pojie.cn）的 mod=viewthread&tid= 链接。
# 别家论坛那个 tid 被当本站引用抓回来，javbus 返回"主题不存在"提示页（无 postmessage
# 锚点 -> verdict=end），crawl 仍把它写进 crawl_done，guard 于是报"抓了却没入库"对称差
# （本轮 tid=2104395，来自 178316 的 52pojie 外链）。
# 收窄到本站域名不丢内容：实测全库 21 个站外 tid 里，镜像那 20 个本来就已在库里，
# 唯一不在库的就是 52pojie 的垃圾。
# 注：build.py 的 localize_links 另有口径——镜像域名在**展示层**按本站改写（见那里
# _MIRROR_ALT 的注释）；抓取层不再顺着镜像域名去补抓，两边口径不同是有意的。
LOCAL_REF_HOSTS = ("javbus.com",)
TID_RE = re.compile(r'mod=viewthread&(?:amp;)?tid=(\d+)', re.I)
_HOST_RE = re.compile(r'(?:https?:)?//([A-Za-z0-9._-]+)', re.I)


def _is_local_ref(host):
    """host 为 None = 相对链接（本站）；有域名则必须命中本站域名。"""
    if not host:
        return True
    h = host.lower().split(":")[0].strip(".")
    return any(h == d or h.endswith("." + d) for d in LOCAL_REF_HOSTS)


def _nearest_host(page_html, pos):
    """tid 之前、同一个标签/属性值内**最后**出现的域名；没有域名返回 None（= 相对链接）。

    先用引号/尖括号把回看范围收窄到当前这一段（前一个标签里的域名不会串到本段），
    再取段内最后一个 `//host` 就近归属。
    为什么不用一个"可选域名前缀 + 惰性任意字符"的大正则：那是灾难性回溯，
    2026-11-06 实测扫全库 parsed 直接卡死（>2 分钟没跑完一层），本实现是线性的。
    """
    lo = max(page_html.rfind('"', 0, pos), page_html.rfind("'", 0, pos),
             page_html.rfind('<', 0, pos), page_html.rfind('>', 0, pos))
    host = None
    for hm in _HOST_RE.finditer(page_html[lo + 1:pos]):
        host = hm.group(1)
    return host


def extract_thread_refs(html):
    """从一段 HTML（帖子页或正文片段）里抽出被引用的**本站** tid 集合（正文+回复区都算）。

    站外域名（别家论坛、镜像站）的 mod=viewthread&tid= 一律不计，理由见上方注释块。
    """
    s = html or ""
    return set(m.group(1) for m in TID_RE.finditer(s)
               if _is_local_ref(_nearest_host(s, m.start())))


def parse_thread_meta(page_html):
    """从帖子页 HTML 里取列表元数据：给“帖内引用抓回来、但不在列表页”的帖用。

    锚点全部来自 2026-11-03 对真实页面的实测：标题 <span id="thread_subject">、
    分类 <a href="...filter=typeid&typeid=8"><font ...>日本</font></a>、
    浏览 <span class="xg1">查看:</span> <span class="xi1">107033</span>、
    楼主/时间走 _op_header_meta。拿不到的字段留空/0，不编造。
    """
    mtid = re.search(r'viewthread&(?:amp;)?tid=(\d+)', page_html)
    mt = re.search(r'<span id="thread_subject">(.*?)</span>', page_html, re.S)
    title = _text(mt.group(1)) if mt else ""
    if not title:
        m2 = re.search(r'<title>(.*?)</title>', page_html, re.S)
        title = re.sub(r'\s*[-–].*$', '', unescape(m2.group(1))) if m2 else ""
    mc = re.search(r'filter=typeid&amp;typeid=(\d+)"[^>]*>\[?<font[^>]*>(.*?)</font>', page_html, re.S)
    mv = re.search(r'查看:</span>\s*<span class="xi1">([\d,]+)</span>', page_html)
    op = _op_header_meta(page_html)
    return {
        "tid": mtid.group(1) if mtid else "",
        "title": title,
        "category": _text(mc.group(2)) if mc else "",
        "typeid": mc.group(1) if mc else "",
        "author": op["author"], "uid": op["uid"], "date": op["date"],
        "views": int(mv.group(1).replace(",", "")) if mv else 0,
    }


def parse_thread_page(page_html):
    """返回 {tid, posts:[{pid,is_first,author,uid,avatar,date,content_html,images,inline_comments}]}，按页面顺序。"""
    op = _op_header_meta(page_html)
    mtid = re.search(r'viewthread&(?:amp;)?tid=(\d+)', page_html)
    tid = mtid.group(1) if mtid else ""
    posts = []
    anchors = list(re.finditer(r'id="post_(\d+)"', page_html))
    for i, m in enumerate(anchors):
        s = m.start()
        e = anchors[i + 1].start() if i + 1 < len(anchors) else len(page_html)
        blk = page_html[s:e]
        pid = m.group(1)
        is_first = "nthread_firstpostbox" in blk[:400]
        mt = re.search(r'<td class="t_f" id="postmessage_%s"[^>]*>' % pid, blk, re.S)
        content = _extract_td(blk, mt.end()) if mt else ""
        if not content:
            continue
        images = extract_content_images(content)
        if is_first:
            author, uid, date, avatar = op["author"], op["uid"], op["date"], op["avatar"]
        else:
            mu = re.search(r'id="userinfo%s".*?uid=(\d+)"[^>]*class="xi2"[^>]*>(.*?)</a>' % pid, blk, re.S) \
                or re.search(r'<div class="authi"><a href="home\.php\?mod=space&amp;uid=(\d+)"[^>]*class="xw1"[^>]*>(.*?)</a>', blk, re.S)
            uid = mu.group(1) if mu else ""
            author = _text(mu.group(2)) if mu else ""
            md = re.search(r'id="authorposton%s".*?<span title="([^"]+)"' % pid, blk, re.S)
            date = md.group(1) if md else ""
            mav = re.search(r'id="favatar%s".*?class="avtm"[^>]*>\s*<img src="([^"]+)"' % pid, blk, re.S)
            avatar = _abs(mav.group(1)) if mav else ""
        posts.append({"pid": pid, "is_first": is_first, "author": author, "uid": uid,
                      "avatar": avatar, "date": date, "content_html": content.strip(),
                      "images": images})
    return {"tid": tid, "posts": posts}
