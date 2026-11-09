# -*- coding: utf-8 -*-
"""
s04s_purge_dead.py — 清除「从未落盘」死链图在项目里的全部原文地址记录

背景：2026-10-27 死链封盘后，仍有 6615 张从未取到过的正文图以「原文地址」形态
留在 parsed / 原始 HTML 缓存 / 子页缓存 / 列表页缓存 / manifest / article.html 中，
每轮增量都会被 s04i 重新列为待下载目标。本脚本把这些地址从全部文本层抹掉。

铁律：只改文本记录，绝不删 images/ 下任何文件。本脚本不含任何针对 images/ 的
写删操作，并在动手前断言死链集合中每个 URL 的磁盘哈希都不存在。

清洗规则：
  - _rawdata/html、_pipeline/subpages、_rawdata/pages：命中死链的 <img> 整块替换为
    <img class="fl-deadimg" alt="">（无 src，s03 的 extract_images 会跳过，
    因此重解析也不会把地址造回来）；非 img 标签内的死链属性改为 "#"。
  - _pipeline/parsed：images[] / pages[].images 中命中死链的条目整条删除；
    content_html 等处替换为 <img src="assets/img-placeholder.png" alt="">
    （保留占位灰框，但不再携带任何原址）。
  - articles/*/article.html：同 parsed，src 用 ../../assets/ 前缀（随后 s06 会重建）。
  - image_manifest.json：删除以死链为 key 的空条目。

用法：
  _scripts\\py.cmd _scripts\\images\\s04s_purge_dead.py                 # 试运行：只统计，不写一个字节
  _scripts\\py.cmd _scripts\\images\\s04s_purge_dead.py --apply         # 执行（受影响文件先复制到项目外备份区）
  _scripts\\py.cmd _scripts\\images\\s04s_purge_dead.py --apply --backup=D:\\其他\\目录
  _scripts\\py.cmd _scripts\\images\\s04s_purge_dead.py --list=<地址清单>   # 改用外部清单（第二轮：只挂在 <img> 属性上、
                                                      #  从没进过 images[] 的那批地址）
"""
import os
import re
import sys
import json
import html as HTML
import shutil
import hashlib
import glob as _glob
from datetime import datetime
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # _scripts/
import common as C

ROOT = C.ROOT
SRC = C.SRC
# 列表页缓存 2026-11-04 起在 _rawdata/pages/（旧布局的 html/subpages 仍在台账区）
CACHE_PAGES_DIR = C.PAGES_DIR
# 图片库模式（mode=incremental 时「本机缺文件」不等于「从未落盘」，见 is_landed）
NEW_ONLY = C.new_images_only()
PH_PARSED = "assets/img-placeholder.png"
PH_ARTICLE = "../../../assets/img-placeholder.png"   # 单篇页在 data/<年>/article/（三层）
IMG_DEAD = '<img class="fl-deadimg" alt="">'
TRAILING = "," + "." + ";" + ":" + "'" + '"' + ")" + "&" + ">" + "/" + " " + "\t" + "\r" + "\n" + "\\"

TOKEN_RE = re.compile(r"""https?://[^\s"'<>\[\]]+""")
TAG_RE = re.compile(r"<[^>]+>", re.S)
IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.I | re.S)

deadset = set()


def in_dead(tok):
    """判断 token 是否死链原串。HTML.unescape 内部带正则、代价高，
    只在 token 真含实体或尾标点时才走，否则几千万个 token 会把全树扫描拖慢一个量级。"""
    if not tok:
        return None
    if tok in deadset:
        return tok
    if "&" in tok:
        u = HTML.unescape(tok)
        if u in deadset:
            return u
    if tok[-1] in TRAILING:
        t = tok.rstrip(TRAILING)
        if t and t in deadset:
            return t
    return None


def has_dead(s):
    """快路径：只抽 URL token 再查集合。切勿用「N 个域名做正则交替」预筛，
    在 2 GB 文本上会慢两个数量级（实测扫全树 13 分钟未完 → 改此法 3 分钟）。"""
    if not s or "://" not in s:
        return False
    return any(in_dead(m.group(0)) for m in TOKEN_RE.finditer(s))


def count_dead(s):
    """文本中死链地址的出现次数（计数与复核用）。"""
    if not s or "://" not in s:
        return 0
    return sum(1 for m in TOKEN_RE.finditer(s) if in_dead(m.group(0)))


def strip_bare(s, repl=""):
    """把裸死链地址替换掉（保留 token 里不属于该 URL 的尾串）。"""
    if not s or "://" not in s:
        return s

    def sub(m):
        t = m.group(0)
        hit = in_dead(t)
        if hit is None:
            return t
        rest = t[len(hit):] if t.startswith(hit) else ""
        return repl + rest

    return TOKEN_RE.sub(sub, s)


def scrub_attrs(tag, repl="#"):
    """非 img 标签：死链属性值一律换成 repl。"""
    return strip_bare(tag, repl)


def scrub_html(s, img_repl):
    if not has_dead(s):
        return s, 0
    n = [0]

    def fix_img(m):
        tag = m.group(0)
        if not has_dead(tag):
            return tag
        n[0] += 1
        return img_repl

    def fix_tag(m):
        tag = m.group(0)
        if not has_dead(tag):
            return tag
        n[0] += 1
        return scrub_attrs(tag)

    out = IMG_TAG_RE.sub(fix_img, s)
    out = TAG_RE.sub(fix_tag, out)
    out = strip_bare(out, "")
    return out, n[0]


def drop_dead_images(lst):
    """images[] / pages[].images：命中死链的条目整条删除。"""
    kept = []
    dropped = 0
    for im in lst or []:
        if isinstance(im, dict) and (has_dead(im.get("url")) or has_dead(im.get("raw_src"))):
            dropped += 1
            continue
        kept.append(im)
    return kept, dropped


# ---------------------------------------------------------------- 死链集合
def load_index():
    """manifest 与磁盘非空图片哈希索引（判断「有没有这张图」的唯一依据）。"""
    man = C.read_json(os.path.join(SRC, "image_manifest.json"), {}) or {}
    disk = set()
    for dp, dn, fn in os.walk(C.IMAGES_DIR):
        for f in fn:
            try:
                if os.path.getsize(os.path.join(dp, f)) > 0:
                    disk.add(f.rsplit(".", 1)[0])
            except OSError:
                pass

    return man, disk


def is_landed(u, man, disk):
    """manifest 有可用 local，或按 sha1[:20] 在磁盘命中 → 视为已落地，绝不列入清洗。

    增量本机模式（mode=incremental，历史图在另一台电脑）：manifest 有条目就算已落地。
    本机上 stat 不到是常态，按旧口径会把 13.8 万个正常地址当成「从未落盘的死链」抹掉。
    """
    loc = (man.get(u) or {}).get("local")
    if loc:
        if NEW_ONLY:
            return True
        fp = C.img_disk_path(loc)
        try:
            if os.path.getsize(fp) > 0:
                return True
        except OSError:
            pass
    return hashlib.sha1(u.encode("utf-8")).hexdigest()[:20] in disk


def build_dead(man, disk):
    """第一轮口径：images[].url（含分页 pages[].images）中从未落地的地址。"""
    body = set()
    for dp, dn, fn in os.walk(os.path.join(SRC, "parsed")):
        for f in fn:
            if not f.endswith(".json"):
                continue
            a = C.read_json(os.path.join(dp, f), None)
            if not a:
                continue
            pools = [a.get("images", [])]
            for pg in a.get("pages", []):
                pools.append(pg.get("images", []))
            for pool in pools:
                for im in pool or []:
                    u = (im or {}).get("url")
                    if u and u.startswith("http"):
                        body.add(u)
    return sorted(u for u in body if not is_landed(u, man, disk)), len(body)



# ---------------------------------------------------------------- 文件处理
def read(p):
    with open(p, encoding="utf-8", errors="replace") as f:
        return f.read()


def process(path, kind):
    """返回 (path, kind, 命中次数, 新内容)；命中 0 表示无需改动。"""
    try:
        raw = read(path)
    except OSError:
        return (path, kind, 0, None)
    if count_dead(raw) == 0:
        return (path, kind, 0, None)

    hits = 0
    if kind == "rawtext":
        # 大体积汇总 JSON（articles.json）：不解析、只把死链子串抹掉，
        # JSON 里的 \" 转义会残在 token 尾部，由 TRAILING 反斜杠吸收，结构不受影响。
        before = count_dead(raw)
        if before == 0:
            return (path, kind, 0, None)
        out = strip_bare(raw, "")
        if count_dead(out):
            print("[跳过] %s 清洗后仍残留 %d 处，不写入" % (os.path.relpath(path, ROOT), count_dead(out)))
            return (path, kind, 0, None)
        return (path, kind, before, out)

    if kind in ("parsed", "article", "cache", "plainjson"):
        img_repl = {"parsed": PH_PARSED, "article": PH_ARTICLE,
                    "cache": IMG_DEAD, "plainjson": PH_PARSED}[kind]
        before = count_dead(raw)
        if before == 0:
            return (path, kind, 0, None)
        if kind in ("parsed", "plainjson"):
            try:
                obj = json.loads(raw)
            except Exception:
                obj = None
            if obj is None:
                out = scrub_html(raw, img_repl)[0]
                out = strip_bare(out, "")
                return (path, kind, before, out) if count_dead(out) == 0 else (path, kind, 0, None)
            drop = [0]

            def walk(o):
                if isinstance(o, dict):
                    res = {}
                    for k, v in o.items():
                        if isinstance(k, str) and has_dead(k):
                            continue
                        if k == "images" and isinstance(v, list):
                            v, d = drop_dead_images(v)
                            drop[0] += d
                        res[k] = walk(v)
                    return res
                if isinstance(o, list):
                    return [walk(x) for x in o]
                if isinstance(o, str):
                    if "<" in o and ">" in o:
                        return strip_bare(scrub_html(o, img_repl)[0], "")
                    return strip_bare(o, "")
                return o

            newtxt = json.dumps(walk(obj), ensure_ascii=False, indent=2)
            if newtxt == raw:
                return (path, kind, 0, None)
            if count_dead(newtxt):
                print("[跳过] %s 清洗后仍残留 %d 处，不写入"
                      % (os.path.relpath(path, ROOT), count_dead(newtxt)))
                return (path, kind, 0, None)
            return (path, kind, before, newtxt)
        out = strip_bare(scrub_html(raw, img_repl)[0], "")
        if count_dead(out):
            print("[跳过] %s 清洗后仍残留 %d 处，不写入" % (os.path.relpath(path, ROOT), count_dead(out)))
            return (path, kind, 0, None)
        return (path, kind, before, out)

    if kind == "manifest":
        obj = C.read_json(path, {}) or {}
        new = {k: v for k, v in obj.items() if k not in deadset}
        if len(new) == len(obj):
            return (path, kind, 0, None)
        return (path, kind, len(obj) - len(new), json.dumps(new, ensure_ascii=False, indent=2))

    return (path, kind, 0, None)


def targets():
    t = []
    for dp, dn, fn in os.walk(os.path.join(SRC, "parsed")):
        for f in fn:
            if f.endswith(".json"):
                t.append((os.path.join(dp, f), "parsed"))
    for name in ("html", "subpages"):
        for dp, dn, fn in os.walk(os.path.join(SRC, name)):
            for f in fn:
                t.append((os.path.join(dp, f), "cache"))
    for dp, dn, fn in os.walk(CACHE_PAGES_DIR):
        for f in fn:
            t.append((os.path.join(dp, f), "cache"))
    for p in _glob.glob(os.path.join(ROOT, "articles", "*", "article.html")):
        t.append((p, "article"))
    t.append((os.path.join(SRC, "image_manifest.json"), "manifest"))
    t.append((os.path.join(SRC, "articles.json"), "rawtext"))
    for rel in ("parsed_summary.json", "resume_wayback.json"):
        p = C.pipeline_find(rel)          # 2026-11-04 起这类过程文件按日期分目录，统一走 pipeline_find
        if p:
            t.append((p, "plainjson"))
    return t


def main():
    apply_it = "--apply" in sys.argv
    if not os.path.isdir(os.path.join(SRC, "parsed")):
        # 年度化后原料已搬进 _pipeline/material/<年>/*.jsonl，本脚本只认旧布局。
        # 死链地址清除已于 2026-10-29 两轮收尾（备份在项目外），此处不半新不旧地跑。
        print("[s04s] 未找到旧布局 _pipeline/parsed——原料已在年度容器 _pipeline/material/<年>/*.jsonl；"
              "本脚本只面向旧布局，不跑（死链清除已于 2026-10-29 收尾）")
        return 0
    if NEW_ONLY:
        print("[s04s] 图片库模式=incremental：已落地判定改按 manifest（历史图在另一台电脑），"
              "不会把正常地址当死链")
    backup_root = None
    list_path = None
    for a in sys.argv[1:]:
        if a.startswith("--backup="):
            backup_root = a.split("=", 1)[1]
        elif a.startswith("--list="):
            list_path = a.split("=", 1)[1]

    man, disk = load_index()
    if list_path:
        # 第二轮口径：外部给一批「本地无文件」的图片地址（例如只出现在 <img> 属性、
        # 从没进过 images[] 的那些），逐条再确认一次未落地才纳入清洗。
        given = [l.strip() for l in open(list_path, encoding="utf-8") if l.strip()]
        # 防呆：裸域名/无路径地址（页脚首页链接、跳转链接）不是图片，一旦纳入会误伤
        # 上千篇文章的正常内容链接，必须挡在门外。
        bare = [u for u in given if not (urlparse(u).path or "").strip("/")]
        if bare:
            print("[剔除] %d 条无路径的非图片地址，不纳入清洗：%s" % (len(bare), bare[:5]))
            given = [u for u in given if u not in set(bare)]
        dead = [u for u in given if not is_landed(u, man, disk)]
        nbody = len(given)
        src_note = "来源清单 %s（给定 %d，未落地 %d，已落地被剔除 %d）" % (
            list_path, len(given), len(dead), len(given) - len(dead))
    else:
        dead, nbody = build_dead(man, disk)
        src_note = "来源 images[] 口径"
    deadset.update(dead)
    hosts = set(urlparse(u).netloc.lower() for u in dead if "://" in u) - {""}

    print("=" * 70)
    print("死链图记录清洗  %s   模式=%s" % (datetime.now().strftime("%F %T"), "APPLY" if apply_it else "DRY-RUN"))
    print("=" * 70)
    print("正文图 URL 去重 %d | 从未落盘（死链）%d | 死链域名 %d 个 | %s" % (nbody, len(dead), len(hosts), src_note))

    # 铁律守卫：死链集合里绝不允许存在已落盘的 URL
    bad = [u for u in dead if (man.get(u) or {}).get("local")]
    if bad:
        print("[中止] 死链集合含已落地条目 %d 个，逻辑异常，未改动任何文件" % len(bad))
        return 2
    print("[守卫] 死链集合 %d 个 URL 全部无本地文件，本脚本不会触碰 images/ 下任何文件" % len(dead))

    tg = targets()
    print("[扫描] 目标文件 %d 个" % len(tg))

    changed = {}

    def record(res):
        path, kind, hits, new = res
        if hits and new is not None:
            changed.setdefault(kind, []).append((path, hits, new))

    pool_tg = [x for x in tg if x[1] != "rawtext"]
    serial_tg = [x for x in tg if x[1] == "rawtext"]
    with ThreadPoolExecutor(max_workers=16) as ex:
        for res in ex.map(lambda a: process(a[0], a[1]), pool_tg):
            record(res)
    for path, kind in serial_tg:          # 数百 MB 的汇总件单独串行，避免并发占内存
        record(process(path, kind))

    order = ["parsed", "cache", "article", "manifest", "plainjson", "rawtext"]
    tot_files = tot_hits = 0
    for k in order:
        lst = changed.get(k, [])
        h = sum(x[1] for x in lst)
        tot_files += len(lst)
        tot_hits += h
        print("  %-10s 需改动 %6d 个文件 | 清除 %7d 处" % (k, len(lst), h))
    print("[合计] 文件 %d | 记录 %d 处" % (tot_files, tot_hits))

    if not apply_it:
        print("\n[DRY-RUN] 未写入。加 --apply 执行（受影响文件会先复制到项目外备份区）")
        return 0
    if not tot_files:
        print("[完成] 无需改动")
        return 0

    if not backup_root:
        backup_root = os.path.join(os.path.dirname(ROOT), "_dead_purge_backup_" + datetime.now().strftime("%Y%m%d_%H%M"))
    backup_root = os.path.abspath(backup_root)
    # 备份区里原样保留着死链地址，必须落在项目之外，否则清洗等于没做
    if os.path.commonpath([os.path.normcase(backup_root), os.path.normcase(ROOT)]) == os.path.normcase(ROOT):
        print("[中止] 备份目录 %s 位于项目内，会把待清除的地址重新写回项目" % backup_root)
        print("       请用 --backup=<项目外的绝对路径>，例如 D:\\workspace\\_dead_purge_backup")
        return 3
    print("[备份] 复制受影响文件 -> %s" % backup_root)
    n = 0
    for k, lst in changed.items():
        for path, _h, _new in lst:
            dst = os.path.join(backup_root, os.path.relpath(path, ROOT))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(path, dst)
            n += 1
    print("[备份] 已复制 %d 个文件" % n)

    print("[写入] 开始清洗…")
    w = 0
    for k, lst in changed.items():
        for path, _h, new in lst:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(new)
            w += 1
    print("[写入] 完成 %d 个文件" % w)

    # 复核：全站再扫一遍，死链地址命中必须为 0
    print("[复核] 扫描 %d 个文件…" % len(tg))
    left = []

    def check(path):
        try:
            c = count_dead(read(path))
        except OSError:
            return None
        return (path, c) if c else None

    with ThreadPoolExecutor(max_workers=16) as ex:
        for r in ex.map(check, [p for p, _ in tg]):
            if r:
                left.append(r)
    print("[复核] 残留死链地址的文件 %d 个，共 %d 处" % (len(left), sum(x[1] for x in left)))
    for p, c in left[:10]:
        print("        %s  (%d)" % (os.path.relpath(p, ROOT), c))

    lf = os.path.join(SRC, "lost_images_by_year.txt")
    if os.path.exists(lf):
        os.remove(lf)
        print("[清理] 已删除死链分类明细 _pipeline/lost_images_by_year.txt")

    if left:
        print("PURGE_DEAD_INCOMPLETE %d" % sum(x[1] for x in left))
        return 1
    print("PURGE_DEAD_OK files=%d records=%d" % (w, tot_hits))
    return 0


if __name__ == "__main__":
    sys.exit(main())
