# -*- coding: utf-8 -*-
"""组装离线画廊数据（输出全部落在 data/）：
  data/data.js             window.FORUM_INDEX = [ {tid,title,category,author,date,views,replies,cover,post_count,...} ]（列表）；并写 window.SHARD_N（详情分片数，前端据此定位分片）
  data/posts/shard_<i>.js  window.THREADS["<tid>"] = {tid,title,...,article,comments}（详情按 tid%SHARD_N 分片，点某帖才注入它所在那一片；图片路径本地化）

网盘标记：每条帖子用 _scripts/common/pannet.py 扫正文(1L)和回复楼层里的网盘分享链接（百度/阿里/夸克/迅雷/
115/蓝奏/微云/天翼/移动/123/UC/谷歌盘 + 新域名兜底），结果写成两层同名字段 `pan`（正文命中）与
`pan_floor`（只在楼层命中），data/data.js 与 data/posts/{tid}.js 都带 —— 列表页据此高亮+chip，详情页据此显示
"本帖含网盘链接"。识别在 build 阶段完成，不需要重新抓取；改规则只要重跑 build.py。

图片本地化：content_html 内 <img> 的远端 src 经 _pipeline/imgmap.json 映射改写为本地相对路径
（assets/images/tid/name、assets/avatars/name）；映射不到（表情/失败）则保留原绝对 URL 以便联网回退。
图片来源开关：同时把 imgmap 倒排成 data/imgsrc.js（本地路径 -> 原始 https URL），供 index.html 的
"本地资源 / 原始资源"切换用。生成的是交付层的小 JS，不动任何 data.js / posts/*.js 的既有内容。
帖内链接本地化：指向本站帖的 href 改成 `index.html#tid=N`（已入库时，并去掉 target=_blank 走本页路由），
未入库的帖与其它站内链接绝对化成主站 https://www.javbus.com；链接文字里的镜像域名（buscdn.casa 等）
统一换成主站——镜像点进去要重新登录，体验差。
去掉站点专用 on* 事件与 zoom class；移除 <script>。
"""
import html as _html
import io
import json
import os
import re
import sys
from urllib.parse import urljoin

_SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # _scripts
sys.path.insert(0, os.path.join(_SCRIPTS, "common"))                      # 公共库：paths/discuz/pannet
import discuz as D
import pannet
import paths as P

ROOT = P.ROOT
WORK_DIR = P.WORK               # 过程区：index.json / imgmap.json / parsed/ 都在这
PARSED_DIR = P.PARSED
DATA_DIR = P.DATA               # 交付层数据统一入口
OUT_INDEX = P.DATA_INDEX
OUT_POSTS = P.DATA_POSTS        # 详情层：分片快照 shard_<i>.js，一帖一行 window.THREADS["<tid>"]={...}
SHARD_N = P.SHARD_N             # 分片数（单一来源在 paths.py，data.js 里写成 window.SHARD_N 供前端读）
OUT_ORIG_MAP = os.path.join(DATA_DIR, "imgsrc.js")  # 图片来源开关：本地路径 -> 原始 URL
IMGMAP_PATH = P.IMGMAP
INDEX_JSON = P.INDEX_JSON
BASE = D.BASE

SCRIPT_RE = re.compile(r"<script\b.*?</script>", re.I | re.S)
IMG_RE = re.compile(r"<img\b[^>]*>", re.I)
SRC_RE = re.compile(r'(?:zoomfile|file|src)\s*=\s*"([^"]+)"', re.I)
ONATTR_RE = re.compile(r"\s+on\w+\s*=\s*(\"[^\"]*\"|'[^']*')", re.I)


def load_map():
    return json.load(io.open(IMGMAP_PATH, encoding="utf-8")) if os.path.exists(IMGMAP_PATH) else {}


def write_orig_map(imgmap):
    """倒排永久台账 -> data/imgsrc.js，供前端"原始资源"模式把本地图片换回原始 https 地址。

    台账是 {原始URL: assets/images/tid/name}，这里翻成 {本地路径: 原始URL}。只收 http(s) 开头的
    key 和 assets/images/ 开头的 value（头像/表情/失败图本来就不在台账里）。同一本地路径若被多个
    URL 复用，取第一条即可。每轮 build 都重生成，因此增量抓的新图自动进入开关，无需额外手工步骤。
    """
    rev = {}
    for url, local in imgmap.items():
        if url.startswith("http") and local.startswith(P.IMG_REL + "/"):
            rev.setdefault(local, url)
    io.open(OUT_ORIG_MAP, "w", encoding="utf-8").write(
        "/* 自动生成：本地图片路径 -> 原始图片 URL，勿手改 */\nwindow.IMG_ORIG_MAP = "
        + json.dumps(rev, ensure_ascii=False, separators=(",", ":")) + ";\n")
    return len(rev)


SHARD_HEAD = "/* 自动生成，勿手改：详情分片（一帖一行 window.THREADS[\"<tid>\"]={...}） */\nwindow.THREADS = window.THREADS || {};\n"


def write_shards(buckets):
    """把按 tid%SHARD_N 归好桶的帖子行写成固定 SHARD_N 个 shard_<i>.js（空片也写，保持片数恒定）。

    buckets: {分片序号: [ "window.THREADS[\"<tid>\"] = {...};", ... ]}。每片是一份合法 ES5 JS，
    逐行赋值进全局 window.THREADS，前端注入一片后即拥有该片全部帖，重复注入也幂等（同 tid 覆盖同值）。
    返回 (写入的帖子总数, 片数)。
    """
    total = 0
    for i in range(SHARD_N):
        lines = buckets.get(i, [])
        total += len(lines)
        body = SHARD_HEAD + "\n".join(lines) + ("\n" if lines else "")
        io.open(P.shard_path(i), "w", encoding="utf-8").write(body)
    return total, SHARD_N


def clean_stale_posts():
    """删掉 posts/ 下不属于当前分片体系的遗留 .js（主要是"一帖一文件"旧格式的 <tid>.js）。

    分片改造后旧碎文件若不清掉，会和 shard_*.js 混在一起（validate/guard 会把它们也当详情扫），
    且数量又变回几百个，违背整合初衷。只删不匹配 shard_\\d+.js 的 .js，绝不碰分片本身或其它文件。
    """
    keep = {"shard_%d.js" % i for i in range(SHARD_N)}
    removed = 0
    for fn in os.listdir(OUT_POSTS) if os.path.isdir(OUT_POSTS) else []:
        if fn.endswith(".js") and fn not in keep:
            try:
                os.remove(os.path.join(OUT_POSTS, fn))
                removed += 1
            except OSError:
                pass
    return removed


def localize_html(html, imgmap):
    if not html:
        return ""
    html = SCRIPT_RE.sub("", html)
    # [img]URL[/img] BBCode 未渲染时，转为 <img>
    html = re.sub(r"\[img\]\s*(https?://[^\s\[]+?)\s*(?:\[/img\])?",
                  r'<img src="\1" loading="lazy" class="gimg" alt="">', html, flags=re.I)

    def repl(m):
        tag = m.group(0)
        ms = SRC_RE.search(tag)
        src = ms.group(1) if ms else ""
        absu = urljoin(BASE, src)
        local = imgmap.get(absu) or imgmap.get(src)
        if local:
            return '<img src="%s" loading="lazy" class="gimg" alt="" onerror="this.style.display=\'none\'">' % local
        # 映射不到（失效/表情）：保留可用绝对地址供在线回退，加载失败则隐藏，清掉站点专用属性
        cleaned = ONATTR_RE.sub("", re.sub(r'class="zoom"', "", tag))
        cleaned = re.sub(r"\s+alt=", " onerror=\"this.style.display='none'\" alt=", cleaned, count=1)
        return cleaned

    html = IMG_RE.sub(repl, html)
    # 兜底：正文中以 <a href>/文本 出现的已下载图 URL -> 本地路径
    for u in set(re.findall(r'https?://[^\s"\'<>\]]+?\.(?:jpe?g|png|gif|webp|bmp)', html, re.I)):
        loc = imgmap.get(u)
        if loc:
            html = html.replace(u, loc)
    return html


def localize_avatar(url, imgmap):
    if not url:
        return ""
    return imgmap.get(url) or imgmap.get(urljoin(BASE, url)) or ""


# ---- 帖内链接改站内（2026-11-03）----
# javbus 是多镜像站：正文里 href 是站内相对路径（/forum/forum.php?mod=viewthread&tid=N），
# 链接文字才是镜像绝对 URL（buscdn.casa / busdmm.bond / javsee.bond / fanbus.bond /
# javsee.cyou / seedmm.lol，2026-11-03 从真实数据扫出来的）。点这些镜像要么要重新登录、要么落在外站。
# 规则：1) 指向本站帖且该帖已入库 -> href 改成 index.html#tid=N，并去掉 target="_blank"（走本页路由）；
#       2) 该帖未入库、或其它站内相对链接（/forum/home.php、/MDBK-264 作品页…）
#          -> 绝对化成主站 https://www.javbus.com（本地点开不再 404）；
#       3) 链接文字里的镜像域名也统一写成主站。外链（网盘/ed2k/图片站…）一律不动。
MAIN_HOST = "https://www.javbus.com"
_MIRROR_ALT = (r'(?:javbus\.com|buscdn\.casa|busdmm\.bond|javsee\.bond|fanbus\.bond'
               r'|javsee\.cyou|seedmm\.lol)')
SITE_HOST_RE = re.compile(r'^https?://(?:www\.)?' + _MIRROR_ALT + r'(?=[/"\'<\s]|$)', re.I)
# 链接文字里的镜像域名要用不带锚点的版本（2026-11-03 自测抓到的：带 ^ 时 sub 只命中串首）
SITE_HOST_TEXT_RE = re.compile(r'https?://(?:www\.)?' + _MIRROR_ALT + r'(?=[/"\'<\s)]|$)', re.I)
VT_TID_RE = re.compile(r'mod=viewthread&(?:amp;)?tid=(\d+)', re.I)
A_START_RE = re.compile(r'<a\b[^>]*?>', re.I)
HREF_RE = re.compile(r'href="([^"]*)"')
# 自定义协议链接（ed2k/magnet/thunder…）：本地是 file:// 打开，带 target="_blank" 时 Chrome 会把
# 未知协议的顶层导航拦成 about:blank#blocked；同标签导航才会正常唤起迅雷/qBittorrent 处理程序，
# 所以这类 href 一律摘掉 target="_blank"（页面不卸载，取消对话框即回到原帖）。
CUSTOM_SCHEME_RE = re.compile(r'^\s*(?:ed2k|magnet|thunder|flashget|ftp|svn|quark|amule|bittorrent):', re.I)


def _site_path(href):
    """是本站链接就返回去掉协议/域名后的路径，否则 None（外链不动）。"""
    u = _html.unescape(href or "").strip()
    if u.startswith('//'):
        u = 'https:' + u
    if u.startswith('/'):
        return u
    if SITE_HOST_RE.match(u):
        m = re.match(r'https?://[^/]+(/.*)?$', u)
        return (m.group(1) or '/') if m else None
    return None


def localize_links(html, have):
    """把帖内链接改写到项目内；have 是已入库的 tid 集合。"""
    if not html:
        return html or ""

    def repl(m):
        tag = m.group(0)
        hm = HREF_RE.search(tag)
        if not hm:
            return tag
        path = _site_path(hm.group(1))
        if not path:
            if CUSTOM_SCHEME_RE.match(_html.unescape(hm.group(1))):
                return re.sub(r'\s+target="_blank"', '', tag)
            return tag
        t = VT_TID_RE.search(_html.unescape(path))
        if t and t.group(1) in have:
            tag = tag.replace(hm.group(0), 'href="index.html#tid=%s"' % t.group(1))
            return re.sub(r'\s+target="_blank"', '', tag)
        return tag.replace(hm.group(0), 'href="%s%s"' % (MAIN_HOST, _html.escape(path, quote=False)))

    out = A_START_RE.sub(repl, html)
    return SITE_HOST_TEXT_RE.sub(MAIN_HOST, out)     # 链接文字里的镜像域名换主站


def local_images(urls, imgmap):
    out = []
    for u in urls:
        r = imgmap.get(u)
        if r:
            out.append(r)
    return out


def html_to_text(html, limit=150):
    """去标签/去图/去脚本 -> 摘要文本。"""
    t = re.sub(r"<[^>]+>", " ", html or "")
    t = _html.unescape(t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:limit]


def build():
    imgmap = load_map()
    metas = json.load(io.open(INDEX_JSON, encoding="utf-8"))
    have = {str(m["tid"]) for m in metas}      # 已入库的 tid，决定帖内链接能不能站内跳转
    P.ensure(DATA_DIR)
    P.ensure(OUT_POSTS)
    index_out = []
    built = 0
    pan_n = 0
    pan_stat = {}
    buckets = {}                 # 详情分片桶：{分片序号: ["window.THREADS[\"<tid>\"]={...};", ...]}
    for m in metas:
        tid = m["tid"]
        pf = os.path.join(PARSED_DIR, "%s.json" % tid)
        cover = ""
        post_count = 0
        preview = ""
        last_reply = None
        pages = 0
        pan, pan_floor = [], []
        if os.path.exists(pf):
            d = json.load(io.open(pf, encoding="utf-8"))
            post_count = d["post_count"]
            locals_ = local_images(d["images"], imgmap)
            cover = locals_[0] if locals_ else ""
            # 网盘链接打标：正文(1L) 命中记 pan，只在回复楼层命中记 pan_floor
            pan, pan_floor = pannet.scan_thread(d)
            if pan or pan_floor:
                pan_n += 1
                for _h in pan:
                    pan_stat["正文 " + _h["label"]] = pan_stat.get("正文 " + _h["label"], 0) + 1
                for _h in pan_floor:
                    pan_stat["楼层 " + _h["label"]] = pan_stat.get("楼层 " + _h["label"], 0) + 1
            article = d["article"]
            thread = {
                "tid": tid, "title": m["title"], "category": m["category"],
                "typeid": m.get("typeid", ""), "author": article["author"] or m["author"],
                "uid": article.get("uid") or m.get("uid"), "date": article["date"] or m["date"],
                "views": m["views"], "replies": m["replies"],
                "pages_fetched": d["pages_fetched"], "post_count": post_count,
                "cover": cover, "all_images": locals_,
                "pan": pan, "pan_floor": pan_floor,
                "article": {
                    "author": article["author"], "uid": article.get("uid", ""),
                    "avatar": "",  # 头像不抓取：置空，前端用缺省占位图
                    "date": article["date"],
                    "html": localize_links(localize_html(article["content_html"], imgmap), have),
                    "images": local_images(article["images"], imgmap),
                },
                "comments": [{
                    "floor": c["floor"], "author": c["author"], "uid": c.get("uid", ""),
                    "avatar": "",  # 头像不抓取
                    "date": c["date"], "html": localize_links(localize_html(c["content_html"], imgmap), have),
                    "images": local_images(c.get("images", []), imgmap),
                } for c in d["comments"]],
            }
            si = P.shard_of(tid)
            buckets.setdefault(si, []).append(
                'window.THREADS["%s"] = %s;' % (tid, json.dumps(thread, ensure_ascii=False)))
            built += 1
            # 摘要也先过一道链接改写：否则 preview 里会残留 buscdn.casa 这类镜像域名（2026-11-03 实测发现）
            preview = html_to_text(localize_links(article["content_html"], have))
            pages = d["pages_fetched"]
            if d["comments"]:
                lc = d["comments"][-1]
                last_reply = {"author": lc["author"], "date": lc["date"]}
        index_out.append({
            "tid": tid, "title": m["title"], "category": m["category"],
            "typeid": m.get("typeid", ""), "author": m["author"], "date": m["date"],
            "views": m["views"], "replies": m["replies"], "post_count": post_count,
            "cover": cover, "avatar": "",  # 头像不抓取
            "preview": preview, "pages": pages, "last_reply": last_reply,
            "pan": pan, "pan_floor": pan_floor,
            "has_detail": os.path.exists(pf),
        })
    n_shards = write_shards(buckets)[1]
    stale = clean_stale_posts()
    io.open(OUT_INDEX, "w", encoding="utf-8").write(
        "/* 老司機論壇 fid=2 离线累积库 · 自动生成，勿手改 */\n"
        "window.SHARD_N = %d;\nwindow.FORUM_INDEX = " % SHARD_N
        + json.dumps(index_out, ensure_ascii=False) + ";\n")
    n_orig = write_orig_map(imgmap)
    print("[build] data/data.js: %d threads; data/posts/shard_*.js: %d 片（详情 %d 帖, 清理旧碎文件 %d, SHARD_N=%d）; imgmap=%d; data/imgsrc.js=%d"
          % (len(index_out), n_shards, built, stale, SHARD_N, len(imgmap), n_orig))
    print("[build] 含网盘链接: %d 帖  %s" % (pan_n, " ".join(
        "%s=%d" % (k, v) for k, v in sorted(pan_stat.items(), key=lambda x: -x[1]))))
    # 统计图片引用覆盖率
    refs = set()
    for it in index_out:
        if it["cover"]:
            refs.add(it["cover"])
    return len(refs)


if __name__ == "__main__":
    build()
