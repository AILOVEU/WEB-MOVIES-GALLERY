# -*- coding: utf-8 -*-
"""
years.py — 年度层（单一事实源）：路径解析 + 一篇一原料容器（JSONL）读写

背景（2026-10-31 年度化，2026-11-04 搬进 data/，2026-11-09 原料迁出到 _pipeline/）：项目按**年份**自包含，
一年一个目录。发布物在站点根的 `data/`，原料在**同级**的 `_pipeline/material/`：

    <ROOT>/data/2026/            站点发布物（要拷去服务器的）
        data.js                  该年列表元数据，自注册 window.FULIBA_YEAR(2026, [...])
        _audit.json              该年完整性小清单（几十字节，拷贝后验收用）
        part<i>.html             文章静态分片（2026-11-08 起，取代 article/<key>.html）：
                                 一片 ≈150 篇，每篇是片内一个 <div class="shard-post" id="<key>"> 锚点
    <ROOT>/_pipeline/material/2026/
        <url_key>.jsonl          原料容器：一篇文章的原始 HTML + 解析产物 + 内部分页子页

为什么原料迁出 `data/`（2026-11-09）：`data/` 是「拷出去即交付」的发布层，原料（含 150KB 原始 HTML 的
容器，7,250 个）不是发布物；混在 `data/` 里既放大拷贝量、又与 JAVBUS「data/ 只放发布物」的模型不符。
迁到同级 `_pipeline/material/<年>/` 后，`data/` 只剩分片+data.js+_audit（≈100 内），且仍保持**一篇一文件**
——`_write_parts` 的「内容相同不写盘（保 mtime）」增量幂等、并行写安全、update.py 按 basename 备份/回滚全部不变。
（曾考虑把原料也聚合成每年一文件：会牺牲上述增量/并发安全，不划算，故 Stage B 只做「迁出」不做「聚合」。）

层深：分片直接落在年份目录（`data/`(1) / `<年>/`(2)）→ 回站点根要 `../..`（years.PART_PAGE_BASE），
页内 href 是 `../../assets/images/…`。旧的 `article/`(3) 单篇页要 `../../..`（years.PAGE_BASE），
仅保留常量供迁移/兼容，新代码一律用 PART_PAGE_BASE。
前缀只允许由本模块的 PAGE_BASE / PART_PAGE_BASE 与 s06 的 base 变量给出，禁止在模板里硬写点数。

为什么文章改成静态分片（part<i>.html）而不是每篇一个 html：7,250 篇逐篇文件让发布层达 14,532 个文件，
整树复制/杀软扫描/MFT 元数据开销大；聚合成年×~150 篇/片（共 ≈56 片）后文件数减半、且仍保留
`file://` 静态浏览与深链（part<p>.html#<key>），无需前端 JS 重写正文/评论。代价：单篇 URL 变成
「分片#锚点」，逐篇独立文件不再有。

约定与红线：
- `assets/`（含图片库 `assets/images/`）保持**全局**，绝不进年度目录。
- 台账里的图片键统一是 `images/<xx>/<hash>.ext`，翻成站点路径要过 common.site_rel()。
- 根工作台只留与年无关的全局层：_pipeline/（urls.json / image_manifest.json /
  images_store.json / state_*.jsonl / pacer_state.json / resume_*.json）与
  _rawdata/（sitemaps / pages）。**年度原料容器在 _pipeline/material/<年>/**（2026-11-09 起）。
- index.html 只此一份（站点根），预枚举 YEAR_MIN..YEAR_MAX，新年份只在 data/ 下建目录、不改任何代码或页面。

容器格式（JSONL，一行一个片段，顺序 meta → parsed → raw → sub×N）：
    {"t":"meta","url":…,"key":…,"year":"2026","date":"2026-10-31","stamp":{…},
     "has_raw":true,"has_parsed":true,"n_sub":N,"n_comments":N,"n_images":N}
    {"t":"parsed", …原 _pipeline/parsed/<k>.json 的全部字段…}
    {"t":"raw","html":"<!doctype html>…"}
    {"t":"sub","n":2,"html":"…"}          ← 0..N 行
只读解析产物的脚本（s04d/s06/_verify/s04i/s04f）用 read_parsed() 读到 parsed 行就停，
不必吞下 150KB 的原始 HTML；需要原始 HTML 的脚本（s02b/s03）才往后读。
读取端一律“解出 t 再判断”，不比对行前缀（旧转换出的容器里 "t" 不在首位、raw 在最后一行）。

约定：本模块**不得** import common（common 要 import 本模块）。
"""
import hashlib
import io
import json
import os
import re
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(ROOT, "data")     # 年份发布目录的父目录（站点发布层）
# 原料容器根（2026-11-09 从 data/<年>/material 迁出到 _pipeline/material/<年>）：
# data/ 只留发布物，原料与台账同归工作台 _pipeline，仍保持一篇一 .jsonl。
MATERIAL_ROOT = os.path.join(ROOT, "_pipeline", "material")

YEAR_MIN = 2011
YEAR_MAX = 2050
UNDATED = "undated"
MATERIAL_SUFFIX = ".jsonl"
ARTICLE_SUBDIR = "article"        # 旧：data/<年>/article/<url_key>.html（分片前的单篇页，迁移用）
MATERIAL_SUBDIR = "material"      # 旧：data/<年>/material/<url_key>.jsonl（2026-11-09 迁到 MATERIAL_ROOT）
LEGACY_MATERIAL_SUBDIR = "_src"   # 仅改名迁移时识别旧目录用

# 站点根相对的访问前缀（index.html、分片页都从这里取，改目录层深只改这几行）
SITE_PREFIX = "data/"             # 站点根 → 年份目录：data/<年>/…
PAGE_BASE = "../../.."            # 旧：data/<年>/article/<key>.html → 站点根（迁移/兼容保留）

# 年度目录名的样子：4 位年份，或 undated
_YEAR_RE = re.compile(r"^\d{4}$")
# 单篇页文件名的样子：16 位哈希 + .html（分片前的旧布局，迁移时清理用）
_KEY_PAGE_RE = re.compile(r"^[0-9a-f]{16}\.html$")

# 文章静态分片（2026-11-08 起）：把一篇一 html 聚合成 data/<年>/part<i>.html，
# 一篇 = 分片内一个 <div class="shard-post" id="<key>"> 锚点，靠 hash 路由逐篇显示。
# 分片直接落在年份目录下（不再嵌 article/ 子目录）→ 回站点根比单篇页少一层。
PART_PREFIX = "part"             # data/<年>/part<i>.html
PART_PAGE_BASE = "../.."         # data/<年>/part<i>.html → 站点根
ARTICLE_PER_PART = 150           # 每片最多多少篇（控分片体积在几 MB 内）
_PART_RE = re.compile(r"^part(\d+)\.html$")
# 分片内每篇文章的包裹锚点：写侧(s06)与摘除侧(remove_article_from_parts)共用，防格式漂移
SHARD_UNIT_OPEN = '<div class="shard-post" id="'
# 分片内「正文锚点区」与「共享脚本区」的分界标记（摘篇时界定最后一个锚点的右边界）
SHARD_SCRIPTS_MARK = "<!--shard-scripts-->"


def url_key(url):
    """URL → 16 位哈希（与 common.url_key 同算法，这里自带以免循环 import）。"""
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- 路径
def all_slots():
    """index.html 预枚举的年份槽位：2011..2050 + undated 兜底槽。"""
    return [str(y) for y in range(YEAR_MIN, YEAR_MAX + 1)] + [UNDATED]


def year_from_date(date_str):
    """'2026-10-31' → '2026'；取不到合法年份就归 undated 槽（不丢数据）。"""
    s = (date_str or "").strip()
    if len(s) >= 4 and s[:4].isdigit() and 1990 <= int(s[:4]) <= 2200:
        return s[:4]
    return UNDATED


def slot_dir(slot):
    """该年的发布目录 `<站点根>/data/<年>`（**不**自动建：all_slots() 预枚举 41 个槽，
    读路径上顺手 mkdir 会造出一堆空年份目录，还会让 _audit 对账把它们报成缺失）。"""
    return os.path.join(DATA_DIR, slot)


def ensure_slot(slot):
    """写路径专用：确定要往这年落东西了，才把目录建出来。"""
    d = slot_dir(slot)
    if not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    return d


def material_dir(slot):
    """某年原料容器目录 `_pipeline/material/<年>`（2026-11-09 起，独立于发布目录 slot_dir）。

    发布物（part/data.js/_audit）在 `data/<年>`；原料只喂构建、不交付，故移出 data/。
    所有 material 读写都过这里，迁移只改这一处即可让采集/解析/建站/归档全链跟随。
    """
    return os.path.join(MATERIAL_ROOT, slot)


def material_path(slot, key):
    return os.path.join(material_dir(slot), key + MATERIAL_SUFFIX)


def article_dir(slot):
    """该年单篇页目录 `data/<年>/article/`。

    从站点根算三层（`data` / `<年>` / `article`），单篇页里回根用 PAGE_BASE（`../../..`）；
    不再“一篇一目录”，是为了把目录数从 7,530 压到 ≈315。
    """
    return os.path.join(slot_dir(slot), ARTICLE_SUBDIR)


def article_page(slot, key):
    return os.path.join(article_dir(slot), key + ".html")


def article_href(slot, key):
    """单篇页在站点里的链接（index.html 卡片用）：data/<年>/article/<key>.html。"""
    return "%s%s/%s/%s.html" % (SITE_PREFIX, slot, ARTICLE_SUBDIR, key)


# ---------------------------------------------------------------- 文章分片（part<i>.html）
def part_path(slot, i):
    """分片文件落点：data/<年>/part<i>.html（直接在年份目录下）。"""
    return os.path.join(slot_dir(slot), "%s%d.html" % (PART_PREFIX, i))


def part_href(slot, i):
    """分片在站点里的链接（不含 #锚点，锚点由卡片用 a.id 拼）。"""
    return "%s%s/%s%d.html" % (SITE_PREFIX, slot, PART_PREFIX, i)


def list_parts(slot):
    """该年已有分片文件名，按序号升序。"""
    d = slot_dir(slot)
    try:
        names = [n for n in os.listdir(d) if _PART_RE.match(n)]
    except OSError:
        return []
    names.sort(key=lambda n: int(_PART_RE.match(n).group(1)))
    return names


def n_parts_for(count):
    """count 篇 → 需要几个分片（至少 0；调用方保证 >0 时至少 1）。"""
    if count <= 0:
        return 0
    return (count + ARTICLE_PER_PART - 1) // ARTICLE_PER_PART


def data_js_href(slot):
    """年度 data.js 在站点里的链接（index.html 的加载器用）。"""
    return "%s%s/data.js" % (SITE_PREFIX, slot)


def data_js_path(slot):
    return os.path.join(slot_dir(slot), "data.js")


AUDIT_NAME = "_audit.json"


def audit_path(slot):
    """该年的完整性小清单 `<年>/_audit.json`（几十字节）。

    用途：拷到别处后只比 16 个小 JSON 就能判定“这年是不是少了几页”，
    不必扫全盘、不必读 7,231 个容器（见 rebuild.py --check 的 audit 行）。
    """
    return os.path.join(slot_dir(slot), AUDIT_NAME)


def read_audit(slot):
    p = audit_path(slot)
    if not os.path.exists(p):
        return None
    try:
        with io.open(p, "r", encoding="utf-8", newline="") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def year_data_count(slot):
    """该年 data.js 里的条目数（解析失败/不存在返回 None）。"""
    p = data_js_path(slot)
    if not os.path.exists(p):
        return None
    try:
        with io.open(p, "r", encoding="utf-8", newline="") as f:
            txt = f.read()
        return len(json.loads(txt[txt.index("["):txt.rindex("]") + 1]))
    except (OSError, ValueError):
        return None


def audit_mismatches():
    """逐年把 `_audit.json` 与磁盘实测对账，返回 [(年, 问题), …]（空=一致）。"""
    c_cont = counts()
    c_page = count_pages()
    bad = []
    for slot in all_slots():
        if not os.path.isdir(slot_dir(slot)):
            continue                       # 空槽位不校
        if c_cont.get(slot, 0) == 0 and c_page.get(slot, 0) == 0:
            continue                       # 新文章先落 undated、s03 解析后归位，会留下空槽：
                                           # 零内容的槽本来就没东西可验收，不算不一致
        a = read_audit(slot)
        if not a:
            bad.append((slot, "缺 " + AUDIT_NAME))
            continue
        checks = (("containers", c_cont.get(slot, 0)),
                  ("articles", c_page.get(slot, 0)),
                  ("data_js", year_data_count(slot)))
        got = {"containers": a.get("containers"), "articles": a.get("articles"),
               "data_js": a.get("items")}
        for name, now in checks:
            if now is None:
                bad.append((slot, "%s 无法解析" % name))
            elif got.get(name) != now:
                bad.append((slot, "%s 清单=%s 实际=%s" % (name, got.get(name), now)))
    return bad


def existing_slots():
    """磁盘上真实存在（且有原料容器目录）的年份槽，按年升序。

    事实源是原料根 `_pipeline/material/<年>`（2026-11-09 起原料不在 data/ 下）：
    有原料 = 这一年真的有内容，比扫发布目录 data/ 更准（data/ 可能残留未清理的空年目录）。
    """
    out = []
    if not os.path.isdir(MATERIAL_ROOT):
        return out
    for name in sorted(os.listdir(MATERIAL_ROOT)):
        if (name == UNDATED or _YEAR_RE.match(name)) and os.path.isdir(os.path.join(MATERIAL_ROOT, name)):
            out.append(name)
    return out


# ---------------------------------------------------------------- key → 槽位 索引
_INDEX = None          # {key: (slot, path)}
_INDEX_MTIME = 0.0


def _index():
    """扫一遍各年度 material 目录，建 key→(slot,path) 索引（一次 scandir/年，秒级）。"""
    global _INDEX
    if _INDEX is None:
        m = {}
        for slot in existing_slots():
            d = material_dir(slot)
            try:
                for e in os.scandir(d):
                    if e.is_file() and e.name.endswith(MATERIAL_SUFFIX):
                        m[e.name[:-len(MATERIAL_SUFFIX)]] = (slot, e.path)
            except OSError:
                continue
        _INDEX = m
    return _INDEX


def forget_index():
    """写侧新建了容器后调用，让下次读重建索引（增量一轮里也能立刻见到新文件）。"""
    global _INDEX
    _INDEX = None


# 落盘计数（给 s04d/s06 日志用：本轮真写了多少、内容未变跳过了多少）
_IO = {"written": 0, "unchanged": 0}


def io_counts():
    return dict(_IO)


def reset_io_counts():
    _IO["written"] = 0
    _IO["unchanged"] = 0


def locate(key):
    """按 key 找容器：返回 (slot, path) 或 (None, None)。"""
    return _index().get(key, (None, None))


def slot_of(url, default=None):
    """已知 URL 时先查索引拿真实年份槽，找不到再用日期推，最后落 undated。"""
    slot, _p = locate(url_key(url))
    if slot:
        return slot
    return default or UNDATED


def material_path_for(url, key=None, slot=None):
    """
    容器落点。槽位优先级：显式 slot > 索引里已有 > 由 parsed.date 推 > undated。
    写侧（s02/s03）拿到解析结果后调用时会带 date，天然归到正确年份。
    """
    k = key or url_key(url)
    if not slot:
        got, _p = locate(k)
        slot = got or UNDATED
    return slot, material_path(slot, k)


# ---------------------------------------------------------------- 容器读写
def _lines(path):
    with io.open(path, "r", encoding="utf-8", newline="") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def _dump_lines(meta, parsed, subs, raw):
    """行顺：meta → parsed → raw → sub×N。

    raw 放在子页之前，是因为只读原始 HTML 的步骤（s02b 判分页）可以读到第 3 行就停，
    不必扫完后面的子页。旧转换出的容器是 raw 在最后一行，读取端都是「见到目标段就返
    回」，两种行顺都能读（新写的容器自然变成本顺序）。
    """
    out = [json.dumps(meta, ensure_ascii=False)]
    if parsed is not None:
        out.append(json.dumps(parsed, ensure_ascii=False))
    if raw is not None:
        out.append(json.dumps({"t": "raw", "html": raw}, ensure_ascii=False))
    for s in subs or []:
        out.append(json.dumps(s, ensure_ascii=False))
    return "\n".join(out) + "\n"


def read_all(url=None, key=None):
    """一次读齐整篇容器（需要 raw 或子页的脚本用这个，避免多遍扫文件）。"""
    p = _path(url, key)
    if not p or not os.path.exists(p):
        return {"meta": None, "parsed": None, "subs": [], "raw": None, "path": None}
    meta, parsed, subs, raw = _load_parts(p)
    return {"meta": meta, "parsed": parsed, "subs": subs, "raw": raw, "path": p}


def read_meta(path):
    try:
        for seg in _lines(path):
            if seg.get("t") == "meta":
                return seg
    except (OSError, ValueError):
        return None
    return None


def read_parsed(url=None, key=None, path=None):
    """读解析产物（读到 parsed 行即停，不吞 raw）。找不到返回 None。"""
    p = path or _path(url, key)
    if not p or not os.path.exists(p):
        return None
    try:
        with io.open(p, "r", encoding="utf-8", newline="") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                seg = json.loads(line)
                t = seg.get("t")
                if t == "parsed":
                    return seg
                if t == "raw":
                    # 两种行顺下 raw 都在 parsed 之后，读到 raw 就等于没有 parsed
                    return None
    except (OSError, ValueError):
        return None
    return None


def read_raw(url=None, key=None, path=None):
    """读原始 HTML：命中 raw 行就返回（新行顺下 raw 在第 3 行，不必扫完子页）。"""
    p = path or _path(url, key)
    if not p or not os.path.exists(p):
        return None
    try:
        with io.open(p, "r", encoding="utf-8", newline="") as f:
            for line in f:
                if line.startswith('{"t": "raw') or line.startswith('{"t":"raw'):
                    return json.loads(line.strip()).get("html")
    except (OSError, ValueError):
        return None
    return None


def read_subpages(url=None, key=None, path=None):
    p = path or _path(url, key)
    if not p or not os.path.exists(p):
        return []
    try:
        return [seg for seg in _lines(p) if seg.get("t") == "sub"]
    except (OSError, ValueError):
        return []


def _path(url, key):
    if key:
        _slot, p = locate(key)
        if p:
            return p
        return material_path(UNDATED, key)
    k = url_key(url)
    _slot, p = locate(k)
    return p


def _load_parts(path):
    """整包读容器，返回 (meta, parsed, subs, raw)；文件不存在返回全 None/[]。"""
    meta = parsed = raw = None
    subs = []
    if path and os.path.exists(path):
        for seg in _lines(path):
            t = seg.get("t")
            if t == "meta":
                meta = seg
            elif t == "parsed":
                parsed = seg
            elif t == "sub":
                subs.append(seg)
            elif t == "raw":
                raw = seg.get("html")
    return meta, parsed, subs, raw


def _write_parts(slot, key, meta, parsed, subs, raw):
    """
    落盘。两条保护：
    - 内容完全相同 → **不写**（mtime 不变）。迁移工具按 mtime+size 判变化，
      这样一轮增量只会把真正改过的文件计成待传；s04d 每轮全树跑也不产垃圾。
    - 否则先写 <key>.jsonl.tmp 再 os.replace：容器一篇一个文件，中途打断不能把
      原始 HTML 和解析产物一起弄坏（这是 common.py「不用 tmp」约定的唯一例外）。
    """
    d = material_dir(slot)
    if not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    path = material_path(slot, key)
    txt = _dump_lines(meta, parsed, subs, raw)
    if os.path.exists(path):
        try:
            # newline=""：与 write_text(newline="\n") 原样落盘的行为对齐，
            # 否则 CR 会被翻译，内容相同也永远判为「变了」
            with io.open(path, "r", encoding="utf-8", newline="") as f:
                if f.read() == txt:
                    _IO["unchanged"] += 1
                    return path
        except OSError:
            pass
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(txt)
    os.replace(tmp, path)
    _IO["written"] += 1
    forget_index()
    return path


def _ensure_meta(meta, url, key, parsed):
    if meta is None:
        meta = {"t": "meta", "url": url, "key": key}
    date = (parsed or {}).get("date") or meta.get("date") or ""
    if date:
        meta["date"] = date
        meta["year"] = year_from_date(date)
    return meta


def _set_flags(meta, parsed, subs, raw):
    """把「有哪些片段、多少评论/图」记在 meta 行，让幂等判定与统计不必读完整文件。"""
    meta["has_parsed"] = bool(parsed)
    meta["has_raw"] = raw is not None
    meta["n_sub"] = len(subs or [])
    if parsed:
        meta["date"] = parsed.get("date") or meta.get("date") or ""
        meta["year"] = year_from_date(meta["date"])
        meta["n_comments"] = len(parsed.get("comments") or [])
        meta["n_images"] = len(parsed.get("images") or [])
    return meta


def stats():
    """只读 meta 行的全局统计（秒级）：容器数/有 raw/有 parsed/子页/评论/图/各年分布。"""
    out = {"containers": 0, "with_raw": 0, "with_parsed": 0, "subpages": 0,
           "comments": 0, "images": 0, "by_year": {}, "no_meta": 0}
    for _url, _key, slot, m, _p in iter_meta():
        out["containers"] += 1
        if m.get("has_raw"):
            out["with_raw"] += 1
        if m.get("has_parsed"):
            out["with_parsed"] += 1
            out["comments"] += int(m.get("n_comments") or 0)
            out["images"] += int(m.get("n_images") or 0)
        out["subpages"] += int(m.get("n_sub") or 0)
        out["by_year"][slot] = out["by_year"].get(slot, 0) + 1
    return out


def write_raw(url, text, slot=None):
    """s02 用：写原始 HTML，保持其它片段不动。"""
    key = url_key(url)
    cur_slot, path = _slot_key_path(url, key, slot)
    meta, parsed, subs, raw = _load_parts(path)
    meta = _ensure_meta(meta, url, key, parsed)
    meta["stamp"] = dict(meta.get("stamp") or {})
    meta["stamp"]["html"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _set_flags(meta, parsed, subs, text)
    return _write_parts(cur_slot, key, meta, parsed, subs, text)


def write_parsed(url, parsed, slot=None, touch=True):
    """s03/s04d 用：写/更新解析产物。返回 (年份槽, 容器路径)；年份按 parsed.date 归属（可跳槽搬家）。

    touch=False 给 s04d 这类「每轮全树回写 local」的幂等步骤用：不刷新时间戳，
    配合 _write_parts 的「内容相同不写盘」，旧容器的 mtime 本轮不动。
    """
    key = url_key(url)
    cur_slot, path = _slot_key_path(url, key, slot)
    meta, _p, subs, raw = _load_parts(path)
    meta = _ensure_meta(meta, url, key, parsed)
    slot_final = meta.get("year") or cur_slot
    if slot_final != cur_slot and os.path.exists(path):
        # 日期揭示出真实年份（少见：先按 undated 落盘，解析后才知是哪一年）→ 搬到正确槽
        subs, raw = _relocate(cur_slot, key, slot_final, subs, raw)
    if touch:
        meta["stamp"] = dict(meta.get("stamp") or {})
        meta["stamp"]["parse"] = time.strftime("%Y-%m-%d %H:%M:%S")
    # 段标记置为首字段（"t" 在第一位置），让读取端能快速识别每一行
    p = {"t": "parsed"}
    for _k, _v in (parsed or {}).items():
        if _k != "t":
            p[_k] = _v
    p.pop("html_file", None)
    _set_flags(meta, p, subs, raw)
    return slot_final, _write_parts(slot_final, key, meta, p, subs, raw)


def write_subpage(url, n, text, slot=None):
    """s02b 用：写本文第 n 页子页（按 n 覆盖）。"""
    key = url_key(url)
    cur_slot, path = _slot_key_path(url, key, slot)
    meta, parsed, subs, raw = _load_parts(path)
    meta = _ensure_meta(meta, url, key, parsed)
    slot_final = meta.get("year") or cur_slot
    subs = [s for s in subs if s.get("n") != n]
    subs.append({"t": "sub", "n": n, "html": text})
    subs.sort(key=lambda s: s.get("n") or 0)
    if slot_final != cur_slot and cur_slot != slot_final:
        subs, raw = _relocate(cur_slot, key, slot_final, subs, raw)
    _set_flags(meta, parsed, subs, raw)
    return _write_parts(slot_final, key, meta, parsed, subs, raw)


def _slot_key_path(url, key, slot):
    """定位现有容器（槽位 + 路径）；没有容器时按给定 slot（默认 undated，等解析后归位）。"""
    got, path = locate(key)
    if got:
        return got, path
    s = slot or UNDATED
    return s, material_path(s, key)


def _relocate(cur_slot, key, slot_final, subs, raw):
    """把已落在错槽的容器内容搬到正确槽，并删掉旧文件（保持一篇一文件、不重复）。"""
    old = material_path(cur_slot, key)
    if os.path.exists(old):
        new = material_path(slot_final, key)
        nd = os.path.dirname(new)
        if not os.path.isdir(nd):
            os.makedirs(nd, exist_ok=True)
        os.replace(old, new)
        forget_index()
    return subs, raw


def delete_article(url=None, key=None):
    """archive.py 移入归档用：删容器（返回删除的文件路径列表）。"""
    k = key or url_key(url or "")
    slot, path = locate(k)
    gone = []
    if path and os.path.exists(path):
        try:
            os.remove(path)
            gone.append(path)
        except OSError:
            pass
    forget_index()
    return gone


def remove_article_from_parts(slot, key):
    """分片布局下摘除某篇：从它所在 part<i>.html 里删掉那一段 <div class="shard-post" id=key>…</div>。

    锚点彼此相邻，右边界=下一个锚点或「共享脚本区」分界标记；两者都定位后再切除，
    不用做嵌套标签配对（正文里有 <section> 等，配对会误伤）。返回 (是否命中, 分片路径)。
    找不到锚点不动任何文件。
    """
    marker = SHARD_UNIT_OPEN + key + '"'
    for name in list_parts(slot):
        p = os.path.join(slot_dir(slot), name)
        try:
            with io.open(p, "r", encoding="utf-8", newline="") as f:
                txt = f.read()
        except OSError:
            continue
        start = txt.find(marker)
        if start < 0:
            continue
        nxt = txt.find(SHARD_UNIT_OPEN, start + len(marker))
        tail = txt.find(SHARD_SCRIPTS_MARK, start)
        cands = [x for x in (nxt, tail) if x >= 0]
        end = min(cands) if cands else len(txt)
        new = txt[:start] + txt[end:]
        tmp = p + ".tmp"
        try:
            with io.open(tmp, "w", encoding="utf-8", newline="") as f:
                f.write(new)
            os.replace(tmp, p)
        except OSError:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            return False, p
        return True, p
    return False, None


def find_article_file(slot, key):
    """只读定位：该篇目前落在哪个分片文件里（返回路径；找不到返回 None）。_verify 抽样用。"""
    marker = SHARD_UNIT_OPEN + key + '"'
    for name in list_parts(slot):
        p = os.path.join(slot_dir(slot), name)
        try:
            with io.open(p, "r", encoding="utf-8", newline="") as f:
                if marker in f.read():
                    return p
        except OSError:
            continue
    return None


def served_ids():
    """当前站点实际可访问的文章 id 集合（扫各年分片里的锚点）。_verify 抽样判存在用。"""
    ids = set()
    rx = re.compile(re.escape(SHARD_UNIT_OPEN) + r'([0-9a-f]{16})"')
    for slot in existing_slots():
        for name in list_parts(slot):
            try:
                with io.open(os.path.join(slot_dir(slot), name), "r",
                             encoding="utf-8", newline="") as f:
                    ids.update(rx.findall(f.read()))
            except OSError:
                continue
    return ids


def drop_parsed(url, key=None):
    """清掉容器里的 parsed 段（保留 raw 与子页），让 s03 重新解析。

    旧布局靠「删 _pipeline/parsed/<k>.json」触发重解析；年度容器不能整文件删，
    否则原始 HTML 和已抓的子页会跟着没。s02b 抓到新子页后就用它。
    """
    k = key or url_key(url)
    slot, path = locate(k)
    if not path or not os.path.exists(path):
        return False
    meta, _parsed, subs, raw = _load_parts(path)
    if meta is None:
        return False
    meta = _ensure_meta(meta, url, k, None)
    _set_flags(meta, None, subs, raw)
    _write_parts(slot, k, meta, None, subs, raw)
    return True


def total_subpages():
    """已存子页总数（只读 meta 行的 n_sub 标记）。"""
    n = 0
    for _url, _key, _slot, m, _p in iter_meta():
        v = m.get("n_sub")
        if isinstance(v, int):
            n += v
    return n


def has_parsed(url=None, key=None):
    """廉价存在性判定：优先看 meta 行的标记，无标记（s07 转出的旧容器）才回退真读。"""
    m = _meta_for(url, key)
    if m is not None and "has_parsed" in m:
        return bool(m["has_parsed"])
    return read_parsed(url=url, key=key) is not None


def has_raw(url=None, key=None):
    m = _meta_for(url, key)
    if m is not None and "has_raw" in m:
        return bool(m["has_raw"])
    return read_raw(url=url, key=key) is not None


def _meta_for(url, key):
    p = _path(url, key)
    if not p or not os.path.exists(p):
        return None
    return read_meta(p)


def paths():
    """所有容器**路径**（按 key 排序）。

    以前这里写的是 `for _slot, p in sorted(_index().items())`：items() 的元素是
    (key, (slot, path))，解包后 p 实际拿到的是 (slot, path) 元组 —— 当时没有调用方，
    所以这个坑一直没响。现在 update.py 的并行差集靠它，必须给真的路径串。
    """
    return [v[1] for _k, v in sorted(_index().items())]


def iter_parsed(slots=None, keys=None):
    """逐篇产出 (url, key, slot, parsed)。只读到 parsed 行，不吞 raw。

    slots / keys 是**读盘之前**的过滤（前者按年份目录、后者按容器文件名=16 位哈希），
    用来把「全树扫」收敛成「本轮范围」；都不给就是全树（历史行为不变）。
    keys 能在 open() 之前就把不相关的容器跳过，所以范围模式真的不用打开另外七千篇
    （s04d/s04i/s04f 全树跑一次要 30~165 秒，全花在这七千次 open + json.loads 上）。
    """
    want_slots = set(slots) if slots else None
    want_keys = set(keys) if keys else None
    for slot in existing_slots():
        if want_slots is not None and slot not in want_slots:
            continue
        d = material_dir(slot)
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for name in names:
            if not name.endswith(MATERIAL_SUFFIX):
                continue
            key = name[:-len(MATERIAL_SUFFIX)]
            if want_keys is not None and key not in want_keys:
                continue
            p = os.path.join(d, name)
            art = read_parsed(path=p)
            if art and art.get("url"):
                yield art["url"], key, slot, art


def iter_meta():
    """轻量遍历：只读 meta 行（统计/对账用，不解析 parsed/raw）。"""
    for slot in existing_slots():
        d = material_dir(slot)
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for name in names:
            if not name.endswith(MATERIAL_SUFFIX):
                continue
            p = os.path.join(d, name)
            m = read_meta(p)
            if m:
                yield m.get("url"), name[:-len(MATERIAL_SUFFIX)], slot, m, p


def counts():
    """年度计数：{slot: 容器数}，用于体检与校验。"""
    c = {}
    for slot in existing_slots():
        d = material_dir(slot)
        try:
            c[slot] = len([n for n in os.listdir(d) if n.endswith(MATERIAL_SUFFIX)])
        except OSError:
            c[slot] = 0
    return c


_ARTICLE_ANCHOR_RE = re.compile(r'<article[^>]*class="[^"]*\bpost\b')


def count_pages():
    """各年文章数（分片布局：数 part<i>.html 里的 <article class="post"> 锚点）。

    口径仍是「可访问的文章篇数」，与 _audit.articles 对账一致；只扫各年分片（每年几片），
    秒级。旧的 listdir `<年>/article/` 数文件已随一篇一文件布局退役。
    """
    out = {}
    for slot in existing_slots():
        n = 0
        for name in list_parts(slot):
            try:
                with io.open(os.path.join(slot_dir(slot), name), "r",
                             encoding="utf-8", newline="") as f:
                    n += len(_ARTICLE_ANCHOR_RE.findall(f.read()))
            except OSError:
                continue
        if n:
            out[slot] = n
    return out


def pages_total():
    return sum(count_pages().values())


def year_data_files():
    """已生成的年度 data.js 路径列表。"""
    return [data_js_path(s) for s in existing_slots()
            if os.path.exists(data_js_path(s))]


def parse_years_arg(text, default_current=True):
    """
    解析 --years：
      省略              → 当前年
      2026              → ['2026']
      2024,2026,2027    → 去重升序
      2024-2026         → 区间
      all               → 全部槽位（含 undated）
    返回槽位字符串列表。
    """
    if not text:
        return [time.strftime("%Y")] if default_current else all_slots()
    t = str(text).strip().lower()
    if t in ("all", "*"):
        return all_slots()
    out = set()
    for part in t.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            if a.isdigit() and b.isdigit():
                for y in range(int(a), int(b) + 1):
                    out.add(str(y))
                continue
        if part.isdigit():
            out.add(part)
    return sorted(out) or [time.strftime("%Y")]


def in_scope(slot, years):
    """槽位是否在给定年份集合内；years 含 'all'/全量槽位时恒真。"""
    if not years:
        return True
    return slot in years or UNDATED in years and slot == UNDATED
