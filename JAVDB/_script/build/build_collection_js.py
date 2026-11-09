# -*- coding: utf-8 -*-
"""actors.js / series.js 的**唯一**生成入口（页面直读的那两个数据文件）。

两种用法（字段 schema 只有一份定义，别再在别处手拼）：
  python build_collection_js.py                                # 从 _pipeline/collection_ckpt.json 重建
  import build_collection_js as bc; bc.rebuild(store)          # collection_spider 跑完直接复用

字段口径（2026-11-04 定，改数据前先读这段）：
  count      = len(codes)          实际能点开的番号数；演员页 section-meta 的「N 部影片」含重复/已下架会虚高
  page_total = ckpt 里的页面原始数  仅供对账，前端一律不读
  cover      = 该条目 date 最新影片的封面（本地 local 且文件在场，否则远程 img）；系列没有头像，卡片图全靠它
  avatar_local 必须写：演员头像本地化后前端只认 assets/avatars/，缺了字段整站头像就没了
写盘走 atomic_io.write_verified（槽位副本，硬杀不撕裂；本机沙盒禁 rename，见 RESUME 第 5 节坑 1）。
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
import json, io, os, re, shutil, datetime

import win_utf8        # noqa: F401  重定向/管道下固定 UTF-8，否则 ✅ 会砸死进程
import atomic_io       # noqa: E402

DATA = os.path.join(ROOT, "data")                        # 站点数据与名单
SITE = os.path.join(DATA, "site")    # 页面要读的 *.js（HTML 写死引用）
ASSETS = os.path.join(ROOT, "assets")
WB = os.path.join(ROOT, "_pipeline")     # 过程中文件：断点 / cookie / 临时切片（删了会丢进度）
CKPT = os.path.join(WB, "collection_ckpt.json")
MOVIES_JS = os.path.join(SITE, "movies.js")
MV_VAR = "window.JAVDB_MOVIES = "   # 项目统一约定：等号两侧各一空格

GROUPS = (("actors", "window.JAVDB_ACTORS", os.path.join(SITE, "actors.js"), "演員"),
          ("series", "window.JAVDB_SERIES", os.path.join(SITE, "series.js"), "系列"))

# 入口页：HTML 直读 data/site/*.js
HTML_REFS = ("collection.html", "mylist.html", "top250.html")
REF_RE = re.compile(r'(src="data/site/[A-Za-z0-9_]+\.js)(?:\?v=\d{8})?(")')
# 前端懒加载磁力桶用的版本号常量（拼串生成的 <script src> 只能靠这个常量刷）
BUILD_V_RE = re.compile(r"BUILD_V\s*=\s*'\d{8}'")


def bump_refs(version=None, html_files=HTML_REFS):
    """把页面里 data/site/*.js 的引用统一刷成 `?v=<YYYYMMDD>`（默认今天）。

    为什么必须做：这些 js 是天天重写的**数据文件**，而 HTML 引用没版本号时浏览器会吃磁盘缓存
    （2026-11-04 实测两次「数据已重建但页面还是旧的」）。日期一变查询串就变 → 缓存自动失效。
    只改引用串、不动其它内容；同一天重复跑不会产生额外修改。返回 (版本, 实际改过的文件)。"""
    version = version or datetime.date.today().strftime("%Y%m%d")
    touched = []
    for name in html_files:
        p = os.path.join(ROOT, name)
        if not os.path.isfile(p):
            continue
        t = io.open(p, encoding="utf-8").read()
        new = REF_RE.sub(lambda m: "%s?v=%s%s" % (m.group(1), version, m.group(2)), t)
        new = BUILD_V_RE.sub("BUILD_V = '%s'" % version, new)     # 同步刷 loadMag 的 ?v= 常量
        if new != t:
            # HTML 是人工维护的源文件（不像 js 可重建）：先存当日备份，再直接写目标。
            # 不用 atomic_io 的 .a/.b 槽位：那会在仓库根落一堆页面副本垃圾，而本改动只动一行引用串。
            shutil.copyfile(p, atomic_io.backup_path(name))
            io.open(p, "w", encoding="utf-8").write(new)
            touched.append(name)
    return version, touched


def load_ckpt():
    d, _from = atomic_io.read_any(CKPT)          # 主文件坏了还会回退 .a/.b 槽位
    if not d:
        raise SystemExit("读不到 collection_ckpt.json（含 .a/.b 槽位），拒绝生成")
    return d


def load_movies():
    """读 movies.js 拿每部影片的 date/local/img（挑封面用）。读不到就退出，绝不静默写空 cover。"""
    if not os.path.isfile(MOVIES_JS):
        raise SystemExit("找不到 data/site/movies.js，拒绝生成（否则所有 cover 会变成空）")
    t = io.open(MOVIES_JS, encoding="utf-8").read()
    if MV_VAR not in t:
        raise SystemExit("movies.js 里找不到 `%s`（前缀被改了？），拒绝生成" % MV_VAR)
    d, _ = json.JSONDecoder().raw_decode(t[t.index(MV_VAR) + len(MV_VAR):].lstrip())
    if not d:
        raise SystemExit("movies.js 解析出 0 条，拒绝生成（多半文件被截断）")
    return d


def cover_of(codes, movies):
    """条目封面 = 其中「最新影片」（date 最大）的封面；优先本地 local（文件确实在），回退远程 img。
    全部没 date 时退回第一个有封面的（抓取顺序就是页面顺序，页面默认最新在前）。"""
    best = None
    for c in codes:
        m = movies.get(c)
        if not m:
            continue
        loc = (m.get("local") or "").replace("\\", "/")
        src = loc if (loc and os.path.isfile(os.path.join(ROOT, loc.replace("/", os.sep)))) else (m.get("img") or "")
        if not src:
            continue
        d = m.get("date") or ""
        if best is None or d > best[0]:
            best = (d, src)
    return best[1] if best else ""


def build_items(src, movies):
    items = []
    for iid, it in src.items():
        av_local = "assets/avatars/%s.jpg" % iid
        if not os.path.exists(os.path.join(ASSETS, "avatars", iid + ".jpg")):
            av_local = ""
        codes = it.get("codes", [])
        items.append({
            "id": iid, "name": it["name"], "avatar": it.get("avatar", ""),
            "avatar_local": av_local,
            "count": len(codes),
            "page_total": it.get("count", 0) or 0,   # 页面原始显示数，仅供对账，前端不用
            "codes": codes, "done": bool(it.get("done")),
            "cover": cover_of(codes, movies),        # 无头像时的卡片图（系列基本都没头像）
        })
    items.sort(key=lambda x: -x["count"])
    return items


def rebuild(store, quiet=False):
    """写 actors.js / series.js。store 与爬虫的 store 同构：{"actors": {id: {...}}, "series": {...}}。"""
    movies = load_movies()
    out = []
    for key, var, fn, label in GROUPS:
        items = build_items(store.get(key) or {}, movies)
        data = {"updated": datetime.date.today().isoformat(), "items": items}
        body = ("/* JavDB 收藏%s（自动生成，勿手改） */\n" % label) + var + " = " + \
            json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n"
        atomic_io.write_verified(fn, body, verify=False)
        done = sum(1 for x in items if x["codes"])
        nocover = sum(1 for x in items if not x["avatar_local"] and not x["avatar"] and not x["cover"])
        codes_total = sum(len(x["codes"]) for x in items)
        out.append({"file": fn, "items": len(items), "done": done, "codes": codes_total, "nocover": nocover})
        if not quiet:
            print("%s: items=%d 已抓取=%d 总影片条目=%d | 无头像且无封面=%d"
                  % (fn, len(items), done, codes_total, nocover))
    ver, touched = bump_refs()
    if not quiet:
        print("页面引用版本号 -> ?v=%s（改动文件：%s）" % (ver, ", ".join(touched) or "无，已是当日版本"))
    return out


if __name__ == "__main__":
    rebuild(load_ckpt())
