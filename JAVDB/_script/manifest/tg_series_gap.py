# -*- coding: utf-8 -*-
"""TG 全量番号缓存 × series.js × 已下清单 三方对账。

场景：TG 群（水果派解说）里的视频番号已经用 `tg_dl.py --list --dump-codes` 扫成一份缓存
（`_pipeline/tg_videos_all.json`）；本脚本纯离线读缓存，算出：

  1. TG 侧共有多少番号（缓存全量）
  2. series.js（收藏的系列）里出现的番号有多少
  3. 两边对上的（= 群里确实有资源的系列番号）
  4. 其中**从未下载过**的 —— 判定依据 = `data/state/tg_manifest.json` 记录 + 目录里实际存在的视频文件
     （两者任一命中即算已下；文件搬去移动硬盘时记录仍在，所以不会误判成没下）

用法（仓库根执行）：
  python _script/manifest/tg_series_gap.py                 # 默认三方路径，打印摘要 + 写待办名单
  python _script/manifest/tg_series_gap.py --show 40       # 摘要里多列几个番号
  python _script/manifest/tg_series_gap.py --out _pipeline/xxx.txt

番号一律压缩成大写无分隔形式再比（SSIS-338 / ssis338 / SSIS 338 视为同一个），与 tg_dl.compact 同规则。
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
import collections
import datetime
import io
import json
import os
import re
import sys

import win_utf8        # noqa: F401  Windows 管道/重定向下固定 UTF-8，否则中文会砸死进程
import atomic_io       # noqa: E402

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".3gp", ".ts"}
MB = 1048576
DEFAULT_TG = os.path.join(ROOT, "_pipeline", "tg_videos_all.json")
DEFAULT_SERIES = os.path.join(ROOT, "data", "site", "series.js")
DEFAULT_MOVIES = os.path.join(ROOT, "data", "site", "movies.js")
DEFAULT_DIR = os.path.join(ROOT, "assets", "水果派")
# 已下载记录从 2026-11-04 起住 data/state（资源目录会被整体搬走，记录不能跟着走）
DEFAULT_MANIFEST = os.path.join(ROOT, "data", "state", "tg_manifest.json")
LEGACY_MANIFEST = "_tg_manifest.json"


def compact(text):
    """大写 + 去掉一切非字母数字，让 SSIS-338 / ssis338 / SSIS 338 互相能对上（同 tg_dl.compact）。"""
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


def load_json(path):
    if not os.path.isfile(path):
        sys.exit("找不到 %s\n  先跑一次全量扫描生成缓存：\n  _script/tg_video_dl/.venv/Scripts/python.exe "
                 "_script/tg_video_dl/tg_dl.py --watched \"\" --list --dump-codes \"%s\""
                 % (path, DEFAULT_TG))
    return json.loads(io.open(path, encoding="utf-8").read())


def load_js_var(path, var):
    """读 window.JAVDB_XXX = {...}; 形式的站点 js。

    注意：项目约定前缀是含空格的 `window.JAVDB_MOVIES = `（等号两侧各一空格），
    这里不依赖调用方传不传尾部 " = "，直接定位到 var 后的第一个等号再接着解。"""
    if not os.path.isfile(path):
        sys.exit("找不到 %s" % path)
    t = io.open(path, encoding="utf-8").read()
    if var not in t:
        sys.exit("%s 里找不到 `%s`" % (path, var))
    i = t.index(var)
    i = t.index("=", i) + 1
    d, _ = json.JSONDecoder().raw_decode(t[i:].lstrip())
    return d


def main():
    ap = argparse.ArgumentParser(description="TG 全量番号 × series.js × 已下清单 对账")
    ap.add_argument("--tg-cache", default=DEFAULT_TG, help="tg_dl.py --dump-codes 生成的缓存")
    ap.add_argument("--series", default=DEFAULT_SERIES, help="data/site/series.js")
    ap.add_argument("--movies", default=DEFAULT_MOVIES,
                    help="data/site/movies.js（取影片本身的**发行日期**；TG 缓存里的 date 是群里上架日期，两者不同）")
    ap.add_argument("--dir", default=DEFAULT_DIR, help="解说视频目录（只用来数磁盘上实际存在的视频）")
    ap.add_argument("--manifest", default="",
                    help="已下载记录 json，默认 %s（该位置不存在时回退到目录里的 %s）" % (DEFAULT_MANIFEST, LEGACY_MANIFEST))
    ap.add_argument("--show", type=int, default=15, help="摘要里列出前多少个番号")
    ap.add_argument("--since", default="", help="只要发行日期 >= 该日（YYYY-MM-DD 或纯年份，如 2023）")
    ap.add_argument("--until", default="", help="只要发行日期 <= 该日（含；YYYY-MM-DD 或纯年份）")
    ap.add_argument("--out", default="", help="待办名单输出路径（默认 _pipeline/<今日>/series_not_downloaded.txt）")
    args = ap.parse_args()

    tg = load_json(args.tg_cache)
    items = tg.get("items", [])
    series = load_js_var(args.series, "window.JAVDB_SERIES")
    movies = load_js_var(args.movies, "window.JAVDB_MOVIES")

    # --- TG 侧：压缩番号 -> 该番号最新的一条消息记录（同番号反复发布只认最新）---
    # 体积优先用 share（合集帖一条消息 N 个番号时的平摊值），没有才退回整条消息 size
    tg_map = {}
    for it in items:
        key = compact(it.get("code") or os.path.splitext(it.get("name") or "")[0])
        if not key or not any(c.isdigit() for c in key):
            continue
        old = tg_map.get(key)
        if old is None or (it.get("date") or "") >= (old.get("date") or ""):
            tg_map[key] = it

    # --- series.js 侧：压缩番号 -> 所属系列名列表 ---
    s_map, s_total = {}, 0
    for it in series.get("items", []):
        for code in it.get("codes", []) or []:
            key = compact(code)
            if not key:
                continue
            s_total += 1
            s_map.setdefault(key, []).append(it.get("name") or it.get("id") or "?")

    # --- 已下：记录 + 目录里实际存在的视频文件（任一命中即算已下）---
    man_path = args.manifest or (DEFAULT_MANIFEST if os.path.isfile(DEFAULT_MANIFEST)
                                 else os.path.join(args.dir, LEGACY_MANIFEST))
    man = json.loads(io.open(man_path, encoding="utf-8").read()) if os.path.isfile(man_path) else {}
    got = {compact(os.path.splitext(v.get("name") or "")[0]) for v in man.values()}
    disk = []
    if os.path.isdir(args.dir):
        for n in os.listdir(args.dir):
            p = os.path.join(args.dir, n)
            if os.path.isfile(p) and os.path.splitext(n)[1].lower() in VIDEO_EXT:
                disk.append(n)
                got.add(compact(os.path.splitext(n)[0]))
    got.discard("")

    # --- 三方求交/求差 ---
    inter = sorted(set(tg_map) & set(s_map))                    # 群里确实有资源的系列番号
    never = [k for k in inter if k not in got]                   # 其中从未下载过的
    def rel_of(r):
        """影片本身的发行日期（movies.js 的 date）；缓存里的 date 是 TG 上架日，别混。"""
        code = r.get("code") or os.path.splitext(r.get("name") or "")[0]
        return (movies.get(code) or {}).get("date") or ""

    never_rows = sorted((tg_map[k] for k in never),
                        key=lambda x: (rel_of(x), x.get("date") or ""), reverse=True)   # 按发行日期新→旧

    # 按发行日期区间筛（没发行日期的在开了筛选项时一律排除，免得混进名单）
    filtered = bool(args.since or args.until)
    if filtered:
        lo = args.since if len(args.since) > 4 else (args.since + "-01-01")
        hi = args.until if len(args.until) > 4 else (args.until + "-12-31")
        before = len(never_rows)
        never_rows = [r for r in never_rows if rel_of(r) and lo <= rel_of(r) <= hi]
        print("已按发行日期筛取 %s ~ %s：%d → %d 个（排除无发行日期 %d 个）"
              % (lo, hi, before, len(never_rows),
                 sum(1 for r in never_rows if not rel_of(r))))

    size_all = sum(int(r.get("share") or r.get("size") or 0) for r in never_rows)
    multi = sum(1 for r in never_rows if int(r.get("msg_codes") or 1) > 1)

    print("TG 缓存      : %s" % args.tg_cache)
    print("               扫描时间 %s | 条目 %d | 去重后番号 %d"
          % (tg.get("updated", "?"), len(items), len(tg_map)))
    if tg.get("scanned"):
        print("               消息 %s 条 | 带视频 %s 条 | 抽不到番号 %s 条 | 来自合集帖的记录 %d 条"
              % (tg.get("scanned"), tg.get("video_msgs"), tg.get("no_code_msgs"),
                 sum(1 for i in items if int(i.get("msg_codes") or 1) > 1)))
    print("series.js    : %s" % args.series)
    print("               系列 %d 个 | 番号引用 %d 次 | 去重番号 %d"
          % (len(series.get("items", [])), s_total, len(s_map)))
    print("已下记录     : manifest %d 条 + 目录实际文件 %d 个 -> 去重番号 %d"
          % (len(man), len(disk), len(got)))
    print("-" * 64)
    print("series ∩ TG  : %d 个（收藏系列里、群里确实有视频的番号）" % len(inter))
    print("  其中已下载 : %d 个" % (len(inter) - len(never)))
    print("  从未下载   : %d 个，合计 %.2f GB%s" % (len(never), size_all / 1073741824,
                                    "（合集帖按平摊计，其中 %d 个来自合集帖）" % multi if multi else ""))
    if filtered:
        print("     ↑ 已按发行日期筛，上面是筛后数量（本次名单共 %d 个 / %.2f GB）"
              % (len(never_rows), size_all / 1073741824))
    print("  参考：TG 有但不在 series 的番号 %d 个" % len(set(tg_map) - set(s_map)))
    rels = [rel_of(r) for r in never_rows if rel_of(r)]
    if rels:
        yrs = collections.Counter(x[:4] for x in rels)
        print("  发行日期   : %s ~ %s（%d 个没发行日期）" % (min(rels), max(rels), len(never_rows) - len(rels)))
        print("  按年份     : " + "  ".join("%s:%d" % (y, yrs[y]) for y in sorted(yrs)))

    if never_rows:
        print("\n从未下载（按**发行日期**新→旧，前 %d 个）:" % min(args.show, len(never_rows)))
        print("  %-14s %-11s %-11s %7s  %s" % ("番号", "发行日期", "TG上架", "MB", "所属系列"))
        for r in never_rows[:args.show]:
            code = r.get("code") or os.path.splitext(r.get("name") or "")[0]
            sers = "/".join(s_map.get(compact(code), [])[:2])
            tag = " [合集x%d]" % int(r["msg_codes"]) if int(r.get("msg_codes") or 1) > 1 else ""
            print("  %-14s %-11s %-11s %7.1f  %s%s"
                  % (code, rel_of(r) or "—", r.get("date") or "?",
                     int(r.get("share") or r.get("size") or 0) / MB, sers[:36], tag))

    # --- 落盘：全量待办名单（番号 + 日期 + 体积 + 所属系列），供后续 --codes 直接喂给下载器 ---
    out = args.out or atomic_io.record_path("series_not_downloaded.txt")
    with io.open(out, "w", encoding="utf-8") as f:
        f.write("# 收藏系列里、TG 群有资源、但从未下载过的番号（生成 %s）\n" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        f.write("# 来源：TG 缓存 %s × %s × 已下(manifest %d + 目录 %d)\n"
                % (os.path.basename(args.tg_cache), os.path.basename(args.series), len(man), len(disk)))
        f.write("# 共 %d 个，合计 %.2f GB；下面每行：番号<TAB>发行日期<TAB>TG上架日期<TAB>MB<TAB>所属系列\n"
                % (len(never_rows), size_all / 1073741824))
        f.write("# （发行日期取自 movies.js；TG 上架日期是群里发消息的日期，两者不同）\n")
        for r in never_rows:
            code = r.get("code") or os.path.splitext(r.get("name") or "")[0]
            f.write("%s\t%s\t%s\t%.1f\t%s\n" % (code, rel_of(r), r.get("date") or "",
                                                 int(r.get("share") or r.get("size") or 0) / MB,
                                                 "/".join(s_map.get(compact(code), []))))
    # 纯番号一份，可直接当 tg_dl.py --codes 的输入
    plain = os.path.splitext(out)[0] + ".codes.txt"
    with io.open(plain, "w", encoding="utf-8") as f:
        for r in never_rows:
            f.write("%s\n" % (r.get("code") or os.path.splitext(r.get("name") or "")[0]))
    print("\n待办名单 -> %s（明细，含日期/体积/系列）\n          -> %s（纯番号，可直接喂 tg_dl.py --codes）"
          % (out, plain))
    return 0


if __name__ == "__main__":
    sys.exit(main())
