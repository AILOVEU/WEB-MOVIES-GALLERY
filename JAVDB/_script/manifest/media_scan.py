# -*- coding: utf-8 -*-
"""本地视频目录 -> movies.js 路径字段 的同步器（让页面能直接点开播放）。

支持的本地视频源（`--source` 选一或 `all`）：
  talk   默认  目录 `assets/水果派/`   字段 `talk`  + `talksize`   —— 影片**解说**（人工放入，按番号命名）
  video        目录 `assets/videos/`   字段 `video` + `videosize`  —— JavDB **预告片**（trailers_download.py 下载）

为什么需要它：movies.js 里的 `video`/`talk` 字段是**前端能不能直接播**的依据。
★★ 核心铁律（和用户定下的 videos_manifest 规则一致）：记录一旦写进去就**永不删除** ★★
  - 目录里有文件 + 番号对得上 movies.js -> 写/更新 `字段 = 目录/文件名`（+ 体积字段）；
  - 目录里文件没了（被手动搬到移动硬盘/别的磁盘）-> **记录照旧保留**，只统计“已移走”，
    并从 videos_manifest.json 回填体积；老记录没存过体积的也能补上。
    页面上碰到这种记录会显示“本地文件读不到（可能已被搬走）”，而不是错删一条档案。
    确需人工剔除（正常流程永不用）：`scan --purge-dead`。
  - 文件在、但番号在 movies.js 里找不到 -> 不改数据，记进data/ `media_unmatched.json`
    （多半是文件名不规范或该影片还没抓过详情）。

★★ 与 videos_manifest.json 的分工 ★★
  两边都是“记录永久保留”，但用途不同：
  `videos_manifest.json` 管「**要不要重新下载**」（有记录 = 已下，文件搬走也不重下，见 RESUME 5.5）；
  `movies.js` 的 `video`/`talk` 管「**曾经有、叫什么路径、多大**」（播得了就播，播不了就当档案）。

番号解析（沿用本项目命名规则 `{CODE}.mp4`）：整名精确 -> 正则候选 -> 已知番号子串（取最长），
一律以 movies.js 的 key（规范化大写番号）校验，所以带说明的文件名（如
`水果派/MFYD-063 完整解说 1080P.mp4`）也能对上；同一番号有多份文件时保留体积最大的一份。

安全：只读视频目录，只写 movies.js / media_unmatched.json；绝不删除或移动目录里的任何文件。
写盘直接写目标文件（本机沙盒会把 tmp+replace 当危险操作中断进程，见 RESUME 第 5 节坑 1）。
movies.js 首次修改前备份到 `_backup/movies_before_media_fields.js`。

用法（选项可以写在任何位置）：
  python media_scan.py                        # = scan（两个源一起对账，最常用：只增不删）
  python media_scan.py scan [-v] [--source talk|video|all]
  python media_scan.py scan --dir 别的目录 --field talk --size-field talksize   # 自定义源
  python media_scan.py scan --purge-dead      # ★危险★ 把文件不在的记录剔掉（仅人工确认时用）
  python media_scan.py restore --from _backup/<日期>/movies_before_media_fields.js   # 误删后从备份灌回（只补不覆盖）
  python media_scan.py stats                  # 各源：目录文件数 / 记录总数 / 在场 / 已移走
  python media_scan.py check <code> [--source talk|video]   # 某番号有无解说或预告（rc=0=有）
  python media_scan.py list [--source talk] [--newest]      # 导出挂了某字段的番号（含已移走的）
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
import argparse
import datetime
import io
import json
import os
import re
import shutil
import sys
import win_utf8        # noqa: F401  重定向/管道下固定 UTF-8，否则 ✅ 会砸死进程

DATA = os.path.join(ROOT, "data")                        # 站点数据与名单
SITE = os.path.join(DATA, "site")    # 页面要读的 *.js（HTML 写死引用）
STATE = os.path.join(DATA, "state")  # 抓取状态：清单 / 终态名单 / 校验基准
ASSETS = os.path.join(ROOT, "assets")                    # 资源目录
WB = os.path.join(ROOT, "_pipeline")     # 过程中文件：断点 / cookie / 临时切片（删了会丢进度）
LOGS = os.path.join(ROOT, "_logs")      # 日志与验证截图：随时可删，不影响任何数据
MOVIES_JS = os.path.join(SITE, "movies.js")
UNMATCHED = os.path.join(STATE, "media_unmatched.json")
BACKUP = os.path.join(ROOT, "_backup", datetime.date.today().isoformat(), "movies_before_media_fields.js")  # 备份进当日日期子目录
VAR = "window.JAVDB_MOVIES = "

# 本地视频源定义（新增源只要在这里加一行；manifest 可为空，用于给已移走的记录回填体积）
SOURCES = {
    "talk":  {"dir": "assets/水果派", "field": "talk",  "size_field": "talksize",  "label": "解说",   "manifest": ""},
    "video": {"dir": "assets/videos", "field": "video", "size_field": "videosize", "label": "预告片", "manifest": "videos_manifest.json"},
}
DEFAULT_SOURCE = "talk"

VIDEO_EXT = {".mp4", ".m4v", ".mkv", ".webm", ".avi", ".mov", ".ts", ".flv", ".wmv", ".mpg", ".mpeg"}
SKIP_SUFFIX = (".part", ".tmp", ".downloading", ".crdownload")
MIN_OK = 10000          # 小于此体积视为拷贝/下载残留，仍登记但给 note 提示

# 番号候选：按本项目实际出现过的格式（后面一定用 movies.js 的 key 校验，误命中不会写坏数据）
CODE_PATTERNS = [
    re.compile(r"FC2[-_]?PPV[-_]?\d+"),
    re.compile(r"\d{6}_\d{3}"),                 # 010516_222
    re.compile(r"[A-Z]{2,10}-\d{2,6}[A-Z]?"),   # MFYD-063 / IPX-118 / HEYZO-0783
    re.compile(r"\d{4,6}-\d{3,4}"),             # 121914-760
    re.compile(r"[A-Z]{3,10}\d{3,5}"),          # RED058
]


def log(*a):
    print(*a, flush=True)


def norm_code(s):
    """规范化番号：压缩空白 + 大写（与 movies.js 的 key 规范一致）。"""
    return re.sub(r"\s+", " ", (s or "").strip()).upper()


def norm_search(s):
    """文件名匹配用的激进规范化：分隔符统一成 -，去掉标点。"""
    return re.sub(r"[\s_]+", "-", re.sub(r"[^\w]+", " ", s or "").strip()).strip().upper()


# ---------------- movies.js ----------------
def load_movies():
    t = io.open(MOVIES_JS, encoding="utf-8").read()
    i = t.index(VAR) + len(VAR)
    d, _ = json.JSONDecoder().raw_decode(t[i:].lstrip())
    return d


def save_movies(d):
    os.makedirs(os.path.dirname(BACKUP), exist_ok=True)
    if not os.path.exists(BACKUP):
        shutil.copyfile(MOVIES_JS, BACKUP)
        log("[backup] ->", BACKUP)
    with io.open(MOVIES_JS, "w", encoding="utf-8") as f:
        f.write("/* JavDB 影片信息合集（自动生成，勿手改） */\n")
        f.write(VAR)
        f.write(json.dumps(d, ensure_ascii=False, separators=(",", ":")))
        f.write(";\n")
    bump_site_refs()      # 数据一变就刷页面版本号，否则同一天内浏览器一直吃旧缓存


def bump_site_refs():
    """movies.js 一落盘就刷 HTML 里的 `?v=<YYYYMMDD>`（版本号只有 build_collection_js 一个定义处）。

    为什么必须：2026-11-05 凌晨补完解说后，`talk` 已写进 movies.js，但页面引用还是当天
    已被反复访问过的 `?v=20261104` —— 浏览器直接用磁盘缓存，新字段根本看不到。
    刷失败不影响数据（文件已写成功），只提醒。
    """
    try:
        import build_collection_js as bcj
        ver, touched = bcj.bump_refs()
        log("[refs] -> ?v=%s（改动文件：%s）" % (ver, ", ".join(touched) or "无，已是当日版本"))
    except Exception as e:
        log("[refs] 版本号刷新失败（数据已写好，不影响）：%s: %s" % (type(e).__name__, str(e)[:80]))


# ---------------- 番号解析 ----------------
def resolve(stem, keys, long_keys):
    """文件名（去扩展名）-> movies.js 的 key；解不到返回 None。"""
    for cand in (norm_code(stem), norm_search(stem)):
        if cand in keys:
            return cand
    s = norm_search(stem)
    cands = []
    for pat in CODE_PATTERNS:
        for m in pat.findall(s):
            c = norm_code(m)
            for v in (c, c.replace("-", "_"), c.replace("_", "-")):
                if v not in cands:
                    cands.append(v)
    for c in cands:
        if c in keys:
            return c
    # 兜底：文件名里包含某个已知番号（长名/带说明的文件名），取最长匹配
    best = None
    for k in long_keys:
        if k in s and (best is None or len(k) > len(best)):
            best = k
    return best


def is_video_file(name):
    n = name.lower()
    if n.endswith(SKIP_SUFFIX) or n.startswith("."):
        return False
    return os.path.splitext(n)[1] in VIDEO_EXT


def ts_of(path):
    return datetime.datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M:%S")


def find_entry(mv, code):
    """按规范化番号找 movies.js 条目，返回 (key, entry)；找不到 (None, None)。"""
    if code in mv:
        return code, mv[code]
    n = norm_code(code)
    for k in mv:
        if norm_code(k) == n:
            return k, mv[k]
    return None, None


def path_exists(p):
    return bool(p) and os.path.exists(p.replace("/", os.sep))


def load_manifest_sizes(fname):
    """从已下载清单（videos_manifest.json）取 code/文件名 -> 体积，用于回填已移走的记录。"""
    p = os.path.join(STATE, fname) if fname else ""
    if not p or not os.path.exists(p):
        return {}
    try:
        items = json.load(io.open(p, encoding="utf-8"))["items"]
    except Exception:
        return {}
    out = {}
    for fn, rec in items.items():
        sz = int(rec.get("size") or 0)
        if sz <= 0:
            continue
        out[norm_code(os.path.splitext(fn)[0])] = sz
        out[norm_code(rec.get("code") or "")] = sz
    out.pop("", None)
    return out


def size_of_record(key, path, sizes):
    """从档案体积表里查一条记录的字节数（先按文件名 stem，再按 key）。"""
    stem = norm_code(os.path.basename(path or "").rsplit(".", 1)[0])
    return int((sizes or {}).get(stem) or (sizes or {}).get(norm_code(key)) or 0)


# ---------------- media_unmatched.json ----------------
def load_unmatched():
    if os.path.exists(UNMATCHED):
        try:
            u = json.load(io.open(UNMATCHED, encoding="utf-8"))
        except Exception:
            u = {}
    else:
        u = {}
    u.setdefault("version", 1)
    u.setdefault("desc", "本地视频目录里无法对应到 movies.js 番号的文件（前端播放不了，需改名或补抓影片）")
    u.setdefault("items", [])
    return u


def save_unmatched(u):
    u["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    u["total"] = len(u["items"])
    with io.open(UNMATCHED, "w", encoding="utf-8") as f:
        json.dump(u, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


# ---------------- 单个源的对账 ----------------
def sync_one(mv, src, keys, long_keys, verbose, purge=False):
    field, size_field, label = src["field"], src["size_field"], src["label"]
    dname = src["dir"]
    talk_dir = os.path.join(ROOT, dname)
    if not os.path.isdir(talk_dir):
        log(f"[{label}] 目录不存在，跳过：{talk_dir}（已存记录不受影响）")
        return {"label": label, "dir": dname, "field": field, "skip": True,
                "files": 0, "mounted": 0, "added": 0, "updated": 0, "unchanged": 0,
                "gone": 0, "filled": 0, "cleared": 0, "unmatched": [], "small": [], "skipped_names": []}
    sizes = load_manifest_sizes(src.get("manifest", ""))

    prefix = dname.replace("\\", "/").rstrip("/") + "/"
    names = sorted(n for n in os.listdir(talk_dir) if os.path.isfile(os.path.join(talk_dir, n)))
    files = [n for n in names if is_video_file(n)]
    skipped = [n for n in names if n not in files]

    # 1) 解析文件 -> 番号；同番号多份留体积最大的
    by_code, unmatched, small = {}, [], []
    for n in files:
        p = os.path.join(talk_dir, n)
        size = os.path.getsize(p)
        code = resolve(os.path.splitext(n)[0], keys, long_keys)
        if verbose:
            log(f"  {n}  ->  {code or '(未匹配)'}  {size/1048576:.1f}MB")
        if size < MIN_OK:
            small.append(f"{n}（{size}B，疑似残留）")
        if not code:
            unmatched.append({"field": field, "dir": dname, "file": n, "size": size,
                              "mtime": ts_of(p), "reason": "番号未在 movies.js 中找到，检查文件名或先补抓该影片"})
            continue
        cur = by_code.get(code)
        if cur is None or size > cur["size"]:
            if cur:
                unmatched.append({"field": field, "dir": dname, "file": cur["file"], "size": cur["size"],
                                  "mtime": cur["mtime"],
                                  "reason": f"同番号 {code} 有多份文件，已保留体积更大的一份"})
            by_code[code] = {"file": n, "size": size, "mtime": ts_of(p)}

    # 2) 回写字段（文件在场就刷新）
    added = updated = unchanged = 0
    for code, info in by_code.items():
        path = prefix + info["file"]
        key, e = find_entry(mv, code)
        if e is None:
            continue
        if e.get(field) == path and int(e.get(size_field) or 0) == info["size"]:
            unchanged += 1
            continue
        was = bool(e.get(field))
        e[field] = path
        if size_field:
            e[size_field] = info["size"]
        updated += 1 if was else 0
        added += 0 if was else 1

    # 3) ★记录永久保留★：目录里文件没了（被手动搬到移动硬盘/别的磁盘）**绝不删字段**，
    #    跟 videos_manifest 一条规矩：记录在就算有过，不因为“现在盘上没”而丢档案。
    #    只统计“已移走”，并从 videos_manifest.json 回填体积（早期记录没存过 size_field）。
    #    确实要人工剔除（正常流程永不用）才加 --purge-dead。
    live = {prefix + v["file"] for v in by_code.values()}
    gone = filled = cleared = 0
    for k, e in mv.items():
        v = e.get(field)
        if not v or not v.startswith(prefix) or v in live or path_exists(v):
            continue
        gone += 1
        if size_field and not int(e.get(size_field) or 0):
            sz = size_of_record(k, v, sizes)
            if sz:
                e[size_field] = sz
                filled += 1
        if purge:
            e.pop(field, None)
            if size_field:
                e.pop(size_field, None)
            cleared += 1

    return {"label": label, "dir": dname, "field": field, "skip": False,
            "files": len(files), "mounted": len(by_code), "added": added, "updated": updated,
            "unchanged": unchanged, "gone": gone, "filled": filled, "cleared": cleared,
            "unmatched": unmatched, "small": small, "skipped_names": skipped}


# ---------------- 子命令 ----------------
def pick_sources(args):
    d, f = getattr(args, "dir", None) or "", getattr(args, "field", None) or ""
    if d and f:
        sf = getattr(args, "size_field", None)
        return [{"dir": d, "field": f,
                 "size_field": sf if sf is not None else f + "size",
                 "label": (getattr(args, "label", None) or f)}]
    # 主解析器会把 --source 默认写进 namespace（可能是 None），子命令缺省值不可靠，这里统一定默认
    if resolve_source(args, "all") == "all":
        return [SOURCES[k] for k in ("talk", "video")]
    return [SOURCES[resolve_source(args, "all")]]


def resolve_source(args, default):
    """取子命令的源：子命令显式传的 > 顶层传的 > 该命令的默认值。"""
    return getattr(args, "source", None) or default


def cmd_scan(args):
    mv = load_movies()
    keys = {norm_code(k) for k in mv}
    long_keys = sorted((k for k in keys if len(k) >= 6), key=len, reverse=True)
    srcs = pick_sources(args)

    results = []
    for src in srcs:
        r = sync_one(mv, src, keys, long_keys, args.verbose, getattr(args, "purge_dead", False))
        results.append((src, r))
        if r["skip"]:
            continue
        total = sum(1 for e in mv.values() if e.get(src["field"]))
        log(f"[scan:{src['field']}] {src['dir']}/ 在场视频 {r['files']} 个 | 挂上番号 {r['mounted']}"
            f"（新增 {r['added']}，变更 {r['updated']}，未变 {r['unchanged']}） | "
            f"文件已移走但记录保留 {r['gone']}（体积回填 {r['filled']}）| 未匹配 {len(r['unmatched'])}"
            f" | {src['field']} 记录总数 {total}")
        if args.purge_dead:
            log(f"  ‼ --purge-dead 已剔除 {r['cleared']} 条记录（正常流程不该用）")
        for n in r["skipped_names"][:5]:
            log(f"  · 非视频文件（忽略）：{n}")
        for s in r["small"]:
            log(f"  ⚠ 体积异常小：{s}")
        for u in r["unmatched"][:10]:
            log(f"  ✗ 未匹配 {u['file']}：{u['reason']}")
        if len(r["unmatched"]) > 10:
            log(f"  ... 其余 {len(r['unmatched']) - 10} 条见 media_unmatched.json")

    # 汇总未匹配：只替换本次扫描的字段，其它字段已有的记录原样保留
    un = load_unmatched()
    fields = {src["field"] for src, r in results if not r["skip"]}
    items = [x for x in un["items"] if x.get("field") not in fields]
    for src, r in results:
        if not r["skip"]:
            items.extend(r["unmatched"])
    un["items"] = sorted(items, key=lambda x: (x.get("field", ""), x.get("file", "")))
    save_unmatched(un)

    save_movies(mv)
    log(f"movies.js 已写入（共 {len(mv)} 部）| 未匹配总计 {un['total']} 条")
    return 0


def cmd_stats(args):
    mv = load_movies()
    for k, src in SOURCES.items():
        field, size_field = src["field"], src["size_field"]
        recs = {kk: e for kk, e in mv.items() if e.get(field)}
        present = sum(1 for e in recs.values() if path_exists(e[field]))
        size = sum(int(e.get(size_field) or 0) for e in recs.values())
        nosize = sum(1 for e in recs.values() if not int(e.get(size_field) or 0))
        d = os.path.join(ROOT, src["dir"])
        nf = len([x for x in os.listdir(d) if os.path.isfile(os.path.join(d, x)) and is_video_file(x)]) \
            if os.path.isdir(d) else -1
        log(f"{src['label']:4s}({src['field']:5s}): 记录 {len(recs)} 部（在场 {present} / 已移走仍保留 "
            f"{len(recs) - present}）| 目录 {src['dir']}/ 现存文件 "
            f"{nf if nf >= 0 else '(目录不存在)'} 个 | 体积合计 {size/1073741824:.2f} GB"
            + (f" | 无体积信息的记录 {nosize} 条" if nosize else ""))
    if os.path.exists(UNMATCHED):
        u = json.load(io.open(UNMATCHED, encoding="utf-8"))
        log(f"未匹配文件  : {u.get('total', 0)} 条（media_unmatched.json），最后更新 {u.get('updated_at') or '-'}")
        for it in u.get("items", [])[:5]:
            log(f"  · [{it.get('field')}] {it.get('file')}：{it.get('reason')}")
    log(f"movies.js   : {len(mv)} 部影片")
    return 0


def cmd_restore(args):
    """从旧备份（movies.js 格式的任何 .js）把被误删的播放字段灌回来：只补不覆盖，绝不删东西。"""
    src_path = getattr(args, "from_backup", "") or ""
    if not src_path or not os.path.exists(src_path):
        log("用法：media_scan.py restore --from _backup/<日期>/movies_before_media_fields.js（备份按日期存子目录，先 `ls _backup/*/` 看）")
        return 2
    raw = io.open(src_path, encoding="utf-8").read()
    i = raw.index(VAR) + len(VAR)
    old, _ = json.JSONDecoder().raw_decode(raw[i:].lstrip())
    mv = load_movies()
    log(f"[restore] 从 {os.path.relpath(src_path, ROOT)}（{len(old)} 条）往 movies.js（{len(mv)} 条）只补不覆盖")
    filled_total = 0
    for src in SOURCES.values():
        field, size_field = src["field"], src["size_field"]
        sizes = load_manifest_sizes(src.get("manifest", ""))
        n_fill = n_nosize = 0
        for k, e in old.items():
            v = e.get(field)
            if not v:
                continue
            key, cur = find_entry(mv, k)
            if cur is None or cur.get(field):      # 条目没了或已有记录 -> 不动
                continue
            cur[field] = v
            if size_field:
                sz = int(e.get(size_field) or 0) or size_of_record(k, v, sizes)
                if sz:
                    cur[size_field] = sz
                else:
                    n_nosize += 1
            n_fill += 1
        filled_total += n_fill
        log(f"  [{field}] 补回 {n_fill} 条（其中 {n_nosize} 条查不到体积）"
            f" | 现记录总数 {sum(1 for x in mv.values() if x.get(field))}")
    if not filled_total:
        log("没有需要补的记录（字段都在）")
        return 0
    save_movies(mv)
    log(f"movies.js 已写入（共 {len(mv)} 部）")
    return 0


def cmd_check(args):
    mv = load_movies()
    src = SOURCES[resolve_source(args, "talk")]
    if not args.code:
        log("用法：media_scan.py check <code>")
        return 2
    for key in mv:
        if norm_code(key) == norm_code(args.code) or key == args.code:
            v = mv[key].get(src["field"])
            if v:
                sz = int(mv[key].get(src["size_field"]) or 0)
                state = "在场" if path_exists(v) else "已移走（记录保留）"
                log(f"HAS-{src['field'].upper()}  {key}  ->  {v}" + (f"  ({sz/1048576:.1f}MB)" if sz else "")
                    + f"  [{state}]")
                return 0
    log(f"NO-{src['field'].upper()}   {args.code}（{src['label']}）")
    return 1


def cmd_list(args):
    mv = load_movies()
    src = SOURCES[resolve_source(args, "talk")]
    field = src["field"]
    hit = [k for k in mv if mv[k].get(field)]
    if args.newest:
        hit.sort(key=lambda k: mv[k].get("date") or "", reverse=True)
    else:
        hit.sort()
    gone = 0
    for k in hit:
        on = path_exists(mv[k][field])
        gone += 0 if on else 1
        if args.verbose:
            log(f"{k}\t{mv[k][field]}" + ("" if on else "\t# 已移走（记录保留）"))
        else:
            log(k)
    log(f"# 共 {len(hit)} 条 {field} 记录（在场 {len(hit) - gone} / 已移走但保留 {gone}）")
    return 0


def main():
    # 故意不用 subparsers：argparse 的子解析器会用子命令的默认值覆盖主解析器已解析的选项
    # （导致 `--source video scan` 这种写法默默失效），改成位置参数 + 单一解析器。
    ap = argparse.ArgumentParser(description="本地视频目录 -> movies.js 播放字段同步器（记录只增不删）")
    ap.add_argument("cmd", nargs="?", default="scan", choices=["scan", "stats", "check", "list", "restore"],
                    help="子命令，缺省 scan")
    ap.add_argument("code", nargs="?", default="", help="check 用的番号")
    ap.add_argument("--source", default=None, choices=["talk", "video", "all"],
                    help="处理哪个本地视频源（scan 默认 all，check/list 默认 talk）")
    ap.add_argument("--dir", default="", help="自定义目录名（与 --field 一起用）")
    ap.add_argument("--field", default="", help="自定义字段名")
    ap.add_argument("--size-field", default=None, help="体积字段名（配合 --field；留空自动 = 字段名+size）")
    ap.add_argument("--label", default="", help="自定义显示名")
    ap.add_argument("--from", dest="from_backup", default="",
                    help="restore：旧 movies.js 备份路径，只把缺的播放字段灌回来（不覆盖 existing）")
    ap.add_argument("--purge-dead", dest="purge_dead", action="store_true",
                    help="★危险★ scan：把文件不在的记录剔掉（正常流程永不用，记录本该永久保留）")
    ap.add_argument("--newest", action="store_true", help="list：按上映日期倒序")
    ap.add_argument("-v", "--verbose", action="store_true", help="scan 逐文件打印解析结果；list 同时输出路径与已移走标记")
    args = ap.parse_args()
    if args.cmd == "check" and not args.code:
        ap.error("check 需要给一个番号：media_scan.py check <code>")
    if args.cmd == "restore" and not args.from_backup:
        ap.error("restore 需要 --from <旧 movies.js 备份>")
    return {"scan": cmd_scan, "stats": cmd_stats, "check": cmd_check,
            "list": cmd_list, "restore": cmd_restore}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
