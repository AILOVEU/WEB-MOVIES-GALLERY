# -*- coding: utf-8 -*-
"""covers/ 封面「已下载清单」维护工具 —— 与 videos_manifest.py 同一套规矩。

清单真相来源：`data/state/covers_manifest.json`（唯一被读取的文件；不再产出 .md 副本）。

★ 为什么需要它 ★
以往「封面下没下过」是**看 covers/ 目录里有没有这个文件**（还按文件头验真）。
一旦把 covers/ 整体搬去别的硬盘 / 别处建站，目录空了 → 所有老封面都会被当成
「没下过」重新下载一遍（约 1 万张，纯浪费）。所以把判定从「目录实况」改成「清单记录」。

★★ 核心判定规则（所有下载/同步脚本都必须遵守）★★
  判定以「清单里有没有这条 valid 记录」为准，**不**以「covers/ 里有没有这个文件」为准：
  - 清单里有记录且 valid=true = 已下载 = 绝不再下载；
  - 即使 covers/ 为空 / 文件不在，也算已下载（文件多半被手动搬走 / 已拷去别处建站）；
  - 只有 valid=false（登记时文件不是真图片 = CF 拦截页/残缺）才允许重下。
  另外保留「目录里确实有真图片」这条判定（并顺手登记进清单），所以搬回来/新拷入都不会漏。

核心规则（勿违背）：
  1. covers/ 里出现的新图片  -> 追加记录（scan）；
  2. 封面被手动移走          -> 记录永久保留，只把 present 置 false，**绝不因文件不在而删记录**；
  3. 增量下载前先比对清单    -> 有 valid 记录就不重下；
  4. 只有人工确认误登记/要重下，才用 drop <code> --yes 剔记录。
本脚本只写清单文件，绝不删除/移动 covers/ 里的任何内容。
所有写盘直接写目标文件（本机沙盒会把 tmp+replace 当危险操作中断进程，见 RESUME 第 5 节坑 1）。

用法：
  python covers_manifest.py scan                      # 扫 covers/，补新记录、更新在场状态（搬走前必跑一次）
  python covers_manifest.py check <code>...            # 是否已下载（exit 0=全部已下载）
  python covers_manifest.py missing <候选清单文件>      # 只输出未下载的 code（供下载器消费）
  python covers_manifest.py mark <code> [--note 说明]   # 手动登记（文件不在也算已下载）
  python covers_manifest.py drop <code> --yes           # 仅清理误登记（正常流程永不用）
  python covers_manifest.py stats                      # 统计
  python covers_manifest.py list [--present|--gone|--since 2026-11-02]
        # --present = 当前还在 covers/ 里的（= 该搬走的「新封面」）；--gone = 已搬走的
  python covers_manifest.py report                     # 把可读视图打印到 stdout（要留档就 > _logs/xxx.txt）

作为模块被别的脚本使用（covers_check.py / users_spider.py / merge_collection.py 都这么做）：
  import covers_manifest as cm
  cm.have(code)              # 目录有真图片 或 清单有 valid 记录  -> True（=不重下）
  cm.mark_downloaded(code)   # 下载成功后登记
  cm.flush()                 # 把内存里的变更落盘（大批量下载中途/结束时都要调）
  JDB_COVERS_DIR=<路径>      # 临时换封面目录（用于「目录已搬走」的演练与校验）
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
import atexit
import datetime
import io
import json
import os
import sys
import threading
import time

DATA = os.path.join(ROOT, "data")                        # 站点数据与名单
SITE = os.path.join(DATA, "site")    # 页面要读的 *.js（HTML 写死引用）
STATE = os.path.join(DATA, "state")  # 抓取状态：清单 / 终态名单 / 校验基准
ASSETS = os.path.join(ROOT, "assets")                        # 资源目录（covers / videos / 水果派）
WB = os.path.join(ROOT, "_pipeline")     # 过程中文件：断点 / cookie / 临时切片（删了会丢进度）
LOGS = os.path.join(ROOT, "_logs")      # 日志与验证截图：随时可删，不影响任何数据
COVERS = os.environ.get("JDB_COVERS_DIR") or os.path.join(ASSETS, "covers")
MANIFEST = os.path.join(STATE, "covers_manifest.json")
COVERS_N = int(os.environ.get("JDB_COVERS_N") or 64)   # 封面分桶数（默认 64）


def cover_bucket(code):
    """与磁力/前端同一规则：大写只留 [A-Z0-9]，逐字符 h=(h*31+ord)&0xffffffff，%N。
    封面的实际目录不靠前端算（<img> 用 movies.js 存的 local 路径），但所有脚本必须用同一个值。"""
    s = "".join(ch for ch in str(code).upper() if ("A" <= ch <= "Z") or ("0" <= ch <= "9"))
    h = 0
    for ch in s:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return str(h % COVERS_N)


def cover_dir(code, base=None):
    return os.path.join(base or COVERS, cover_bucket(code))

# movies.js 里封面字段（local）的相对路径前缀；旧数据可能还没带 assets/，两个都认
LOCAL_PREFIXES = ("assets/covers", "covers")

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
SKIP_SUFFIX = (".part", ".tmp", ".downloading", ".crdownload")
MIN_BYTES = 1024            # 小于 1KB 视为残缺（真封面不会这么小）
MAGIC = [(b"\xff\xd8\xff", "jpg"), (b"\x89PNG", "png"), (b"GIF8", "gif")]

NOW = lambda: datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
DAY = lambda: datetime.datetime.now().strftime("%Y-%m-%d")

# Windows 下 stdout 被管道/重定向时默认用 GBK（✅/⚠ 会砸进程），提前关管道还会抛 Errno 22；
# 两者都由 win_utf8 统一处理（import 本模块的 covers_check / merge_collection / users_spider 一并受保护）
import win_utf8          # noqa: F401


def log(*a):
    print(*a, flush=True)


# ---------------- 真伪判定（唯一真相 = 文件内容，不看体积）----------------
def local_field(name):
    """写进 movies.js 的 local 字段值（前端直接当相对 URL 用）；带分桶子目录。"""
    base = os.path.basename(name)
    stem = os.path.splitext(base)[0]
    return "assets/covers/" + cover_bucket(stem) + "/" + base


def local_to_path(rel):
    """把 movies.js 的 local 字段解析成实际路径。
    兼容 `assets/covers/X.jpg`（新布局）与 `covers/X.jpg`（旧数据）；
    目录被 JDB_COVERS_DIR 覆盖时（演练“已搬走”）也走覆盖后的路径。"""
    if not rel:
        return ""
    p = rel.replace("\\", "/")
    for pre in LOCAL_PREFIXES:
        if p.startswith(pre + "/"):
            return os.path.join(COVERS, *p[len(pre) + 1:].split("/"))
    return os.path.join(ROOT, *p.split("/"))


def is_valid_image(path):
    """按文件头判真伪：确实是图片且体积正常才算有效。
    （CF 拦截页有 6~9KB，用体积判会被当「下载成功」——老坑，见 RESUME 第 5 节坑 9。）"""
    try:
        if not path or not os.path.exists(path):
            return False
        if os.path.getsize(path) < MIN_BYTES:
            return False
        with open(path, "rb") as f:
            head = f.read(32)
        for mg, _ in MAGIC:
            if head.startswith(mg):
                return True
        if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
            return True
        low = head.lower()
        if low.startswith(b"<!doctype") or low.startswith(b"<html") or low.startswith(b"<?xml"):
            return False
        return False
    except OSError:
        return False


def code_of(name):
    """文件名/番号 -> 清单主键（大写番号，去掉图片扩展名与下载残留后缀）。
    ★ 只剥「已知的图片扩展名」，不能拿 splitext 一刀切：
      像 `BLACKED.15.12.jpg` 这种番号本身就带点，splitext 会错切成 `BLACKED.15`，
      而且这种切法不幂等（对已经算出来的 code 再调一次会越切越短）。"""
    s = os.path.basename(name or "").strip()
    low = s.lower()
    for suf in SKIP_SUFFIX:
        if low.endswith(suf):
            s = s[: -len(suf)]
            low = s.lower()
            break
    for ext in IMG_EXT:
        if low.endswith(ext):
            s = s[: -len(ext)]
            break
    return s.upper()


def ts_of(path):
    return datetime.datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M:%S")


# ---------------- 清单读写（内存缓存 + 脏标记，批量下载时不必每张都写盘）----------------
_lock = threading.Lock()
_cache = None
_dirty = False


def load(force=False):
    global _cache
    if _cache is not None and not force:
        return _cache
    if os.path.exists(MANIFEST):
        try:
            with io.open(MANIFEST, encoding="utf-8") as f:
                m = json.load(f)
        except Exception as e:                      # 清单坏了也不能拖垮爬虫：当空的，但会告警
            log(f"⚠ covers_manifest.json 解析失败（{e}），本轮按空清单处理")
            m = {}
    else:
        m = {}
    m.setdefault("version", 1)
    m.setdefault("covers_dir", "assets/covers")
    m.setdefault("rules", "清单即已下载记录：有 valid 记录=已下载；文件搬走后记录保留，不重复下载")
    m.setdefault("updated_at", "")
    m.setdefault("items", {})
    _cache = m
    return m


def save(m=None):
    global _dirty
    m = m or load()
    with _lock:
        items = m["items"]
        m["updated_at"] = NOW()
        m["total"] = len(items)
        m["present"] = sum(1 for v in items.values() if v.get("present"))
        m["valid"] = sum(1 for v in items.values() if v.get("valid"))
        with io.open(MANIFEST, "w", encoding="utf-8") as f:      # 直接写目标文件
            json.dump(m, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        _dirty = False


def flush():
    """有变更才落盘（供其它脚本在中途/收尾调用）。"""
    if _dirty:
        save()
    return _dirty


atexit.register(flush)


# ---------------- 目录实际文件名索引（Windows 大小写不敏感，但记录必须存真名）----------------
_idx = {"t": 0.0, "map": {}, "name": {}}


def dir_index(refresh=False):
    """covers/ 递归索引：{小写文件名: 相对子路径(含分桶目录)}，另存实际大小写名（缓存 5 分钟）。"""
    now = time.time()
    if refresh or not _idx["map"] or now - _idx["t"] > 300:
        mp, nm = {}, {}
        if os.path.isdir(COVERS):
            for root, _, files in os.walk(COVERS):
                for n in files:
                    low = n.lower()
                    if low in mp:
                        continue
                    mp[low] = os.path.relpath(os.path.join(root, n), COVERS).replace("\\", "/")
                    nm[low] = n
        _idx["map"], _idx["name"], _idx["t"] = mp, nm, now
    return _idx["map"]


def true_name(path_or_name):
    b = os.path.basename(path_or_name or "")
    dir_index()
    return _idx["name"].get(b.lower(), b)


# ---------------- 记录（只增不删）----------------
def _touch(rec, path=None, valid=None, origin="scan", note=""):
    """按磁盘实况刷新一条记录；path 为空表示文件不在目录里。"""
    if path and os.path.exists(path):
        rec["present"] = True
        rec["size"] = os.path.getsize(path)
        rec["mtime"] = ts_of(path)
        rec["last_seen"] = DAY()
        rec["valid"] = is_valid_image(path) if valid is None else bool(valid)
    else:
        rec["present"] = False                      # 只标「移走」，历史字段一个不动
        if valid is not None:
            rec["valid"] = bool(valid)
    if origin:
        rec["origin"] = origin
    if note:
        rec["note"] = note
    return rec


def put(code, path=None, valid=None, origin="scan", note=""):
    """追加或刷新一条记录（永不删除）。返回 (结果, 记录)。"""
    global _dirty
    m = load()
    c = code_of(code)
    rec = m["items"].get(c)
    is_new = rec is None
    if is_new:
        rec = {"code": c, "file": true_name(path) if path else c + ".jpg",
               "size": 0, "mtime": "", "first_seen": DAY(), "last_seen": "",
               "present": False, "valid": False, "origin": origin, "note": ""}
        m["items"][c] = rec
        res = "added"
    else:
        res = "known"
        if path and true_name(path) != rec.get("file"):
            rec["file"] = true_name(path)
            res = "updated"
    _touch(rec, path, valid, origin, note)
    if is_new:
        res = "added"
    elif rec.get("present") and not rec.get("last_seen"):
        res = "updated"
    _dirty = True
    return res, rec


# ---------------- 对外判定 API ----------------
def get(code):
    return load()["items"].get(code_of(code))


def is_downloaded(code):
    """清单说了算：有记录且当初确实下到了真图片 = 已下载（文件在不在都算）。"""
    rec = get(code)
    return bool(rec) and rec.get("valid") is not False


def find_on_disk(code):
    """covers/（含分桶子目录）里确实有一张真图片 -> 返回路径，否则 None。"""
    c = code_of(code)
    mp = dir_index()
    for ext in (".jpg", ".png", ".webp", ".jpeg"):
        rel = mp.get((c + ext).lower())
        if not rel:
            continue
        p = os.path.join(COVERS, *rel.split("/"))
        if is_valid_image(p):
            return p
    return None


def have(code):
    """唯一判定入口：这张封面是否已经有了（不必再下）。
    磁盘有真图片时**顺手登记**，所以就算没先跑 scan，搬回来的封面也不会被当成缺失。"""
    p = find_on_disk(code)
    if p:
        if not is_downloaded(code):
            put(code_of(code), p, valid=True, origin="disk")
        return True
    return is_downloaded(code_of(code))


def mark_downloaded(code, path=None, origin="download"):
    """下载成功后登记（valid=true）；不传 path 时自己按 covers/{CODE}.jpg 找。"""
    c = code_of(code)
    p = path or os.path.join(cover_dir(c), c + ".jpg")
    put(c, p if os.path.exists(p) else None, valid=is_valid_image(p), origin=origin)


def mark_invalid(code, note="文件在但不是图片（拦截页/残缺），允许重下"):
    """磁盘上的文件被验真判死 -> 记录改 valid=false，这样 --repair 才会重下它。
    ★ 垃圾文件仍然躺在目录里，不要把 present 抹成 false（否则会被当成「已搬走」）。"""
    c = code_of(code)
    p = None
    for ext in (".jpg", ".png", ".webp", ".jpeg"):
        q = os.path.join(cover_dir(c), c + ext)
        if os.path.exists(q):
            p = q
            break
    put(c, p, valid=False, origin="", note=note)


# ---------------- 子命令 ----------------
def cmd_scan(args):
    if not os.path.isdir(COVERS):
        log("covers/ 目录不存在（可能已整体搬走）：", COVERS)
        log("清单里的记录照旧有效（= 已下载，不重下）。如需重新登记请先跑 stats 查看。")
        return 1
    m = load()
    dir_index(refresh=True)                 # 先建实际文件名索引，免得把小写名登成大写
    names = []
    for _root, _d, _files in os.walk(COVERS):
        for _n in _files:
            names.append(os.path.relpath(os.path.join(_root, _n), COVERS).replace("\\", "/"))
    names = sorted(names)
    imgs, junk = [], []
    for n in names:
        if n.lower().endswith(SKIP_SUFFIX) or n.startswith("."):
            junk.append(n)
            continue
        if os.path.splitext(n)[1].lower() in IMG_EXT:
            imgs.append(n)
        else:
            junk.append(n)
    counts = {"added": 0, "updated": 0, "known": 0}
    seen, broken, suspect = set(), [], []
    for n in imgs:
        p = os.path.join(COVERS, *n.split("/"))
        code = code_of(n)
        ok = is_valid_image(p)
        if os.path.getsize(p) < MIN_BYTES and ok:
            suspect.append((n, os.path.getsize(p)))
        counts[put(code, p, valid=None if ok else False,
                   origin="scan", note="" if ok else "文件在但不是图片（拦截页/残缺）")[0]] += 1
        seen.add(code)
        if not ok:
            broken.append(n)
    moved = []
    for k, v in m["items"].items():
        if k in seen:
            continue
        if v.get("present"):
            v["present"] = False                    # 只标移走，记录永久保留
            moved.append(k)
    save()
    log(f"[scan] 目录内图片 {len(imgs)} 张 | 新增记录 {counts['added']} | 变更 {counts['updated']} | "
        f"未变 {counts['known']} | 移出目录(保留记录) {len(moved)} | 清单总数 {len(m['items'])}"
        f"（其中有效 {m['valid']}）")
    if broken:
        log(f"  ⚠ 其中 {len(broken)} 张内容不是图片（记为 valid=false，允许 --repair 重下）："
            + ", ".join(broken[:10]) + (" ..." if len(broken) > 10 else ""))
    for n, st in suspect:
        log(f"  ⚠ 体积可疑 {n} ({st}B)")
    if junk and args.verbose:
        log("  非图片文件（不纳入清单）：", ", ".join(junk[:10]) + (" ..." if len(junk) > 10 else ""))
    return 0


def cmd_check(args):
    m = load()
    miss = []
    for name in args.names:
        c = code_of(name)
        rec = m["items"].get(c)
        if rec and rec.get("valid") is not False:
            on_disk = bool(find_on_disk(c))
            log(f"HAVE        {c}  ({rec.get('file', c + '.jpg')}, size={rec.get('size', 0)}, "
                f"首次记录 {rec.get('first_seen', '?')}, 目录在场={'yes' if on_disk else 'no 已搬走/别处'})")
        elif rec:
            log(f"INVALID     {c}  （记录里 valid=false：当年下到的是拦截页/残缺，需要重下）")
            miss.append(c)
        else:
            log(f"NOT-YET     {c}")
            miss.append(c)
    return 0 if not miss else 1


def cmd_missing(args):
    """候选清单：每行一个 code（或文件名）；也支持 .json（数组 / 对象 keys）。"""
    raw = args.candidates
    if os.path.exists(raw):
        if raw.lower().endswith(".json"):
            with io.open(raw, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data = data.get("codes") or data.get("items") or list(data.keys())
            cands = [x if isinstance(x, str) else (x.get("code") or x.get("file") or "") for x in data]
        else:
            with io.open(raw, encoding="utf-8") as f:
                cands = [s.strip().strip(",").strip('"') for s in f
                         if s.strip() and not s.startswith("//")]
    else:
        cands = [raw]
    need = [code_of(c) for c in cands if c and not is_downloaded(c)]
    if getattr(args, "out", ""):
        d = os.path.join(WB, time.strftime("%Y-%m-%d")); os.makedirs(d, exist_ok=True)
        op = os.path.join(d, args.out)
        with io.open(op, "w", encoding="utf-8") as f:
            f.write("\n".join(need) + ("\n" if need else ""))
        log("[out] 待办名单 -> %s（%d 条，已自动归入当日日期夹）" % (op, len(need)), )
    else:
        for c in need:
            log(c)
    log(f"# 候选 {len(cands)} 张，清单已有 {len(cands) - len(need)} 张，待下载 {len(need)} 张",
        file=sys.stderr)
    return 0


def cmd_mark(args):
    for name in args.names:
        c = code_of(name)
        p = os.path.join(cover_dir(c), c + ".jpg")
        if os.path.exists(p):
            res = put(c, p, origin="mark", note=args.note)[0]
        else:
            res = put(c, None, valid=True, origin="mark",
                      note=args.note or "手动登记（文件不在 covers/，仍视为已下载）")[0]
        log(f"{res:8s} {c}")
    save()
    return 0


def cmd_drop(args):
    """误登记时才用：从清单剔记录（不碰 covers/ 里任何文件）。"""
    m = load()
    for name in args.names:
        c = code_of(name)
        if c not in m["items"]:
            log(f"not-found {c}")
            continue
        if not args.yes:
            log(f"⚠ 删除记录会让 {c} 被当成未下载而重下；确认请加 --yes")
            continue
        m["items"].pop(c)
        log(f"dropped  {c}")
    save()
    return 0


def cmd_stats(args):
    m = load()
    items = m["items"]
    gone = [k for k, v in items.items() if not v.get("present")]
    invalid = [k for k, v in items.items() if v.get("valid") is False]
    on_disk = sum(len(fs) for _, _d, fs in os.walk(COVERS)) if os.path.isdir(COVERS) else -1
    total_size = sum(int(v.get("size") or 0) for v in items.values())
    log(f"清单文件      : {MANIFEST}")
    log(f"封面目录      : {COVERS}  （当前 {on_disk if on_disk >= 0 else '目录不存在'} 个条目）")
    log(f"最后更新      : {m.get('updated_at') or '-'}")
    log(f"记录总数      : {len(items)}   （即『已下载』总量，含已搬走的）")
    log(f"目录内仍存在  : {len(items) - len(gone)}")
    log(f"已搬走/移走    : {len(gone)}   （仍视为已下载，不会重下）")
    log(f"其中 valid=false（当年下到的是拦截页/残缺，允许重下）: {len(invalid)}")
    log(f"累计体积      : {total_size / 1048576:.1f} MB")
    if gone:
        log("搬走示例      : " + ", ".join(sorted(gone)[:8]) + (" ..." if len(gone) > 8 else ""))
    return 0


def cmd_list(args):
    items = load()["items"]
    for k in sorted(items):
        v = items[k]
        if args.present and not v.get("present"):
            continue
        if args.gone and v.get("present"):
            continue
        if args.invalid and v.get("valid") is not False:
            continue
        if args.since and (v.get("first_seen") or "") < args.since:
            continue
        log(v.get("file") if args.file else k)
    return 0


def cmd_report(args):
    m = load()
    items = m["items"]
    gone = sorted(k for k, v in items.items() if not v.get("present"))
    invalid = sorted(k for k, v in items.items() if v.get("valid") is False)
    lines = [
        "# covers/ 已下载清单（由 _script/covers_manifest.py report 自动生成，请勿手改）",
        "",
        f"- 生成时间：{NOW()}",
        f"- 记录总数：**{len(items)}**（有 valid 记录 = 已下载，增量同步时不再重复下载）",
        f"- 目录内仍在：{len(items) - len(gone)} | 已搬走（保留记录）：{len(gone)}",
        f"- 其中 valid=false（当年下到的是拦截页/残缺，允许重下）：{len(invalid)}",
        f"- 真相来源：`data/state/covers_manifest.json`（下面只是可读视图；十万级明细请用 list/missing 现查）",
        "",
        "## valid=false 的记录（--repair 会重下）",
        "",
        ("- 无" if not invalid else "\n".join(f"- `{k}`：{items[k].get('note') or ''}" for k in invalid[:200])),
        "",
        "## 已搬走的记录（文件不在 covers/，但清单保留、不重下）",
        "",
    ]
    if gone:
        head = gone[:300]
        lines += [f"- `{k}`（首次记录 {items[k].get('first_seen', '-')}，最后在场 {items[k].get('last_seen', '-')}）"
                  for k in head]
        if len(gone) > len(head):
            lines.append(f"- ……其余 {len(gone) - len(head)} 条见 `covers_manifest.py list --gone`")
    else:
        lines.append("- 无")
    # 只打印、不落盘（清单 json 是唯一真相；派生 .md 没人读、还会过期）
    print("\n".join(lines))
    log("\n# report 只输出到 stdout；要留档：python _script/covers_manifest.py report > ../_logs/covers_manifest_view.txt")
    return 0


def main():
    ap = argparse.ArgumentParser(description="covers/ 已下载清单维护工具（判定以清单为准，不看目录）")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("scan"); p.add_argument("-v", "--verbose", action="store_true", help="打印非图片文件")
    p = sub.add_parser("check"); p.add_argument("names", nargs="+")
    p = sub.add_parser("missing"); p.add_argument("candidates"); p.add_argument("--out", default="", help="把待办名单写到 _pipeline/<日期>/<out>")
    p = sub.add_parser("mark"); p.add_argument("names", nargs="+"); p.add_argument("--note", default="")
    p = sub.add_parser("drop"); p.add_argument("names", nargs="+"); p.add_argument("--yes", action="store_true")
    sub.add_parser("stats")
    p = sub.add_parser("list")
    p.add_argument("--present", action="store_true", help="只要当前还在 covers/ 里的（= 该搬走的新封面）")
    p.add_argument("--gone", action="store_true", help="只要已搬走的")
    p.add_argument("--invalid", action="store_true", help="只要 valid=false 的")
    p.add_argument("--since", default="", help="只要首次记录日期 >= 该日（YYYY-MM-DD）")
    p.add_argument("--file", action="store_true", help="输出文件名而不是番号")
    sub.add_parser("report")
    args = ap.parse_args()
    if not args.cmd:
        ap.print_help()
        return 2
    return {"scan": cmd_scan, "check": cmd_check, "missing": cmd_missing, "mark": cmd_mark,
            "drop": cmd_drop, "stats": cmd_stats, "list": cmd_list, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
