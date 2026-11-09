# -*- coding: utf-8 -*-
"""videos/ 目录「已下载清单」维护工具（增量下载/同步的判定依据）。

清单真相来源：`data/state/videos_manifest.json`（唯一被读取的文件；不再有 .md 副本）。

★★ 核心判定规则（所有下载/同步脚本都必须遵守）★★
  判定一律以「清单里有没有这条记录」为准，而 NOT 以「assets/videos/ 目录里有没有这个文件」为准：
  - 清单里有记录 = 已下载 = 绝不再下载；
  - 即使 assets/videos/ 目录为空 / 文件不在，也当作「已下载」——因为文件很可能是被手动
    搬到了移动硬盘/别处，只要记录还在就不重下（见规则 2、4）。
  这样可以避免：搬走文件后目录变空 → 误判为未下载 → 重复下载浪费时间和带宽。

核心规则（勿违背）：
  1. assets/videos/ 里出现的新文件  -> 追加记录（scan）；
  2. 文件被手动移走（移动硬盘）-> 记录永久保留，只把 present 置 false；
  3. 增量下载前一律先比对清单：清单里有记录 = 已下载 = 不再下载，
     即使目录里已经没有这个文件（默认视为已被手动移动/清理）。
本脚本只写清单文件，绝不删除/移动 assets/videos/ 里的任何内容。
所有写盘直接写目标文件（本机沙盒会把 tmp+replace 当危险操作中断进程）。

用法：
  python videos_manifest.py scan                     # 扫 assets/videos/，补新记录、更新状态（最常用）
  python videos_manifest.py check <文件或code>...     # 判断是否已下载（exit 0=全部已下载）
  python videos_manifest.py missing <候选清单文件>     # 输出未下载的 code（每行一个，供下载器消费）
  python videos_manifest.py mark <文件名> [--note 说明] # 手动登记一条记录（文件不在目录也算已下载）
  python videos_manifest.py drop <文件名> [--yes]      # 仅用于清理误登记（正常流程永不用）
  python videos_manifest.py stats                    # 统计
  python videos_manifest.py list [--present|--gone]  # 导出文件名清单
  python videos_manifest.py report                   # 把可读视图打印到 stdout（要留档就 > _logs/xxx.txt）
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
import json
import os
import sys
import time
import win_utf8        # noqa: F401  重定向/管道下固定 UTF-8，否则 ✅ 会砸死进程

DATA = os.path.join(ROOT, "data")                        # 站点数据与名单
SITE = os.path.join(DATA, "site")    # 页面要读的 *.js（HTML 写死引用）
STATE = os.path.join(DATA, "state")  # 抓取状态：清单 / 终态名单 / 校验基准
ASSETS = os.path.join(ROOT, "assets")                        # 资源目录
WB = os.path.join(ROOT, "_pipeline")     # 过程中文件：断点 / cookie / 临时切片（删了会丢进度）
LOGS = os.path.join(ROOT, "_logs")      # 日志与验证截图：随时可删，不影响任何数据
VID = os.path.join(ASSETS, "videos")            # 视频目录（可为空；文件可能被搬走）
# 清单（唯一真相）在 data/state/，随仓库版本化；可读视图只打印不落盘
MANIFEST = os.path.join(STATE, "videos_manifest.json")

VIDEO_EXT = {".mp4", ".m4v", ".mkv", ".webm", ".avi", ".mov", ".ts", ".flv", ".wmv", ".mpg", ".mpeg"}
SKIP_SUFFIX = (".part", ".tmp", ".downloading", ".crdownload")
MIN_OK = 10000            # 小于此字节数视为可疑残留（仍记录，但加 note 提示）
NOW = lambda: time.strftime("%Y-%m-%d %H:%M:%S")
DAY = lambda: time.strftime("%Y-%m-%d")


def log(*a):
    print(*a, flush=True)


# ---------------- 读写 ----------------
def load():
    if os.path.exists(MANIFEST):
        with open(MANIFEST, encoding="utf-8") as f:
            m = json.load(f)
    else:
        m = {}
    m.setdefault("version", 1)
    m.setdefault("videos_dir", "assets/videos")
    m.setdefault("rules", "清单即已下载记录：有记录=已下载；文件移走后记录保留，不重复下载")
    m.setdefault("updated_at", "")
    m.setdefault("items", {})
    return m


def save(m):
    m["updated_at"] = NOW()
    m["total"] = len(m["items"])
    m["present"] = sum(1 for v in m["items"].values() if v.get("present"))
    # 直接写目标文件，key 排序便于 diff
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def ts_of(path):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(path)))


def code_of(fname):
    return os.path.splitext(fname)[0]


def is_video_file(name):
    n = name.lower()
    if n.endswith(SKIP_SUFFIX) or n.startswith("."):
        return False
    return os.path.splitext(n)[1] in VIDEO_EXT


# ---------------- 记录 ----------------
def add_record(m, fname, path, source, note=""):
    """追加或刷新一条记录（record 只会新增/更新，不会删除）。"""
    items = m["items"]
    if isinstance(fname, dict):                 # 手动登记（文件不在目录）
        rec = items.get(fname["file"])
        if rec is None:
            items[fname["file"]] = fname
            return "added"
        changed = False
        for k in ("size", "url", "source_page", "note"):
            if fname.get(k) not in (None, "") and rec.get(k) != fname[k]:
                rec[k] = fname[k]
                changed = True
        rec["last_seen"] = rec.get("last_seen") or DAY()
        return "updated" if changed else "known"
    size = os.path.getsize(path) if path and os.path.exists(path) else 0
    rec = items.get(fname)
    if rec is None:
        items[fname] = {
            "file": fname,
            "code": code_of(fname),
            "size": size,
            "mtime": ts_of(path),
            "first_seen": DAY(),
            "last_seen": DAY(),
            "present": True,
            "origin": source,
            "note": note,
        }
        return "added"
    out = "known"
    if rec.get("size") != size:
        rec["size"] = size
        rec["mtime"] = ts_of(path)
        out = "updated"
    if not rec.get("present"):
        out = "back"                            # 曾移走、现在又回到目录
    rec["present"] = True
    rec["last_seen"] = DAY()
    rec.setdefault("code", code_of(fname))
    if note and not rec.get("note"):
        rec["note"] = note
    return out


def match(name, m=None):
    """按 文件名 或 code 匹配已有记录，返回 (key, rec)；不改清单内容。"""
    items = (m or load())["items"]
    if name in items:
        return name, items[name]
    stem = os.path.splitext(name)[0]
    for k, v in items.items():
        if v.get("code") == stem:
            return k, v
    return None, None


# ---------------- 子命令 ----------------
def cmd_scan(args):
    m = load()
    if not os.path.isdir(VID):
        log("videos/ 目录不存在：", VID)
        return 1
    names = sorted(n for n in os.listdir(VID) if os.path.isfile(os.path.join(VID, n)))
    video, skipped = [], []
    for n in names:
        (video if is_video_file(n) else skipped).append(n)

    counts = {"added": 0, "updated": 0, "back": 0, "known": 0}
    suspect = []
    seen = set()
    for n in video:
        p = os.path.join(VID, n)
        st = os.path.getsize(p)
        note = "疑似未下完的残留（体积过小）" if st < MIN_OK else ""
        counts[add_record(m, n, p, "scan", note)] += 1
        seen.add(n)
        if note:
            suspect.append((n, st))

    # 目录里没有、但清单有记录 -> 仅标记 present=false，记录永久保留
    moved = []
    for k, v in m["items"].items():
        if k in seen:
            continue
        if v.get("present"):
            v["present"] = False
            moved.append(k)

    save(m)
    log(f"[scan] 目录内视频 {len(video)} 个 | 新增记录 {counts['added']} | "
        f"变更 {counts['updated']} | 回归 {counts['back']} | 未变 {counts['known']} | "
        f"移出目录(保留记录) {len(moved)} | 清单总数 {len(m['items'])}")
    if skipped:
        log("  非视频文件（不纳入清单）：", ", ".join(skipped[:10]) + (" ..." if len(skipped) > 10 else ""))
    for n, st in suspect:
        log(f"  ⚠ 可疑小文件 {n} ({st}B)：若确认下载失败，请手动从清单删除该条再重下")
    if moved and args.verbose:
        log("  已移出目录（仍视为已下载）：" + ", ".join(moved[:20]) + (" ..." if len(moved) > 20 else ""))
    return 0


def cmd_check(args):
    m = load()
    missing = []
    for name in args.names:
        k, rec = match(name, m)
        if rec:
            log(f"DOWNLOADED  {name}  ->  {k}  (present={str(bool(rec.get('present'))).lower()}, "
                f"size={rec.get('size', 0)}, first_seen={rec.get('first_seen', '?')})")
        else:
            log(f"NOT-YET     {name}")
            missing.append(name)
    return 0 if not missing else 1


def cmd_missing(args):
    """候选清单：每行一个 code 或文件名；也支持 .json（数组 / 对象 keys / items 数组）。"""
    cands = []
    if args.candidates.lower().endswith(".json"):
        with open(args.candidates, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data = data.get("items") if isinstance(data.get("items"), list) else list(data.keys())
        for it in data:
            if isinstance(it, dict):
                cands.append(it.get("file") or it.get("code") or it.get("name") or "")
            else:
                cands.append(str(it))
    else:
        with open(args.candidates, encoding="utf-8") as f:
            for line in f:
                s = line.strip().strip(",").strip('"')
                if s and not s.startswith("//"):
                    cands.append(s)
    m = load()
    need = [c for c in cands if c and not match(c, m)[0]]
    lines = [code_of(c) for c in need]
    if getattr(args, "out", ""):
        d = os.path.join(WB, time.strftime("%Y-%m-%d")); os.makedirs(d, exist_ok=True)
        op = os.path.join(d, args.out)
        with open(op, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + ("\n" if lines else ""))
        print("[out] 待办名单 -> %s（%d 条，已自动归入当日日期夹）" % (op, len(lines)), file=sys.stderr)
    else:
        for c in lines:
            log(c)
    print(f"# 候选 {len(cands)} 条，清单已有 {len(cands) - len(need)} 条，待下载 {len(need)} 条", file=sys.stderr)
    return 0


def cmd_mark(args):
    m = load()
    for name in args.names:
        fname = name if os.path.splitext(name)[1] else name + ".mp4"
        p = os.path.join(VID, fname)
        if os.path.exists(p):
            res = add_record(m, fname, p, "mark", args.note)
        else:
            res = add_record(m, {
                "file": fname, "code": code_of(fname), "size": 0,
                "mtime": "", "first_seen": DAY(), "last_seen": DAY(),
                "present": False, "origin": "mark", "note": args.note or "手动登记",
            }, None, "mark")
        log(f"{res:8s} {fname}")
    save(m)
    return 0


def cmd_drop(args):
    """误登记时才用：从清单剔除记录（不碰 videos/ 里任何文件）。"""
    m = load()
    for name in args.names:
        k, rec = match(name, m)
        if not rec:
            log(f"not-found {name}")
            continue
        if not args.yes:
            log(f"⚠ 删除记录会让 {k} 被当成未下载而重下；确认请加 --yes")
            continue
        m["items"].pop(k)
        log(f"dropped  {k}")
    save(m)
    return 0


def cmd_stats(args):
    m = load()
    items = m["items"]
    gone = [k for k, v in items.items() if not v.get("present")]
    total_size = sum(int(v.get("size") or 0) for v in items.values())
    log(f"清单文件      : {MANIFEST}")
    log(f"最后更新      : {m.get('updated_at') or '-'}")
    log(f"记录总数      : {len(items)}   （即『已下载』总量，含已移走的）")
    log(f"目录内仍存在  : {len(items) - len(gone)}")
    log(f"已移走/清理   : {len(gone)}   （仍视为已下载，不会重下）")
    log(f"累计体积      : {total_size / 1073741824:.2f} GB")
    if gone:
        log("移走示例      : " + ", ".join(sorted(gone)[:8]) + (" ..." if len(gone) > 8 else ""))
    return 0


def cmd_list(args):
    m = load()
    for k in sorted(m["items"]):
        v = m["items"][k]
        if args.present and not v.get("present"):
            continue
        if args.gone and v.get("present"):
            continue
        log(k)
    return 0


def cmd_report(args):
    m = load()
    items = m["items"]
    gone = sorted(k for k, v in items.items() if not v.get("present"))
    lines = [
        "# videos/ 已下载清单（由 _script/videos_manifest.py report 自动生成，请勿手改）",
        "",
        f"- 生成时间：{NOW()}",
        f"- 记录总数：**{len(items)}**（有记录 = 已下载，增量同步时不再重复下载）",
        f"- 目录内仍在：{len(items) - len(gone)} | 已手动移走（保留记录）：{len(gone)}",
        f"- 真相来源：`data/state/videos_manifest.json`（下面只是可读视图；全量明细请用 list --present/--gone 现查）",
        "",
        "## 已移走的记录（文件不在 videos/，但清单保留）",
        "",
    ]
    if gone:
        for k in gone:
            v = items[k]
            lines.append(f"- `{k}`（首次记录 {v.get('first_seen', '-')}，最后在场 {v.get('last_seen', '-')}）")
    else:
        lines.append("- 无")
    lines += ["", "## 全部记录", "", "| 文件名 | 体积 | 文件时间 | 首次记录 | 在目录 |", "|---|---|---|---|---|"]
    for k in sorted(items):
        v = items[k]
        sz = f"{int(v.get('size') or 0) / 1048576:.1f}MB"
        lines.append(f"| `{k}` | {sz} | {v.get('mtime') or '-'} | {v.get('first_seen') or '-'} "
                     f"| {'✅' if v.get('present') else '➡️ 已移走'} |")
    # 只打印、不落盘：清单 json 是唯一真相，.md 副本没人读、还会过期（原来 277 KB 重复数据）
    print("\n".join(lines))
    log("\n# report 只输出到 stdout；要留档：python _script/videos_manifest.py report > ../_logs/videos_manifest_view.txt")
    return 0


def main():
    ap = argparse.ArgumentParser(description="videos/ 已下载清单维护工具")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("scan"); p.add_argument("-v", "--verbose", action="store_true", help="打印移走明细")
    p = sub.add_parser("check"); p.add_argument("names", nargs="+")
    p = sub.add_parser("missing"); p.add_argument("candidates"); p.add_argument("--out", default="", help="把待办名单写到 _pipeline/<日期>/<out>")
    p = sub.add_parser("mark"); p.add_argument("names", nargs="+"); p.add_argument("--note", default="")
    p = sub.add_parser("drop"); p.add_argument("names", nargs="+"); p.add_argument("--yes", action="store_true")
    sub.add_parser("stats")
    p = sub.add_parser("list"); p.add_argument("--present", action="store_true"); p.add_argument("--gone", action="store_true")
    sub.add_parser("report")
    args = ap.parse_args()
    if not args.cmd:
        ap.print_help()
        return 2
    return {"scan": cmd_scan, "check": cmd_check, "missing": cmd_missing,
            "mark": cmd_mark, "drop": cmd_drop, "stats": cmd_stats, "list": cmd_list,
            "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
