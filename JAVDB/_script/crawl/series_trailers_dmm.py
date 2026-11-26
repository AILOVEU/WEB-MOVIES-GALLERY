# -*- coding: utf-8 -*-
"""系列预告片下载器：每个系列只取**最新**和**次新**两部。

规则（用户 2026-11-25 要求）：
  - 遍历 data/site/series.js 的全部系列
  - 每个系列按 movies.js 的 date 降序，取前 2 部（最新 + 次新）
  - 有预告就下，没有就跳过（不报错、不重试）
  - 命名与地址格式沿用旧脚本：assets/videos/<code>.mp4

复用 top250_trailers_dmm.py 的核心能力：
  - CDP 浏览器探测 DMM 页面（curl 抓不到，返回固定拦截页）
  - 从 HTML 提取 /pv/<token>/<cid>hhb.mp4 真实地址（带签名，无法构造）
  - 直接写目标文件下载

用法：
  python series_trailers_dmm.py --dry-run        # 只看要处理多少
  python series_trailers_dmm.py --limit 10       # 试跑前 10 个
  python series_trailers_dmm.py                  # 全量
  python series_trailers_dmm.py --recheck        # 连已判无预告的也重查
"""
# __JDB_BOOT__
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
import io
import json
import os
import re
import sys
import time

import win_utf8        # noqa: F401

# 复用 top250 脚本里已验证的探测/下载能力
import top250_trailers_dmm as T

SITE = os.path.join(ROOT, "data", "site")
STATE = os.path.join(ROOT, "data", "state")
VID_DIR = os.path.join(ROOT, "assets", "videos")
WB = os.path.join(ROOT, "_pipeline")
LOGS = os.path.join(ROOT, "_logs")
CKPT = os.path.join(WB, "series_dmm_ckpt.json")
LOG = os.path.join(LOGS, time.strftime("%Y-%m-%d") + "_series_dmm.log")

CID_RE = re.compile(r"/digital/video/([a-z0-9_]+)/")


def log(*a):
    line = "%s %s" % (time.strftime("[%H:%M:%S]"), " ".join(str(x) for x in a))
    print(line, flush=True)
    try:
        os.makedirs(LOGS, exist_ok=True)
        with io.open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_ckpt():
    if not os.path.exists(CKPT):
        return {}
    try:
        return json.load(io.open(CKPT, encoding="utf-8")) or {}
    except Exception:
        return {}


def save_ckpt(d):
    os.makedirs(WB, exist_ok=True)
    io.open(CKPT, "w", encoding="utf-8").write(
        json.dumps(d, ensure_ascii=False, indent=1, sort_keys=True))


def pick_series_top2(series_items, movies):
    """每个系列按 date 降序取前 2 部。返回 [(series_name, [code, ...])]。"""
    out = []
    for s in series_items:
        codes = s.get("codes") or []
        dated = []
        for c in codes:
            m = movies.get(c)
            if m and m.get("date"):
                dated.append((m["date"], c))
        if not dated:
            continue
        dated.sort(reverse=True)
        out.append((s.get("name") or "?", [c for _, c in dated[:2]]))
    return out


def main():
    ap = argparse.ArgumentParser(description="系列预告片下载（每系列最新+次新）")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 个 code（0=全部）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--recheck", action="store_true", help="连已判无预告的也重查")
    ap.add_argument("--wait", type=float, default=3.2, help="每页等待渲染秒数")
    args = ap.parse_args()

    series_items = T.loadjs("series.js")["items"]
    movies = T.loadjs("movies.js")

    pairs = pick_series_top2(series_items, movies)
    log("系列 %d 个 | 每系列取最新+次新" % len(pairs))

    # 展平 + 去重（同一 code 可能属于多个系列）
    flat = []
    seen = set()
    for name, codes in pairs:
        for c in codes:
            if c in seen:
                continue
            seen.add(c)
            flat.append((c, name))
    log("展平后 %d 个 code（去重后 %d）" % (sum(len(c) for _, c in pairs), len(flat)))

    # 剧照地址（用于反推 cid）
    samples = {}
    smp_path = os.path.join(STATE, "samples.js")
    if os.path.exists(smp_path):
        t = io.open(smp_path, encoding="utf-8").read()
        i = t.index("window.JAVDB_SAMPLES") + len("window.JAVDB_SAMPLES")
        rest = t[i:].lstrip()
        if rest.startswith("="):
            rest = rest[1:].lstrip()
        samples, _ = json.JSONDecoder().raw_decode(rest)

    man = json.load(io.open(os.path.join(STATE, "videos_manifest.json"), encoding="utf-8"))
    man_items = man.get("items", {})
    npv = json.load(io.open(os.path.join(STATE, "videos_no_preview.json"), encoding="utf-8"))
    npv_items = npv.get("items", {})
    ckpt = load_ckpt()

    todo, skip_dl, skip_npv, no_cid = [], 0, 0, 0
    for c, sname in flat:
        fn = T.out_name(c)
        if fn in man_items or c in man_items:
            skip_dl += 1
            continue
        st = ckpt.get(c, {}).get("status", "")
        if st == "ok":
            skip_dl += 1
            continue
        if c in npv_items and not args.recheck:
            skip_npv += 1
            continue
        cid = None
        for u in (samples.get(c) or []):
            m = CID_RE.search(u)
            if m:
                cid = m.group(1)
                break
        if not cid:
            cid = T.code_to_cid(c)
        if not cid:
            no_cid += 1
            continue
        todo.append((c, cid, sname))

    if args.limit:
        todo = todo[:args.limit]

    log("已下载 %d | 确认无预告 %d | 无 cid %d | 本轮待处理 %d%s"
        % (skip_dl, skip_npv, no_cid, len(todo),
           "  [--recheck]" if args.recheck else ""))

    if args.dry_run:
        for c, cid, sname in todo[:25]:
            print("  %-16s cid=%-16s [%s]" % (c, cid, sname[:20]))
        log("dry-run，未下载。")
        return 0
    if not todo:
        log("没有需要处理的条目。")
        return 0

    ok = fail = nopv = 0
    t0 = time.time()
    for i, (code, cid, sname) in enumerate(todo, 1):
        st, pv = T.probe_dmm(cid, wait=args.wait)
        if st == "OK" and pv:
            dest = os.path.join(VID_DIR, T.out_name(code))
            good, size = T.download(pv, dest)
            if good:
                ok += 1
                ckpt[code] = {"status": "ok", "size": size, "url": pv, "series": sname,
                              "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
                log("[%d/%d] %-16s OK  %.1f MB  [%s]"
                    % (i, len(todo), code, size / 1048576, sname[:18]))
            else:
                fail += 1
                ckpt[code] = {"status": "dl_fail", "url": pv, "series": sname,
                              "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
                log("[%d/%d] %-16s 下载失败" % (i, len(todo), code))
        elif st == "404":
            nopv += 1
            ckpt[code] = {"status": "404", "series": sname,
                          "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
            log("[%d/%d] %-16s DMM 404" % (i, len(todo), code))
        elif st == "NO_PV":
            nopv += 1
            ckpt[code] = {"status": "no_pv", "series": sname,
                          "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
            log("[%d/%d] %-16s 无 /pv/ 地址" % (i, len(todo), code))
        else:
            fail += 1
            ckpt[code] = {"status": "probe_fail", "series": sname,
                          "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
            log("[%d/%d] %-16s 探测失败" % (i, len(todo), code))
        save_ckpt(ckpt)

    log("DONE 成功 %d 失败 %d 无预告 %d | 用时 %.0f 分钟"
        % (ok, fail, nopv, (time.time() - t0) / 60))
    return 0


if __name__ == "__main__":
    sys.exit(main())
