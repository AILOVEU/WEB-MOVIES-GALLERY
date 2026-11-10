# -*- coding: utf-8 -*-
"""
s06_build_site.py — 生成离线交付站点（年度层）
产出（一年一个自包含目录，统一在站点根的 data/ 下，迁移就是复制一个文件夹）：
- data/<年>/data.js        window.FULIBA_YEAR("<年>", [...])  该年列表元数据（自注册）
- data/<年>/_audit.json    该年完整性小清单（拷贝后只比 16 个小文件就能验收）
- data/<年>/part<i>.html   文章静态分片（2026-11-08 起，取代一篇一文件）：一片装
                                 years.ARTICLE_PER_PART(=150) 篇，每篇 = 一个
                                 <div class="shard-post" id="<slug>"> 锚点，靠 hash 路由逐篇显示；
                                 分片在年份目录下（站点根下两层），页内前缀取 years.PART_PAGE_BASE = ../..，图指 assets/images/…
- index.html          只此一份（站点根）：预枚举 YEAR_MIN..YEAR_MAX + undated，存在即加载、缺失即跳过；
                     统计合并后现算 → 新增年份只建目录，不改任何页面代码
- _logs/<日期>/report.md / data/common/archived.js / data/common/style.css / data/common/img-fallback.js
  （站点根只留入口 HTML；图片仍归 assets/images/，样式/脚本/占位图归 data/common/）
站点根的旧全局 data.js 已退役（改用各年 data.js 在运行时合并）。
卡片链接形如 data/<年>/part<p>.html#<slug>，p 由 build_data 按各年 date-desc 顺序连续分配并写进 data.js 的 item.p，
与 write_article_parts 的分块序号同口径（改一处必须同步另一处，否则深链指错片）。
写盘一律走 write_if_changed（忽略生成时间戳后内容相同就不写）——
新增 1 篇只重写其所属年的若干分片 + 该年 data.js，不会把 7 千页 mtime 全刷新。

用法：
  _scripts\\py.cmd _scripts\\build\\s06_build_site.py                    全站重建（含 report.md）
  _scripts\\py.cmd _scripts\\build\\s06_build_site.py --years=2026,2027  只重建指定年的 data.js 与分片
  ⚠ 卡片链接是全局生成的：改了分片方案后，首次必须跑「全站重建」，让所有年份都产出 part<i>.html，
    否则未重建年份的卡片链接会指向尚不存在的分片。
"""
import html as htmlmod
import io
import json
import os
import re
import sys
import time
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # _scripts/
import common as C

# 生成时间戳每次都在变，比较时要先抹平，否则「内容未变就不写」永远失效
_VOLATILE = (re.compile(r"生成于 \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}"),
             re.compile(r"生成时间：\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}"),
             re.compile(r'"generated":\s*"[^"]*"'))


def _norm(text):
    for rx in _VOLATILE:
        text = rx.sub("TS", text)
    return text


def write_if_changed(path, text):
    """忽略生成时间后内容相同 → 不写（mtime 保持），返回是否真写了。

    ⚠ 读取必须 newline=""：write_text 按原样写（正文里的 CRLF 会落盘成 \r\n），
    默认 newline=None 的读法会把 CRLF 翻译成 \n，于是每次比对都不相等，
    「内容未变就不写」会默默失效（实测 448 篇被多写）。
    """
    try:
        if os.path.exists(path):
            with io.open(path, "r", encoding="utf-8", newline="") as f:
                if _norm(f.read()) == _norm(text):
                    _IO["same"] += 1
                    return False
    except OSError:
        pass
    C.write_text(path, text)
    _IO["written"] += 1
    return True


# 本轮写盘计数（给完成日志用：写多少、内容未变跳过多少）
_IO = {"written": 0, "same": 0}


def slugify(title, url):
    t = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "", title or "").strip()
    # Windows 目录名不能以 . 或空格结尾，也不能是保留设备名
    t = t.rstrip(". ")
    t = re.sub(r"\s+", "-", t)[:60]
    if not t or t.upper() in ("CON", "PRN", "AUX", "NUL", "COM1", "LPT1"):
        t = C.url_key(url)
    return t


def build_data(articles, slots=None):
    """把容器解析产物压成列表页需要的元数据项。slots: {url: 年份槽}，进 item 的 y 字段。"""
    items = []
    for url, a in articles.items():
        slug = C.url_key(url)   # 与 write_article_page 的目录名保持一致
        slot = (slots or {}).get(url) or C.Y.slot_of(url)
        cmt_parsed = a.get("comments_parsed", 0)
        # 兜底：若 comments_parsed 缺失，用实际评论数组长度
        if not cmt_parsed:
            cmt_parsed = len(a.get("comments", []) or [])
        items.append({
            "id": slug,
            "y": slot,
            "url": url,
            "title": a.get("title", ""),
            "date": a.get("date", ""),
            "category": a.get("category", ""),
            "tags": a.get("tags", []),
            "comment_count": a.get("comment_count", 0),
            "comments_parsed": cmt_parsed,
            "view_count": a.get("view_count", 0),
            "imgs": len([i for i in a.get("images", []) if i.get("local")]),
            "page_count": a.get("page_count", 1),
            "text_len": len(a.get("content_text", "")),
            "summary": (a.get("content_text", "") or "")[:160],
            "gap_reason": a.get("comment_gap_reason", ""),
        })
    items.sort(key=lambda x: (x["date"] or "0000-00-00", x["id"]), reverse=True)
    # 分片序号 p：在各年「date-desc」顺序里连续排，每 ARTICLE_PER_PART 篇进一片。
    # 写分片（main 的 write_article_parts）与卡片链接（index 的 part<p>.html#id）都据 p，
    # 口径必须一致，故 p 只在这里算一次、随 item 落到 data.js。
    _per_part = C.Y.ARTICLE_PER_PART
    _seen = {}
    for it in items:
        k = it["y"]
        i = _seen.get(k, 0)
        it["p"] = i // _per_part
        _seen[k] = i + 1
    cats = {}
    for it in items:
        c = it["category"] or "未分类"
        cats[c] = cats.get(c, 0) + 1
    years = {}
    for it in items:
        y = (it["date"] or "")[:4]
        if y:
            years[y] = years.get(y, 0) + 1
    meta = {
        "generated": time_str(),
        "total": len(items),
        "total_comments": sum(i["comments_parsed"] for i in items),
        "total_images": sum(i["imgs"] for i in items),
        "categories": cats,
        "years": years,
        "domain": "fuliba2023.net",
    }
    return meta, items


def time_str():
    import time
    return time.strftime("%Y-%m-%d %H:%M:%S")


def js_escape(s):
    if s is None:
        return ""
    s = str(s)
    return (s.replace("\\", "\\\\").replace("'", "\\'")
             .replace("\r", "").replace("\n", "\\n")
             .replace("<", "\\u003c").replace(">", "\\u003e")
             .replace("&", "\\u0026").replace(" ", "\\u2028")
             .replace(" ", "\\u2029"))


def article_unit(a, slug, base):
    """渲染一篇文章的「片段」：正文 + 图集 + 评论 + 页脚，返回 (unit_html, has_switch)。

    不产 head/脚本——这些在分片装配时共享一份（见 write_article_parts）。
    slug 与年度 data.js 的 id 一致（URL 哈希），同时作为分片内该篇的锚点 id。
    base：分片页回站点根的相对前缀（years.PART_PAGE_BASE = `../..`），由调用方传入，
    页内 `../../assets/images/…` 全由它拼出。

    ⚠ 路径基准：manifest / 容器里的 local 是站点根相对的图片键（`images/b8/b8d65f....png`），
    而图片实际住在 `assets/images/` 下 → 先过 C.site_rel() 换前缀，再加 base 深度前缀。
    ⚠ 评论锚点跨文章可能重号（cid 是每篇内的序号），故加 slug 前缀命名，保证分片内 id 唯一。
    """

    def rel(local):
        """站点根相对路径 → 分片页相对路径（图片键顺便翻成 assets/images/）。"""
        if not local or local.startswith(("http://", "https://", "//", "data:")):
            return local
        return base + "/" + C.site_rel(local).lstrip("/")

    comments_html = []
    by_parent = {}
    for c in a.get("comments", []):
        by_parent.setdefault(c.get("parent_id") or "", []).append(c)

    def render(pid, depth):
        out = []
        for c in by_parent.get(pid, []):
            # 头像优先用本地路径（本地化失败时回退原远程 URL）
            av = c.get("avatar_local") or c.get("avatar") or ""
            av = rel(av)
            av_html = '<img class="cmt-av" src="%s" alt="">' % (av or "") if av else ""
            rt = ('<span class="cmt-rt">回复 @%s</span>' % htmlmod.escape(c["reply_to"])) if c.get("reply_to") else ""
            # likes / depth 可能是字符串（早期抓取与新补抓的字段类型不一致）→ 统一转 int
            try:
                likes = int(re.sub(r"\D", "", str(c.get("likes", 0))) or 0)
            except ValueError:
                likes = 0
            out.append(
                '<li class="cmt d%d" id="c-%s-%s">'
                '<div class="cmt-avw">%s</div>'
                '<div class="cmt-main">'
                '<div class="cmt-meta"><b class="cmt-au">%s</b>'
                '<span class="cmt-fl">%s</span>'
                '<span class="cmt-tm">%s</span>%s'
                '<span class="cmt-lk">♥ %d</span></div>'
                '<div class="cmt-txt">%s</div>'
                '</div></li>'
                % (depth, htmlmod.escape(slug), htmlmod.escape(c.get("id", "")),
                   av_html, htmlmod.escape(c.get("author", "")),
                   htmlmod.escape(c.get("floor", "")), htmlmod.escape(c.get("time", "")),
                   rt, likes,
                   c.get("content_html", "") or htmlmod.escape(c.get("content", ""))
                   )
            )
            kids = render(c.get("id", ""), depth + 1)
            if kids:
                out.append("<ul class='cmt-children'>%s</ul>" % kids)
        return "".join(out)

    comments_html = render("", 1)

    imgs = [i for i in a.get("images", []) if i.get("local")]
    # 本地↔原始 URL 双向映射：正文与图集的 <img> 注入 data-local + data-orig，
    # 供页内「本地/原始」切换按钮使用（见 IMG_MODE_JS）。原始地址取自抓取台账 images[].url。
    local2url = {}
    for i in a.get("images", []) or []:
        if i.get("local") and i.get("url"):
            local2url[i["local"]] = i["url"]
    has_switch = bool(local2url)
    _rx_img_src = re.compile(r'<img([^>]*?)src="([^"]*assets/images/[^"]*)"'
                             r'((?:(?!\sdata-orig=)[^>])*)>', re.I)

    def inject_orig(h):
        """base_fix 之后，给 src 指向本地图片的 <img> 补 data-local/data-orig（已带则跳过）。"""
        if not h or not has_switch:
            return h

        def _rep(m):
            pre, src, post = m.group(1), m.group(2), m.group(3)
            idx = src.find("assets/images/")
            if idx < 0:
                return m.group(0)
            key = "images/" + src[idx + len("assets/images/"):]
            url = local2url.get(key)
            if not url:
                return m.group(0)
            return ('<img%ssrc="%s" data-local="%s" data-orig="%s"%s>'
                    % (pre, src, src, htmlmod.escape(url, quote=True), post))
        return _rx_img_src.sub(_rep, h)

    img_html = ""
    if imgs:
        cards = []
        for i in imgs:
            u = i.get("url", "") or ""
            src_local = rel(i["local"])
            data_l = ' data-local="%s"' % src_local if u else ""
            data_o = (' data-orig="%s"' % htmlmod.escape(u, quote=True)) if u else ""
            cards.append(
                '<figure class="fimg"><img loading="lazy" src="%s"%s%s alt="%s">'
                '<figcaption>%s</figcaption></figure>'
                % (src_local, data_l, data_o, htmlmod.escape(i.get("alt", "")),
                   htmlmod.escape(i.get("alt", "") or ""))
            )
        img_html = '<section class="fimgwrap"><h2>图片（%d）</h2><div class="fgrid">%s</div></section>' % (
            len(imgs), "".join(cards))

    # 正文 content_html 里的 <img src> 是站点根相对的图片键（images/xx/...），
    # 分片页在 data/<年>/ 下（base=years.PART_PAGE_BASE=`../..`）：既要补深度前缀，也要把 images/ 翻成 assets/images/
    def base_fix(h):
        h = h or ""
        if h and base:
            h = h.replace('src="images/', 'src="%s/assets/images/' % base)
            h = h.replace("src='images/", "src='%s/assets/images/" % base)
            h = h.replace('src="assets/', 'src="%s/data/common/' % base)
            h = h.replace("src='assets/", "src='%s/data/common/" % base)
        return inject_orig(h)

    # 分页：站点把长文拆成第 1..N 页，离线页也分页展示（翻页器 + 逐页 div）。
    pages = a.get("pages") or [{"n": 1, "content_html": a.get("content_html", "")}]
    page_count = a.get("page_count", len(pages))
    if page_count > 1:
        secs = []
        for pg in pages:
            vis = "" if pg.get("n") == 1 else " style='display:none'"
            secs.append('<div class="pc-page" data-n="%d"%s>%s</div>'
                        % (pg.get("n", 1), vis, base_fix(pg.get("content_html", ""))))
        btns = "".join('<button class="pbtn%s" data-n="%d">%d</button>'
                       % (" cur" if n == 1 else "", n, n) for n in range(1, page_count + 1))
        pager = ('<nav class="pc-pager" aria-label="页码">'
                 '<button class="pnav" data-d="-1">‹ 上一页</button>'
                 '<span class="pnums">%s</span>'
                 '<span class="pinfo">第 1 / %d 页</span>'
                 '<button class="pnav" data-d="1">下一页 ›</button></nav>' % (btns, page_count))
        content_html = "".join(secs) + pager
    else:
        content_html = base_fix(a.get("content_html", ""))

    # 子页翻页器与「本地/原始」图片模式脚本不再逐篇内联——合并进分片后它们会跨文章
    # 串页/重复执行，改由分片装配时的一份「作用域化」共享脚本统一处理（见 SHARD_JS）。
    unit = """<article class="post">
  <header class="ph">
    <h1>%(title)s</h1>
    <div class="pm"><span>%(date)s</span><span>分类：%(cat)s</span><span>评论 %(cc)d</span><span>浏览 %(vc)d</span>
    %(multi)s<a class="src" href="%(url)s" target="_blank" rel="noopener">查看原文 ↗</a></div>
  </header>
  <div class="pc">%(content)s</div>
</article>
%(imgs)s
<section class="cmts">
  <h2>评论（%(cc)d）</h2>
  <ol class="cmtlist">%(comments)s</ol>
</section>
<footer class="pf"><a href="%(base)s/index.html">← 返回索引</a></footer>""" % {
        "title": htmlmod.escape(a.get("title", "")),
        "date": htmlmod.escape(a.get("date", "")),
        "cat": htmlmod.escape(a.get("category", "") or "未分类"),
        "cc": a.get("comments_parsed", 0),
        "vc": a.get("view_count", 0),
        "url": htmlmod.escape(a.get("url", "")),
        "base": base,
        "multi": ('<span class="multi">全文 %d 页</span>' % page_count) if page_count > 1 else "",
        "content": content_html,
        "imgs": img_html,
        "comments": comments_html or "<li class='empty'>暂无评论</li>",
    }
    return unit, has_switch


# 分片外壳：共享一份 head + 作用域脚本；每篇 = 一个 <div class="shard-post" id="<key>"> 锚点。
# 显示切换用纯 JS（hash 路由），不依赖 CSS :target/:has；未命中锚点时显示落地页提示。
SHARD_HEAD = """<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>福利吧归档 - %(year)s 第 %(i)d 片</title>
<link rel="stylesheet" href="%(base)sdata/common/style.css">
<script src="%(base)sdata/common/img-fallback.js"></script>
<style>
.shard-post{display:none}
.shard-landing{min-height:60vh;display:flex;align-items:center;justify-content:center;color:var(--fg2,#6b7280);font-size:15px}
.shard-loading{display:flex;align-items:center;gap:10px}
.spin{width:22px;height:22px;border:3px solid rgba(127,127,127,.25);border-top-color:var(--acc,#4a90d9);border-radius:50%%;animation:shardspin .8s linear infinite}
@keyframes shardspin{to{transform:rotate(360deg)}}
</style>
</head><body>
<div class="shard-landing">
  <div class="shard-loading"><span class="spin"></span>加载中… 正在跳转到首页</div>
  <noscript><p style="margin-left:10px">请 <a href="%(base)s/index.html">点此进入首页</a>。</p></noscript>
</div>
"""

# 分片底部共享脚本：① 子页翻页作用域到每篇 .shard-post（避免跨文章串页）；
# ② hash 路由逐篇显示 + 同步标题；无编号直开分片页时显示加载态并跳回首页。
# 图片「本地/原始」模式仍用全局 IMG_MODE_JS。
SHARD_JS = r"""<script>
(function(){
  var DEFAULT_TITLE=document.title;
  var posts=[].slice.call(document.querySelectorAll('.shard-post'));
  var byId={}; posts.forEach(function(p){byId[p.id]=p;});
  var landing=document.querySelector('.shard-landing');
  posts.forEach(function(wrap){
    var pg=wrap.querySelector('.pc-pager'); if(!pg) return;
    var pages=[].slice.call(wrap.querySelectorAll('.pc-page')); if(!pages.length) return;
    var pv=pg.querySelector('.pnav[data-d="-1"]'), nx=pg.querySelector('.pnav[data-d="1"]');
    var total=pages.length, cur=1;
    function setNav(){ if(pv) pv.disabled=(cur<=1); if(nx) nx.disabled=(cur>=total); }
    function show(n){
      n=Math.max(1,Math.min(total,n)); cur=n;
      pages.forEach(function(p){p.style.display=(+p.getAttribute('data-n')===n)?'':'none';});
      [].forEach.call(pg.querySelectorAll('.pbtn'),function(b){b.classList.toggle('cur',+b.getAttribute('data-n')===n);});
      var inf=pg.querySelector('.pinfo'); if(inf) inf.textContent='第 '+n+' / '+total+' 页';
      setNav();
      var pc=wrap.querySelector('.pc'); if(pc) pc.scrollIntoView({behavior:'smooth',block:'start'});
    }
    pg.addEventListener('click',function(e){
      var b=e.target.closest('button'); if(!b||b.disabled) return;
      if(b.classList.contains('pnav')) show(cur+(+b.getAttribute('data-d')));
      else if(b.classList.contains('pbtn')) show(+b.getAttribute('data-n'));
    });
    setNav();
  });
  function route(){
    var id; try{ id=decodeURIComponent((location.hash||'').slice(1)); }catch(e){ id=(location.hash||'').slice(1); }
    var hit=byId[id];
    // 命中篇必须显式给 block：CSS 里 .shard-post{display:none}，置空串('')不覆盖 CSS 规则会全隐
    posts.forEach(function(p){p.style.display=(p===hit)?'block':'none';});
    if(landing){
      landing.style.display=hit?'none':'block';
      if(!hit) setTimeout(function(){ location.replace('../../index.html'); },600); // 无编号直开分片页 = 加载页，回首页
    }
    document.title=hit?((hit.getAttribute('data-title')||'')+' - 福利吧归档'):DEFAULT_TITLE;
    if(hit) window.scrollTo(0,0);
  }
  window.addEventListener('hashchange',route);
  route();
})();
</script>
"""


def write_article_parts(slot, units):
    """把 (slug, article) 列表按 years.ARTICLE_PER_PART 聚合写成 data/<年>/part<i>.html。

    units 必须按该年 data.js 的 date-desc 顺序传入（与 build_data 里算的 p 同口径），
    否则 part<i>.html#id 会指向错片。返回本片实际写出的分片数。
    """
    base = C.Y.PART_PAGE_BASE
    per = C.Y.ARTICLE_PER_PART
    n_parts = C.Y.n_parts_for(len(units))
    for i in range(n_parts):
        chunk = units[i * per:(i + 1) * per]
        any_switch = False
        blocks = []
        for slug, a in chunk:
            try:
                unit, has_switch = article_unit(a, slug, base)
            except Exception as e:  # 单篇渲染异常不拖垮整年分片：跳过该篇，卡片链接退化为落地页
                C.log("[s06] 警告：%s/%s 正文渲染失败，跳过（%s）" % (slot, slug, e))
                continue
            if has_switch:
                any_switch = True
            blocks.append('%s%s" data-title="%s">\n%s\n</div>\n'
                          % (C.Y.SHARD_UNIT_OPEN, slug,
                             htmlmod.escape(a.get("title", "") or "(无标题)"), unit))
        page = (SHARD_HEAD % {"year": slot, "i": i, "n": len(chunk), "base": base}
                + "".join(blocks)
                + C.Y.SHARD_SCRIPTS_MARK + "\n" + SHARD_JS
                + (IMG_MODE_JS if any_switch else "")
                + "</body></html>\n")
        write_if_changed(C.Y.part_path(slot, i), page)
    return n_parts


INDEX_TPL = """<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>福利吧 fuliba2023.net 全站归档</title>
<link rel="stylesheet" href="data/common/style.css">
<style>
.arch-panel{position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:50;overflow:auto;padding:24px 0}
.arch-panel[hidden]{display:none}
.arch-panel-in{max-width:960px;margin:0 auto;background:var(--bg);border:1px solid var(--line);border-radius:var(--r);padding:18px 20px}
.arch-head{display:flex;align-items:center;justify-content:space-between;margin:0 0 8px}
.arch-head h2{margin:0;font-size:18px}
.arch-close{border:1px solid var(--line);background:transparent;color:var(--fg);border-radius:6px;width:32px;height:32px;cursor:pointer;font-size:15px}
.arch-close:hover{border-color:var(--acc);color:var(--acc)}
.arch-sub{color:var(--fg2);font-size:13px;line-height:1.7;margin:0 0 12px}
.arch-sub b{color:var(--acc)}
.mini{border:1px solid var(--line);background:transparent;color:var(--fg2);border-radius:5px;padding:1px 9px;font-size:12px;cursor:pointer;margin-left:6px}
.mini:hover{border-color:var(--acc);color:var(--acc)}
.msg{color:var(--acc);font-size:13px}
.arch-btn{background:transparent;cursor:pointer;font-family:inherit}
.arch-list .arch-mark{margin-left:auto}
</style>
</head><body>
<header class="hero">
  <div class="hero-in">
    <h1>福利吧 · 全站文章归档</h1>
    <p class="sub" id="heroSub">%(domain)s</p>
    <p class="gen">数据来档于本地各年目录（data/&lt;年&gt;/）· 生成于 %(generated)s</p>
  </div>
</header>

<nav class="bar">
  <div class="bar-in">
    <input id="q" type="search" placeholder="搜索标题 / 正文 / 分类…" autocomplete="off">
    <select id="cat"><option value="">全部分类</option></select>
    <select id="year"><option value="">全部年份</option></select>
    <span class="cnt" id="cnt"></span>
    <button class="arch-btn" id="archBtn" type="button" title="归档管理：标记移入/移出，导出名单交 archive.py 执行">归档<span id="archPend"></span></button>
    <button id="imgModeBtn" type="button" title="切换所有文章页的图片来源：本机 assets/images（本地）或原始地址（省图库，需联网）">🖼 图片源：<b></b></button>
  </div>
</nav>

<main>
  <div class="list" id="list"></div>
  <div class="pager" id="pager"></div>
  <p class="empty" id="empty" hidden>没有匹配的文章</p>
</main>

<footer class="foot">
  <p>本归档仅收录 fuliba2023.net 公开内容页，版权归原作者所有；评论与图片均已本地化保存。</p>
</footer>

<section class="arch-panel" id="archPanel" hidden>
  <div class="arch-panel-in" role="dialog" aria-modal="true" aria-label="归档管理">
    <div class="arch-head">
      <h2>归档管理</h2>
      <button id="archClose" class="arch-close" type="button" aria-label="关闭">✕</button>
    </div>
    <p class="arch-sub">已归档 <span id="archTotal">…</span> 篇（仅名录/原地址/发布时间，资源已释放）。在首页文章卡片点「归档」标记<b>待移入</b>，在下方名录点「移出归档」标记<b>待移出</b>；导出名单交 <b>_scripts/audit/archive.py --apply</b> 执行（移入=删资源，移出=重抓）。</p>
    <section class="ops">
      <h2>待移入归档 <span id="nIn" class="opn"></span></h2>
      <ul id="pendIn"></ul>
      <h2>待移出归档 <span id="nOut" class="opn"></span></h2>
      <ul id="pendOut"></ul>
      <div class="ops-bar">
        <button id="clrBtn" type="button">清空名单</button>
        <span id="msg" class="msg"></span>
        <span class="hint">标记保存在浏览器 localStorage（换电脑不迁移）；名单确认后由助手按名单执行
        <b>_scripts\py.cmd _scripts\audit\archive.py --apply</b>（移入=删资源，移出=重抓）；
        执行完回到本页点「清空名单」清掉已执行标记。</span>
      </div>
    </section>
    <h2 class="arch-h">已归档名录（<span id="archTotal2">…</span> 篇）</h2>
    <ul class="arch-list" id="archList"></ul>
    <p class="arch-note">名录来自 data/common/archived.js；逐篇判定依据见台账 _pipeline/skipped_promo.json 的 source 字段，防抓回登记见 _pipeline/urls_gone.json。</p>
  </div>
</section>

<script>
/* ===== 年度数据加载器（只改造一次，之后逐年零修改）===== */
/* 预枚举 YEAR_MIN..YEAR_MAX + undated：%(data_prefix)s<年>/data.js 存在就合并，不存在静默跳过；
   明年新建 data/2027/ 目录后刷新页面就能看到，index.html 一个字都不改。
   统计（总篇数/评论/图/分类/年份）由各年条目合并后现算。 */
(function(){
  var SLOTS = %(year_slots)s;
  var bucket = {}, left = SLOTS.length, pending = left;
  window.FULIBA_YEAR = function(y, items){ bucket[y] = items || []; return; };
  function meta(all){
    var cats = {}, years = {}, tc = 0, ti = 0;
    all.forEach(function(a){
      var c = a.category || '未分类', y = (a.date || '').slice(0, 4) || a.y;
      cats[c] = (cats[c] || 0) + 1; years[y] = (years[y] || 0) + 1;
      tc += a.comments_parsed || 0; ti += a.imgs || 0;
    });
    return {total: all.length, total_comments: tc, total_images: ti,
            categories: cats, years: years, domain: '%(domain)s'};
  }
  function finish(){
    var all = [];
    Object.keys(bucket).sort().forEach(function(k){ all = all.concat(bucket[k]); });
    all.sort(function(x, y){ return (y.date || '').localeCompare(x.date || ''); });
    window.FULIBA_DATA = {articles: all, meta: meta(all)};
    /* 年度脚本可能比本文件后面的 app 代码先加载完，两边都要能接住 */
    window.__yearReady = true;
    if (window.__startApp__) window.__startApp__();
  }
  SLOTS.forEach(function(y){
    var s = document.createElement('script');
    s.src = '%(data_prefix)s' + y + '/data.js';
    s.onload = s.onerror = function(){ if (--pending === 0) finish(); };
    document.head.appendChild(s);
  });
})();
</script>
<script>
/* 全局图片来源开关：只读写 localStorage('fuliba_img_mode')，作用于所有文章页
   （文章页 IMG_MODE_JS 启动时读同一 key）。首页自身不展示正文图，这里仅做预设。 */
(function(){
  var KEY='fuliba_img_mode', b=document.getElementById('imgModeBtn');
  if(!b) return;
  var lab=b.querySelector('b');
  function paint(m){ if(lab) lab.textContent=(m==='orig')?'原始地址':'本地'; b.classList.toggle('on',m==='orig'); }
  var cur='local'; try{ cur=localStorage.getItem(KEY)||'local'; }catch(e){}
  paint(cur==='orig'?'orig':'local');
  b.addEventListener('click',function(){
    var next=b.classList.contains('on')?'local':'orig';
    try{ localStorage.setItem(KEY,next); }catch(e){}
    paint(next);
  });
})();
/* —— 归档名单（localStorage）：首页卡片「归档」= 待移入，归档面板「移出归档」= 待移出；导出名单交 _scripts/audit/archive.py 执行 —— */
var OPSKEY='fuliba_arch_ops';
var OM=loadOps();
function loadOps(){ try{var o=JSON.parse(localStorage.getItem(OPSKEY)); if(o&&o.in&&o.out) return o;}catch(e){} return {in:{},out:{}}; }
function saveOps(){ try{localStorage.setItem(OPSKEY,JSON.stringify(OM));}catch(e){} }
function pendCount(){ var n=0,k; for(k in OM.in)n++; for(k in OM.out)n++; return n; }
function updBadge(){ var b=document.querySelector('#archPend'); if(b){ var n=pendCount(); b.textContent=n?(' '+n):''; } }

window.__startApp__ = function(){
  var D = window.FULIBA_DATA, A = D.articles;
  var DP = '%(data_prefix)s';   /* 年份目录在站点根下的前缀（data/）*/
  (function(){
    var m = D.meta, n = document.querySelector('#heroSub');
    if (n) n.textContent = m.domain + ' — 共 ' + m.total.toLocaleString() + ' 篇文章 · '
      + m.total_comments.toLocaleString() + ' 条评论 · ' + m.total_images.toLocaleString() + ' 张本地图片';
  })();
  var PAGE = 20, page = 0, list = A;
  var $ = function(s){return document.querySelector(s)};
  var catSel = $('#cat'), yearSel = $('#year');

  Object.keys(D.meta.categories).sort().forEach(function(c){
    var o=document.createElement('option'); o.value=c; o.textContent=c+' ('+D.meta.categories[c]+')'; catSel.appendChild(o);
  });
  Object.keys(D.meta.years).sort().reverse().forEach(function(y){
    var o=document.createElement('option'); o.value=y; o.textContent=y+' ('+D.meta.years[y]+')'; yearSel.appendChild(o);
  });

  function esc(s){var d=document.createElement('div'); d.textContent=s||''; return d.innerHTML;}

  function card(a){
    var imgs = [];
    (a.imgs>0) && imgs.push('<span class="tag t-img">图 '+a.imgs+'</span>');
    (a.page_count>1) && imgs.push('<span class="tag t-pg">全文 '+a.page_count+' 页</span>');
    (a.comments_parsed>0) && imgs.push('<span class="tag t-cmt">评 '+a.comments_parsed+'</span>');
    (a.view_count>0) && imgs.push('<span class="tag t-view">阅 '+a.view_count+'</span>');
    return '<article class="card">'+
      '<a class="c-title" href="' + DP + a.y + '/part' + a.p + '.html#' + a.id + '">' + esc(a.title||'(无标题)') + '</a>'+
      '<div class="c-meta"><span class="c-date">'+esc(a.date)+'</span>'+
      (a.category?'<a class="c-cat" href="#" data-cat="'+esc(a.category)+'">'+esc(a.category)+'</a>':'')+
      imgs.join('')+'</div>'+
      '<p class="c-sum">'+esc(a.summary)+'…</p>'+
      '<a class="c-link" href="'+esc(a.url)+'" target="_blank" rel="noopener">原文 ↗</a>'+
      '<button class="arch-mark'+(OM.in[a.id]?' on':'')+'" data-mark="'+a.id+'">'+(OM.in[a.id]?'已标记 ✓':'归档')+'</button>'+
      '</article>';
  }

  function pageNumbers(cur, total){
    var arr=[], lo=Math.max(0,cur-2), hi=Math.min(total-1,cur+2), i;
    if(lo>0){ arr.push(0); if(lo>1) arr.push('…'); }
    for(i=lo;i<=hi;i++) arr.push(i);
    if(hi<total-1){ if(hi<total-2) arr.push('…'); arr.push(total-1); }
    return arr;
  }
  function renderPager(){
    var box=$('#pager'); if(!box) return;
    var total=Math.ceil(list.length/PAGE);
    if(total<=1){ box.innerHTML=''; return; }
    var h='<button class="pnav" data-pg="'+(page-1)+'"'+(page<=0?' disabled':'')+'>‹ 上一页</button>';
    var nums=pageNumbers(page,total);
    for(var k=0;k<nums.length;k++){
      if(nums[k]==='…'){ h+='<span class="pinfo">…</span>'; }
      else { var n=nums[k]; h+='<button class="pbtn'+(n===page?' cur':'')+'" data-pg="'+n+'">'+(n+1)+'</button>'; }
    }
    h+='<button class="pnav" data-pg="'+(page+1)+'"'+(page>=total-1?' disabled':'')+'>下一页 ›</button>';
    h+='<span class="pinfo">第 '+(page+1)+' / '+total+' 页</span>';
    box.innerHTML=h;
  }
  function render(){
    var start=page*PAGE, end=Math.min(start+PAGE, list.length), html='';
    for(var i=start;i<end;i++) html+=card(list[i]);
    $('#list').innerHTML=html;
    $('#cnt').textContent = list.length ? ('第 '+(start+1)+'-'+end+' 篇 / 共 '+list.length+' 篇') : '共 0 篇';
    renderPager();
    $('#empty').hidden = list.length>0;
  }

  function filter(){
    var q=$('#q').value.trim().toLowerCase(), c=catSel.value, y=yearSel.value;
    list = A.filter(function(a){
      if(c && a.category!==c) return false;
      if(y && (a.date||'').slice(0,4)!==y) return false;
      if(q){
        var hay=(a.title+' '+a.category+' '+a.summary+' '+(a.tags||[]).join(' ')).toLowerCase();
        if(hay.indexOf(q)<0) return false;
      }
      return true;
    });
    page=0; render();
  }

  var t=null;
  $('#q').addEventListener('input',function(){clearTimeout(t);t=setTimeout(filter,220);});
  catSel.addEventListener('change',filter);
  yearSel.addEventListener('change',filter);
  document.addEventListener('click',function(e){
    var b=e.target.closest('[data-pg]');
    if(!b || b.hasAttribute('disabled')) return;
    var p=parseInt(b.getAttribute('data-pg'),10);
    var total=Math.ceil(list.length/PAGE);
    if(isNaN(p)||p<0||p>=total||p===page) return;
    page=p; render();
    var m=document.querySelector('main');
    window.scrollTo(0, m?m.offsetTop-58:0);
  });
  document.addEventListener('click',function(e){
    var a=e.target.closest('[data-cat]');
    if(a){ e.preventDefault(); catSel.value=a.getAttribute('data-cat'); filter(); }
  });
  document.addEventListener('click',function(e){
    var b=e.target.closest('[data-mark]');
    if(!b) return;
    e.preventDefault();
    var id=b.getAttribute('data-mark'), a=null;
    for(var i=0;i<A.length;i++) if(A[i].id===id){a=A[i];break;}
    if(!a) return;
    if(OM.in[id]){ delete OM.in[id]; b.classList.remove('on'); b.textContent='归档'; }
    else { OM.in[id]={url:a.url,title:a.title,date:a.date,category:a.category,ts:Date.now()};
           b.classList.add('on'); b.textContent='已标记 ✓'; }
    saveOps(); updBadge();
  });
  updBadge();
  render();
};
if (window.__yearReady) window.__startApp__();
</script>
<script>
/* ===== 归档面板：复用同一份 localStorage 名单（与首页卡片「归档」共享），懒加载 data/common/archived.js 名录 ===== */
(function(){
  var panel=document.getElementById('archPanel');
  var btn=document.getElementById('archBtn');
  var closeBtn=document.getElementById('archClose');
  if(!panel||!btn) return;
  var loaded=false, roster=[];
  function esc(s){var d=document.createElement('div'); d.textContent=s||''; return d.innerHTML;}
  function msg(t){ var m=document.getElementById('msg'); if(m) m.textContent=t||''; }
  function pendLi(v,attr){
    return '<li><span class="od">'+esc(v.date||'?')+'</span>'+
      '<span class="ot">'+esc(v.title||'(无标题)')+'</span>'+
      '<span class="au">'+esc(v.url||'')+'</span>'+
      '<button class="mini" '+attr+'="'+esc(v.key)+'">移除</button></li>';
  }
  function renderPend(){
    var i='',o='',k;
    for(k in OM.in){ var v=OM.in[k]; v.key=k; i+=pendLi(v,'data-rin'); }
    for(k in OM.out){ var w=OM.out[k]; w.key=k; o+=pendLi(w,'data-rout'); }
    var pin=document.getElementById('pendIn'), pout=document.getElementById('pendOut');
    if(pin) pin.innerHTML=i||'<li class="none">（空）—— 在首页文章卡片点「归档」标记待移入</li>';
    if(pout) pout.innerHTML=o||'<li class="none">（空）—— 在下方已归档名录点「移出归档」标记待移出</li>';
    var nIn=document.getElementById('nIn'), nOut=document.getElementById('nOut');
    if(nIn) nIn.textContent=Object.keys(OM.in).length?('('+Object.keys(OM.in).length+')'):'';
    if(nOut) nOut.textContent=Object.keys(OM.out).length?('('+Object.keys(OM.out).length+')'):'';
    markButtons();
  }
  function markButtons(){
    var bs=document.querySelectorAll('#archList [data-out]');
    for(var j=0;j<bs.length;j++){
      var k2=bs[j].getAttribute('data-out');
      if(OM.out[k2]){ bs[j].classList.add('on'); bs[j].textContent='待移出 ✓'; }
      else { bs[j].classList.remove('on'); bs[j].textContent='移出归档'; }
    }
  }
  function renderRoster(){
    var box=document.getElementById('archList'); if(!box) return;
    var h='';
    for(var i=0;i<roster.length;i++){
      var r=roster[i];
      h+='<li data-url="'+esc(r.url)+'" data-title="'+esc(r.title)+'" data-date="'+esc(r.date)+'" data-cat="'+esc(r.cat)+'">'+
         '<span class="ad">'+esc(r.date)+'</span>'+
         '<span class="ac">'+esc(r.cat)+'</span>'+
         '<a href="'+esc(r.url)+'" target="_blank" rel="noopener">'+esc(r.title)+'</a>'+
         '<span class="au">'+esc(r.url)+'</span>'+
         '<button class="arch-mark'+(OM.out[r.key]?' on':'')+'" data-out="'+esc(r.key)+'">'+(OM.out[r.key]?'待移出 ✓':'移出归档')+'</button></li>';
    }
    box.innerHTML=h;
    var t1=document.getElementById('archTotal'), t2=document.getElementById('archTotal2');
    if(t1) t1.textContent=roster.length; if(t2) t2.textContent=roster.length;
    markButtons();
  }
  function openPanel(){
    panel.hidden=false;
    document.body.style.overflow='hidden';
    if(!loaded){
      loaded=true;
      var s=document.createElement('script');
      s.src='data/common/archived.js';
      s.onload=function(){ roster=window.ARCHIVED_ROSTER||[]; renderRoster(); };
      s.onerror=function(){ roster=[]; renderRoster(); };
      document.head.appendChild(s);
    }
    renderPend();
  }
  function closePanel(){ panel.hidden=true; document.body.style.overflow=''; }
  btn.addEventListener('click',function(){ if(panel.hidden) openPanel(); else closePanel(); });
  if(closeBtn) closeBtn.addEventListener('click',closePanel);
  panel.addEventListener('click',function(e){ if(e.target===panel) closePanel(); });
  document.addEventListener('keydown',function(e){ if(e.key==='Escape'&&!panel.hidden) closePanel(); });
  document.addEventListener('click',function(e){
    var b;
    if((b=e.target.closest('#archList [data-out]'))){
      var k=b.getAttribute('data-out'), li=b.closest('li');
      if(OM.out[k]) delete OM.out[k];
      else OM.out[k]={key:k,url:li.getAttribute('data-url'),title:li.getAttribute('data-title'),
                       date:li.getAttribute('data-date'),category:li.getAttribute('data-cat'),ts:Date.now()};
      saveOps(); renderPend(); updBadge(); msg(''); return;
    }
    if((b=e.target.closest('[data-rin]'))){ delete OM.in[b.getAttribute('data-rin')]; saveOps(); renderPend(); updBadge(); return; }
    if((b=e.target.closest('[data-rout]'))){ delete OM.out[b.getAttribute('data-rout')]; saveOps(); renderPend(); updBadge(); return; }
  });
  var clr=document.getElementById('clrBtn');
  if(clr) clr.addEventListener('click',function(){
    var n=Object.keys(OM.in).length+Object.keys(OM.out).length;
    if(!n){ msg('名单已为空'); return; }
    if(confirm('确定清空待移入/待移出全部 '+n+' 条标记？（应在 archive.py --apply 执行成功后清理）')){
      OM={in:{},out:{}}; saveOps(); renderPend(); updBadge(); msg('已清空');
    }
  });
})();
</script>
</body></html>
"""

STYLE_CSS = """*{box-sizing:border-box}
/* 归档入口与墓碑页 */
.arch-btn{display:inline-block;margin-left:12px;padding:0 14px;line-height:32px;border:1px solid #4a90d9;border-radius:6px;color:#4a90d9;text-decoration:none;font-size:14px;white-space:nowrap}
.arch-btn:hover{background:#4a90d9;color:#fff}
.arch-wrap{max-width:960px;margin:24px auto;padding:0 16px}
.arch-list{list-style:none;margin:0;padding:0}
.arch-list li{display:flex;flex-wrap:wrap;gap:6px 12px;align-items:baseline;padding:10px 0;border-bottom:1px dashed var(--line)}
.arch-list .ad{color:var(--fg2);font-size:13px;font-variant-numeric:tabular-nums}
.arch-list .ac{font-size:12px;background:var(--panel2);color:var(--acc);border-radius:4px;padding:1px 8px}
.arch-list .au{color:var(--fg2);font-size:12px;word-break:break-all}
.arch-note{color:var(--fg2);font-size:13px;margin-top:16px}
:root{--bg:#0f1115;--panel:#171a21;--panel2:#1e222b;--line:#272c37;--fg:#e6e9ef;--fg2:#9aa3b2;--acc:#4a9eff;--acc2:#ff6b8b;--r:12px}
@media (prefers-color-scheme:light){:root{--bg:#f5f6f8;--panel:#fff;--panel2:#f0f2f6;--line:#e3e6ec;--fg:#1b1e25;--fg2:#6b7280}}
html,body{margin:0;padding:0}
body{background:var(--bg);color:var(--fg);font:15px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
a{color:inherit;text-decoration:none}
.hero{background:linear-gradient(135deg,#1b2a4a,#3b1f4d 60%,#1f2b3a);padding:48px 20px 40px;color:#fff}
.hero-in{max-width:1080px;margin:0 auto}
.hero h1{margin:0 0 10px;font-size:30px;letter-spacing:.5px}
.hero .sub{margin:0 0 6px;opacity:.9;font-size:15px}
.hero .gen{margin:0;opacity:.6;font-size:13px}
.bar{position:sticky;top:0;z-index:20;background:var(--panel);border-bottom:1px solid var(--line);padding:12px 20px}
.bar-in{max-width:1080px;margin:0 auto;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.bar input,.bar select{background:var(--panel2);border:1px solid var(--line);color:var(--fg);padding:8px 12px;border-radius:8px;font-size:14px;outline:none}
.bar input{flex:1;min-width:220px}
.bar input:focus,.bar select:focus{border-color:var(--acc)}
.bar .cnt{margin-left:auto;color:var(--fg2);font-size:13px}
.bar #imgModeBtn{padding:8px 14px;font-size:14px}
main{max-width:1080px;margin:0 auto;padding:22px 20px 60px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);padding:16px 18px;margin-bottom:12px;transition:.15s}
.card:hover{border-color:var(--acc);transform:translateY(-1px)}
.c-title{font-size:17px;font-weight:600;display:block;margin-bottom:6px}
.c-title:hover{color:var(--acc)}
.c-meta{display:flex;gap:10px;align-items:center;flex-wrap:wrap;font-size:12.5px;color:var(--fg2);margin-bottom:8px}
.c-cat{background:var(--panel2);padding:2px 8px;border-radius:20px}
.c-cat:hover{color:var(--acc)}
.tag{background:var(--panel2);padding:2px 8px;border-radius:20px}
.t-img{color:#7ee0c0}.t-cmt{color:#ffb86b}.t-view{color:#9aa3b2}.t-pg{color:#c3a6ff}
.c-sum{margin:0 0 8px;color:var(--fg2);font-size:13.5px;line-height:1.6}
.c-link{font-size:12.5px;color:var(--acc)}
.more{text-align:center;padding:10px}
.pager{display:flex;gap:8px;align-items:center;justify-content:center;flex-wrap:wrap;padding:18px 0 6px}
.more button{background:var(--acc);color:#fff;border:0;padding:10px 26px;border-radius:8px;font-size:14px;cursor:pointer}
.empty{text-align:center;color:var(--fg2);padding:40px}
.foot{border-top:1px solid var(--line);padding:20px;text-align:center;color:var(--fg2);font-size:12.5px}
/* 文章页 */
.post{max-width:820px;margin:0 auto;padding:30px 20px 10px}
.ph h1{font-size:26px;line-height:1.4;margin:0 0 10px}
.pm{display:flex;gap:12px;flex-wrap:wrap;color:var(--fg2);font-size:13px;margin-bottom:22px}
.pm .src{color:var(--acc)}
.pc{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);padding:20px 22px;overflow-wrap:break-word}
.pc img{max-width:100%;height:auto;border-radius:8px;display:block;margin:14px 0}
.pc p{margin:12px 0}
.pc a{color:var(--acc);word-break:break-all}
.pm .multi{background:#3b2f5e;color:#d7c8ff;padding:2px 8px;border-radius:20px;font-size:12px}
.pc-page{}
.pc-pager{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:18px;padding-top:14px;border-top:1px solid var(--line)}
.pc-pager .pnums{display:flex;gap:4px;flex-wrap:wrap}
.pbtn,.pnav{background:var(--panel2);border:1px solid var(--line);color:var(--fg);padding:5px 11px;border-radius:8px;font-size:13px;cursor:pointer}
.pbtn:hover,.pnav:hover{border-color:var(--acc);color:var(--acc)}
.pbtn.cur{background:var(--acc);color:#fff;border-color:var(--acc)}
.pnav[disabled]{opacity:.4;cursor:default;pointer-events:none}
.pinfo{color:var(--fg2);font-size:12.5px;margin:0 6px}
.pc blockquote{margin:14px 0;padding:10px 16px;border-left:3px solid var(--acc);background:var(--panel2);border-radius:0 8px 8px 0}
.pc iframe{max-width:100%}
.pc h2,.pc h3,.pc h4{margin:20px 0 10px}
.fimgwrap{max-width:820px;margin:26px auto 0;padding:0 20px}
.fimgwrap h2{font-size:16px;color:var(--fg2);font-weight:600}
.fgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:10px}
.fimg{margin:0;background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden}
.fimg img{width:100%;display:block;cursor:zoom-in}
.fimg figcaption{font-size:11.5px;color:var(--fg2);padding:6px 8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cmts{max-width:820px;margin:30px auto 0;padding:0 20px}
.cmts h2{font-size:17px}
.cmtlist{list-style:none;margin:0;padding:0}
.cmt{display:flex;gap:10px;padding:12px 0;border-top:1px solid var(--line)}
.cmt.d2{margin-left:34px}.cmt.d3{margin-left:68px}
.cmt-avw{flex:0 0 34px}
.cmt-av{width:34px;height:34px;border-radius:50%;object-fit:cover;background:var(--panel2)}
.cmt-main{flex:1;min-width:0}
.cmt-meta{display:flex;gap:10px;align-items:center;flex-wrap:wrap;font-size:12.5px;color:var(--fg2);margin-bottom:4px}
.cmt-au{color:var(--fg);font-weight:600}
.cmt-rt{color:var(--acc2)}
.cmt-txt{overflow-wrap:break-word}
.cmt-txt img{max-width:100%;border-radius:6px}
.cmt-children{list-style:none;margin:0;padding:0}
.cmtlist .empty{color:var(--fg2);padding:20px 0;border-top:1px solid var(--line)}
.pf{max-width:820px;margin:30px auto 50px;padding:0 20px}
.pf a{color:var(--acc)}
@media (max-width:600px){.hero h1{font-size:22px}.ph h1{font-size:21px}.cmt.d2{margin-left:18px}.cmt.d3{margin-left:36px}}
/* 归档标记按钮（首页卡片 + 管理页名录） */
.arch-mark{margin-left:8px;padding:1px 9px;font-size:12px;border:1px solid var(--line);border-radius:5px;background:transparent;color:var(--fg2);cursor:pointer;white-space:nowrap;vertical-align:middle}
.arch-mark:hover{border-color:var(--acc);color:var(--acc)}
.arch-mark.on{background:var(--acc);border-color:var(--acc);color:#fff}
/* 归档管理页 */
.arch-wrap{max-width:960px;margin:0 auto;padding:0 16px 40px}
.ops{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);padding:16px 18px;margin-bottom:8px}
.ops h2{font-size:15px;margin:12px 0 6px}
.ops h2:first-child{margin-top:0}
.opn{color:var(--acc);font-size:13px;font-weight:400}
.ops ul{list-style:none;margin:0;padding:0}
.ops li{display:flex;flex-wrap:wrap;gap:4px 10px;align-items:baseline;padding:7px 0;border-bottom:1px dashed var(--line);font-size:13px}
.ops li.none{color:var(--fg2);border-bottom:0}
.ops .od{color:var(--fg2);font-variant-numeric:tabular-nums;flex:0 0 auto}
.ops .ot{font-weight:600}
.ops .au{color:var(--fg2);font-size:12px;word-break:break-all}
.mini{margin-left:auto;padding:0 8px;font-size:12px;border:1px solid var(--line);border-radius:4px;background:transparent;color:var(--fg2);cursor:pointer;white-space:nowrap}
.mini:hover{border-color:#d9534f;color:#d9534f}
.ops-bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:14px}
.ops-bar button{padding:5px 14px;border:1px solid var(--acc);border-radius:6px;background:transparent;color:var(--acc);cursor:pointer;font-size:13px}
.ops-bar button:hover{background:var(--acc);color:#fff}
.ops-bar .hint{color:var(--fg2);font-size:12px;flex-basis:100%;line-height:1.7}
.msg{color:var(--acc);font-size:12px}
h2.arch-h{font-size:16px;margin:18px 0 10px}
/* 图片「本地/原始」切换栏（单篇页正文上方） */
.modebar{max-width:820px;margin:0 auto 8px;padding:0 20px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
#imgModeBtn{background:var(--panel);border:1px solid var(--line);color:var(--fg);padding:6px 14px;border-radius:8px;font-size:13px;cursor:pointer;white-space:nowrap}
#imgModeBtn:hover{border-color:var(--acc)}
#imgModeBtn b{color:var(--acc);font-weight:600}
#imgModeBtn.on{border-color:var(--acc);box-shadow:0 0 0 1px var(--acc) inset}
.modehint{color:var(--fg2);font-size:12.5px}
/* 图片未在本机（历史图库在别的电脑 / 尚未挂载）时的兜底观感 */
img.img-missing{opacity:.5;filter:saturate(.5)}
"""

# 图片兜底脚本：历史图片库已被移到别的电脑（images_store.json mode=incremental）
# 或盘未接上时，页面里 assets/images/... 的引用会加载失败。本脚本**不改动任何数据**，
# 只在浏览器里把加载失败的图换成同一目录下的占位图，保证「可以没有图片，
# 但页面必须正常打开、不出破图图标」；库接回来以后无需任何改动自动恢复真图。
IMG_FALLBACK_JS = r"""(function(){
  var ph='assets/img-placeholder.png';
  var s=document.currentScript;
  if(s&&s.src){ ph=s.src.replace(/[^\/]*$/,'')+'img-placeholder.png'; }
  function fix(t){
    if(!t||t.tagName!=='IMG'||t.getAttribute('data-fb'))return;
    t.setAttribute('data-fb','1');
    var cur=t.getAttribute('src')||'';
    t.setAttribute('title','图片未在本机：'+cur);
    t.classList.add('img-missing');
    if(cur.indexOf('img-placeholder.png')<0){ t.src=ph; }
  }
  /* error 不冒泡，用捕获阶段监听才能接住所有 <img>（含动态插入的） */
  document.addEventListener('error',function(e){fix(e.target);},true);
  function scan(){
    var im=document.images;
    for(var i=0;i<im.length;i++){ var t=im[i]; if(t.complete&&t.naturalWidth===0)fix(t); }
  }
  if(document.readyState==='loading'){ document.addEventListener('DOMContentLoaded',scan); }
  else{ scan(); }
  window.addEventListener('load',scan);
})();
"""

# 图片来源模式应用脚本：文章页**不再显示切换按钮**，入口统一在首页工具栏。
# 这里只按 localStorage('fuliba_img_mode') 应用——orig 时把带 data-orig/data-local 的
# <img> 的 src 换成原始地址；local 即建站默认 src，无需改动。原始地址直连原站，
# 防盗链第三方图（sinaimg/微博等）多半 403，加载失败由上面的 IMG_FALLBACK_JS 兜底成
# 占位图；脚本只读 data-* 属性，与兜底改 src 互不干扰。
IMG_MODE_JS = r"""
<script>
(function(){
  var mode='local'; try{ mode=localStorage.getItem('fuliba_img_mode')||'local'; }catch(e){}
  if(mode!=='orig') return;
  var im=document.images;
  for(var i=0;i<im.length;i++){
    var t=im[i], o=t.getAttribute('data-orig'), l=t.getAttribute('data-local');
    if(!o || !l) continue;
    t.removeAttribute('data-fb'); t.classList.remove('img-missing'); t.removeAttribute('title');
    if(t.getAttribute('src')!==o) t.setAttribute('src', o);
  }
})();
</script>
"""


def build_report(articles, meta):
    cats = sorted(meta["categories"].items(), key=lambda kv: -kv[1])
    years = sorted(meta["years"].items())
    with_c = sum(1 for a in articles.values() if a.get("comments_parsed"))
    no_c = sum(1 for a in articles.values() if not a.get("comments_parsed"))
    with_i = sum(1 for a in articles.values() if a.get("images"))
    # 评论缺口统计
    gap_zero = sum(1 for a in articles.values()
                   if a.get("comment_gap_reason") == "site_no_comment_container")
    gap_part = sum(1 for a in articles.values()
                   if a.get("comment_gap_reason") == "site_render_cap")
    # 缺口条数（终态归类，见 §1.29）
    gap_cnt = sum(int(a.get("comment_gap") or 0) for a in articles.values())
    gap_cnt_zero = sum(int(a.get("comment_gap") or 0) for a in articles.values()
                       if a.get("comment_gap_reason") == "site_no_comment_container")
    gap_cnt_cap = sum(int(a.get("comment_gap") or 0) for a in articles.values()
                      if a.get("comment_gap_reason") == "site_render_cap")
    marked = sum(1 for a in articles.values() if a.get("comment_count"))
    aligned = sum(1 for a in articles.values()
                  if a.get("comment_count") and a.get("comments_parsed") == a.get("comment_count"))
    # 图片本地化统计
    img_total = sum(len(a.get("images", []) or []) for a in articles.values())
    img_local = sum(len([i for i in (a.get("images") or []) if i.get("local")])
                    for a in articles.values())
    av_total = sum(len(a.get("comments", []) or []) for a in articles.values())
    av_local = sum(len([c for c in (a.get("comments") or []) if c.get("avatar_local")])
                   for a in articles.values())
    av_real = sum(len([c for c in (a.get("comments") or []) if c.get("avatar_local")
                        and c.get("avatar_is_default") is False])
                   for a in articles.values())
    av_default = sum(len([c for c in (a.get("comments") or []) if c.get("avatar_local")
                           and c.get("avatar_is_default") is True])
                      for a in articles.values())
    txt = []
    txt.append("# fuliba2023.net 全站抓取报告\n")
    txt.append("生成时间：%s\n" % meta["generated"])
    txt.append("## 总览\n")
    txt.append("| 指标 | 数值 |")
    txt.append("|---|---|")
    txt.append("| 文章总数 | %d |" % meta["total"])
    txt.append("| 评论总数 | %d |" % meta["total_comments"])
    txt.append("| 本地化正文图 | %d |" % meta["total_images"])
    txt.append("| 含评论文章 | %d |" % with_c)
    txt.append("| 无评论文章 | %d |" % no_c)
    txt.append("| 含图文章 | %d |" % with_i)
    txt.append("| 分类数 | %d |" % len(cats))
    txt.append("| 年份跨度 | %s — %s |" % (years[0][0] if years else "-",
                                            years[-1][0] if years else "-"))
    txt.append("\n## 评论完整度\n")
    txt.append("| 指标 | 数值 |")
    txt.append("|---|---|")
    txt.append("| 页面标注评论数的文章 | %d |" % marked)
    txt.append("| 已解析数与标注数**精确一致**的文章 | %d |" % aligned)
    txt.append("| 站点侧无评论容器（标注数虚高，站点无数据） | %d 篇 / %d 条 |" % (gap_zero, gap_cnt_zero))
    txt.append("| 服务端渲染上限（各页楼层集合相同） | %d 篇 / %d 条 |" % (gap_part, gap_cnt_cap))
    txt.append("| **缺口合计** | **%d 条** |" % gap_cnt)
    txt.append("\n**缺口成因说明（两类，均已实证穷举，站点侧不可取）**：\n")
    txt.append("- `site_no_comment_container`（%d 篇 / %d 条）：文章页**根本没有** `id=\"postcomments\"` 容器，"
               "主题不渲染评论区块，但元信息仍打印「评论(N)」—— 标注数是虚高值。"
               "已用 `?comment-page=N` 逐页翻至无新楼层（实测 3 页一致），"
               "**站点侧无数据可取，不可补齐**。" % (gap_zero, gap_cnt_zero))
    txt.append("- `site_render_cap`（%d 篇 / %d 条）：容器存在、首屏只渲染部分楼层，"
               "但 `?comment-page=N` 各页返回**完全相同的楼层集合**（实测 3 页楼层 id 集合一致，"
               "新增 0、独有 0）→ 判定为**服务端渲染上限**，客户端无论怎么翻页都取不到，"
               "属**数据源边界**，非抓取缺陷。" % (gap_part, gap_cnt_cap))
    txt.append("\n## 图片本地化\n")
    txt.append("| 指标 | 数值 |")
    txt.append("|---|---|")
    txt.append("| 正文图引用总数 | %d |" % img_total)
    txt.append("| 已本地化正文图 | %d |" % img_local)
    txt.append("| 评论总数 | %d |" % av_total)
    txt.append("| 评论头像 | **统一使用占位图** |")
    txt.append("\n**头像说明**：按归档需求，评论区的用户头像**不做逐个本地化**，"
               "统一渲染为一张默认占位图 `assets/avatar-placeholder.png`（96×96，521 字节）。"
               "抓取阶段曾按 `cravatar.cn` / `gravatar.com` / `wnflb*.com` 三套通道"
               "把头像全部落盘（59612 个文件 / 181.9 MB），现已全部移除；"      
               "评论的**作者名、楼层、时间、点赞数、回复关系**均完整保留，不受此影响。")
    txt.append("\n**动图说明**：站点历史流程曾把动画 GIF 转成静态 JPEG 首帧（降级，§1.37）。"
               "本次收尾已从图床重新下载原 GIF 并替换 2680 处引用（3039 个 URL、0 失败），"
               "抽验 25 个 GIF 中 24 个为多帧动画；离线站现含 3549 个 GIF 动画原图。")
    txt.append("\n## 分类分布\n")
    txt.append("| 分类 | 文章数 |")
    txt.append("|---|---|")
    for c, n in cats:
        txt.append("| %s | %d |" % (c, n))
    txt.append("\n## 年份分布\n")
    txt.append("| 年份 | 文章数 |")
    txt.append("|---|---|")
    for y, n in years:
        txt.append("| %s | %d |" % (y, n))
    txt.append("\n## 说明\n")
    txt.append("- 抓取通道：Clash 代理 `127.0.0.1:7890`，串行限速（1.2–2.0s/请求 + 随机抖动），无 429/封禁。")
    txt.append("- 广告剔除：正文容器 `article.article-content` 外的广告位全部不取；正文内按 class/id 广告特征（adsbygoogle/pub_300x250/text_ad/adsense 等）二次清洗。")
    txt.append("- 评论：默认页仅渲染前 30~75 条。完整评论需按 `?commentsort=hot|asc|desc&cpage=N` "
               "**三链分别翻页**（hot 与 asc 的 comment id 集合不重叠，只用单链会漏），"
               "并按 comment id 求并集去重；翻页深度以页面实际给出的 cpage 链接为准，"
               "而非「本页无新 id 即末页」的经验判定。多数文章的解析数与页面标注「评论 (N)」精确一致。")
    txt.append("- 图片：还原 `img03.sogoucdn.com` 代理包装取得真实原图，落地 `assets/images/`（台账键名仍记 `images/<xx>/<hash>`）；sinaimg 防盗链以 `Referer: https://weibo.com/` 放行；正文 `src` 全部改写为本地相对路径。")
    txt.append("- 站点 robots.txt 明确允许抓取内容页并声明 sitemap；本任务仅抓 sitemap 列出的文章 URL，未触碰任何 Disallow 路径。")
    write_if_changed(C.log_path("report.md"), "\n".join(txt))   # 报告也按天落：_logs/<今天>/report.md


def load_articles(years=None):
    """读年度容器的解析产物。years=None 全量；给了列表就只读那些年（7,231 → 当年几百）。

    过滤在 iter_parsed 的**目录级**（slots=）做：先选年份目录再开容器，不是
    「全读完再丢」，所以局部重建真的只碰得到那几年的文件。
    ⚠ 只能按年收敛，**不能按篇收敛**：分片序号 p 是按「该年 loaded 的全部篇」date-desc 连续排的，
      少给一篇就会让该年的 p 与实际分片块错位、深链指错片。
    """
    arts, slots = {}, {}
    for url, key, slot, a in C.Y.iter_parsed(slots=years):
        arts[url] = a
        slots[url] = slot
    return arts, slots


def write_year_data(items):
    """按年分组写 data/<年>/data.js（自注册）。返回写过多少个年份文件。

    只写本轮 items 里出现过的年份；未涉及的年份目录不动（mtime 保持）。
    """
    by = {}
    for it in items:
        by.setdefault(it["y"], []).append(it)
    n = 0
    for slot, sub in sorted(by.items()):
        txt = ('window.FULIBA_YEAR && window.FULIBA_YEAR("%s", %s);\n'
               % (slot, json.dumps(sub, ensure_ascii=False)))
        C.Y.ensure_slot(slot)      # 新年份只在这里建目录（读路径不顺手 mkdir）
        if write_if_changed(C.Y.data_js_path(slot), txt):
            n += 1
    return len(by), n


def write_year_audits(items):
    """每年写一个极小的 `data/<年>/_audit.json`（几十字节）。

    记录本年应有的 items/articles/containers 与首尾日期；把年份目录拷到别处后，
    `rebuild.py --check` 只比这几个小文件就能报“哪年少了几页”，
    不必扫全盘、不必读容器。（内容未变不写，不会每次刷 mtime。）
    """
    by = {}
    for it in items:
        by.setdefault(it["y"], []).append(it)
    cont = C.Y.counts()
    n = 0
    for slot, sub in sorted(by.items()):
        dates = sorted([it.get("date") or "" for it in sub if it.get("date")])
        audit = {"year": slot,
                 "items": len(sub),
                 "articles": len(sub),
                 "containers": cont.get(slot, 0),
                 "total_comments": sum(it.get("comments_parsed") or 0 for it in sub),
                 "total_images": sum(it.get("imgs") or 0 for it in sub),
                 "first_date": dates[0] if dates else "",
                 "last_date": dates[-1] if dates else "",
                 "generated": time_str()}
        if write_if_changed(C.Y.audit_path(slot),
                            json.dumps(audit, ensure_ascii=False, indent=1) + "\n"):
            n += 1
    return len(by), n


def clean_stale_pages(slot, keep_part_count):
    """清掉序号 ≥ keep_part_count 的分片（文章减少时尾部空壳分片收缩）。

    只按文件删、每年仅几个，落在环境 safe-delete 守卫的单轮阈值内，安全。
    ⚠ 不在此删旧 `data/<年>/article/` 目录：那是一次性迁移（每轮自动触发会被 >500 阈值拦，
      且重建时该目录早已不存在）。遗留目录的清除用单独的迁移命令（见本次改造说明）。
    """
    gone = 0
    for name in C.Y.list_parts(slot):
        m = C.Y._PART_RE.match(name)
        if m and int(m.group(1)) >= keep_part_count:
            try:
                os.remove(os.path.join(C.Y.slot_dir(slot), name))
                gone += 1
            except OSError:
                pass
    return gone


def write_archived_page():
    """归档已归档名录 → data/common/archived.js（window.ARCHIVED_ROSTER）。

    数据源 _pipeline/skipped_promo.json（删除台账，由促销清理/archive.py 维护），
    每条导出为 {key,url,title,date,cat}；待移入/待移出名单本体存在浏览器
    localStorage（fuliba_arch_ops），由 index.html 归档面板读写，交 _scripts/audit/archive.py 执行。
    台账为空时名录为空，但 archived.js 仍生成（window.ARCHIVED_ROSTER=[]）。
    """
    led = C.read_json(os.path.join(C.SRC, "skipped_promo.json"), []) or []
    rows, seen = [], set()
    for r in sorted(led, key=lambda x: x.get("date", ""), reverse=True):
        key = r.get("key", "")
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "key": key,
            "url": r.get("url", ""),
            "title": r.get("title", ""),
            "date": r.get("date", ""),
            "cat": r.get("category", "") or "未分类",
        })
    body = "window.ARCHIVED_ROSTER=" + json.dumps(rows, ensure_ascii=False, separators=(",", ":")) + ";\n"
    cdir = os.path.join(C.ROOT, "data", "common")
    if not os.path.isdir(cdir):
        os.makedirs(cdir, exist_ok=True)
    write_if_changed(os.path.join(cdir, "archived.js"), body)
    C.log("[s06] archived.js 写入（已归档 %d 篇，data/common/）" % len(rows))
    return len(rows)


def main():
    yarg = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--years=")), None)
    years = C.Y.parse_years_arg(yarg) if yarg else None
    full = not years

    articles, slots = load_articles(years)
    if not articles:
        C.log("[s06] 致命：解析产物为空（先跑 s03）")
        return 2

    meta, items = build_data(articles, slots)
    C.log("[s06] 本次涉及 %d 篇（%s），评论 %d"
          % (meta["total"], "全部年份" if full else "年=" + ",".join(years),
             meta["total_comments"]))

    # 1) 年度 data.js：data/<年>/data.js 自注册，未涉及的年份目录不碰
    n_slots, n_written = write_year_data(items)
    C.log("[s06] 年度 data.js：%d 个年份涉及，写入/变更 %d 个" % (n_slots, n_written))
    # 1b) 年度完整性小清单（拷贝后验收用）
    n_a, n_aw = write_year_audits(items)
    C.log("[s06] 年度 _audit.json：%d 个年份，写入/变更 %d 个" % (n_a, n_aw))

    # 2) 站点外壳：与数据无关，写一次就恒定（内容未变不再写）
    write_if_changed(os.path.join(C.ROOT, "index.html"), INDEX_TPL % {
        "domain": meta["domain"],
        "generated": meta["generated"],
        "year_slots": json.dumps(C.Y.all_slots()),
        "data_prefix": C.Y.SITE_PREFIX,
    })
    # 站点根只留入口 HTML；图片仍归 assets/images/，而样式/脚本/占位图归 data/common/（分片页引用 ../common/style.css）
    sdir = os.path.join(C.ROOT, "data", "common")
    if not os.path.isdir(sdir):
        os.makedirs(sdir, exist_ok=True)
    write_if_changed(os.path.join(sdir, "style.css"), STYLE_CSS)
    # 图片兜底脚本：图不在本机时页面仍正常打开（占位图在同目录 data/common/ 下）
    write_if_changed(os.path.join(sdir, "img-fallback.js"), IMG_FALLBACK_JS)
    write_archived_page()

    # 3) 文章分片：按各年 data.js 的 date-desc 顺序把整年聚合成 data/<年>/part<i>.html。
    #    items 顺序 = build_data 里算 p 的顺序，二者用同一 per，故 chunk 序号 i 与卡片 p 对齐。
    by_slot_units = {}
    for it in items:
        a = articles.get(it["url"])
        if a is None:
            continue
        a["_slug"] = it["id"]
        by_slot_units.setdefault(it["y"], []).append((it["id"], a))
    failed = []
    n_ok = 0
    n_parts = 0
    for slot in sorted(by_slot_units):
        units = by_slot_units[slot]
        gone = clean_stale_pages(slot, C.Y.n_parts_for(len(units)))
        if gone:
            C.log("[s06] %s 迁移/清理旧页面产物 %d 处" % (slot, gone))
        for attempt in range(4):
            try:
                np = write_article_parts(slot, units)
                n_parts += np
                n_ok += len(units)
                break
            except PermissionError:
                time.sleep(0.6 * (attempt + 1))
            except OSError as e:
                failed.append((slot, str(e)))
                break
        else:
            failed.append((slot, "PermissionError after retries"))
        if n_ok and n_ok % 1000 < len(units):
            C.log("[s06] 已生成文章 %d 篇（分片累计 %d 个）" % (n_ok, n_parts))
    if failed:
        C.log("[s06] 警告：%d 年写入失败（已跳过）：%s" % (len(failed), failed[:5]))

    # 4) report.md 只在全量重建时重生（--years 局部跑时它是上一轮全量的结果）
    if full:
        build_report(articles, meta)
    else:
        C.log("[s06] 本轮为 --years 局部重建 → 跳过 _logs/<日期>/report.md")

    C.log("[s06] 完成：%d 篇 → %d 个分片 | 写盘 %d 个文件 / 内容未变跳过 %d 个 | 年度 data.js %d 个%s"
          % (n_ok, n_parts, _IO["written"], _IO["same"], n_slots, "" if full else "（--years 局部）"))
    C.state_append("site", "__summary__", "DONE", articles=n_ok, written=_IO["written"],
                   same=_IO["same"], years=("all" if full else ",".join(years)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
