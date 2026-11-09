# -*- coding: utf-8 -*-
"""
s07_migrate_years.py — 旧原料布局 → 年度一篇一容器 的一次性转换（years.py 的落地器）

旧布局（每篇 2~4 个文件）                    新布局（每篇 1 个文件，按年自包含）
  _rawdata/html/<k2>/<k>.html            →        data/<年>/material/<k>.jsonl   的 raw 行（最后一行）
  _pipeline/parsed/<k2>/<k>.json          →        同文件的 parsed 行（第 2 行）
  _pipeline/subpages/<k2>/<k>__pN.html    →        同文件的 sub 行（0..N 行）
年 = parsed.date 的前 4 位；取不到日期落 undated 槽。

安全设计：
- 默认**试算**（只读、只统计、不写）。真写要 `--apply`。
- 转换只**新建** `data/<年>/material/*.jsonl`，不删不改任何旧文件；旧目录的去留由 `--retire` 单独决定，
  且 `--retire` 只是把旧目录整体改名到 `_pipeline/_old_layout_<日期>/`（同卷 rename，瞬时、可逆）。
- 写完自带校验（数量对账 + 抽样逐字节比对），不通过就打印 FAIL 并非 0 退出，绝不自动 retire。

用法：
  _scripts\\py.cmd _scripts\\build\\s07_migrate_years.py                 # 试算：报将要转多少篇、各年分布
  _scripts\\py.cmd _scripts\\build\\s07_migrate_years.py --apply         # 真转换 + 校验
  _scripts\\py.cmd _scripts\\build\\s07_migrate_years.py --verify        # 只跑校验（幂等，可反复）
  _scripts\\py.cmd _scripts\\build\\s07_migrate_years.py --retire        # 校验通过后，把旧目录移入 _pipeline/_old_layout_*
"""
import glob
import io
import json
import os
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # _scripts/
import common as C
import years as Y

ROOT = C.ROOT
SRC = C.SRC
HTML_DIR = C.LEGACY_HTML_DIR        # 旧布局原始 HTML：_rawdata/html（已不存在，只供回退读）
PARSED_DIR = C.LEGACY_PARSED_DIR    # 旧布局解析产物：_pipeline/parsed
SUB_DIR = C.LEGACY_SUBPAGE_DIR      # 旧布局子页：_pipeline/subpages
SAMPLES = 300


def legacy_index():
    """key → (parsed 路径, html 路径, [子页路径…])。"""
    idx = {}
    for p in glob.glob(os.path.join(PARSED_DIR, "*", "*.json")):
        key = os.path.basename(p)[:-5]
        idx[key] = {"parsed": p, "html": os.path.join(HTML_DIR, key[:2], key + ".html"), "subs": []}
    for p in glob.glob(os.path.join(SUB_DIR, "*", "*.html")):
        b = os.path.basename(p)[:-5]
        if "__p" not in b:
            continue
        key, n = b.split("__p", 1)
        if key in idx:
            idx[key]["subs"].append((int(n), p))
    for k in idx:
        idx[k]["subs"].sort()
    return idx


def convert_one(key, rec, report):
    """一篇：读旧三处 → 组装容器 → 写 data/<年>/material/<key>.jsonl。"""
    art = C.read_json(rec["parsed"], None)
    if not art or not art.get("url"):
        report["no_parsed"] += 1
        return False
    url = art["url"]
    slot = Y.year_from_date(art.get("date"))
    raw = None
    if os.path.exists(rec["html"]):
        try:
            with io.open(rec["html"], "r", encoding="utf-8", errors="replace") as f:
                raw = f.read()
        except OSError:
            raw = None
    else:
        report["no_html"] += 1
    subs = []
    for n, p in rec["subs"]:
        try:
            with io.open(p, "r", encoding="utf-8", errors="replace") as f:
                subs.append({"t": "sub", "n": n, "html": f.read()})
        except OSError:
            report["sub_read_fail"] += 1
    meta = {"t": "meta", "url": url, "key": key, "year": slot,
            "date": art.get("date", ""),
            "stamp": {"html": "migrated", "parse": "migrated"},
            # 跟 years._set_flags 同口径，让后续脚本的存在性判定/统计只读 meta 行
            "has_raw": raw is not None, "has_parsed": True, "n_sub": len(subs),
            "n_comments": len(art.get("comments") or []),
            "n_images": len(art.get("images") or [])}
    p2 = {"t": "parsed"}
    for _k, _v in art.items():
        if _k != "t":
            p2[_k] = _v
    p2.pop("html_file", None)
    path = Y.material_path(slot, key)
    d = os.path.dirname(path)
    if not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(Y._dump_lines(meta, p2, subs, raw))
    os.replace(tmp, path)
    report["done"] += 1
    report["years"][slot] += 1
    return True


def verify():
    """对账 + 抽样逐字节比对。返回 (ok, 明细 dict)。"""
    idx = legacy_index()
    yc = Y.counts()
    n_cont = sum(yc.values())
    detail = {"legacy": len(idx), "containers": n_cont, "by_year": yc}
    Y.forget_index()

    # 1) 数量与集合：容器 key 集合必须等于旧 parsed key 集合
    new_keys = set(k for k, _p in Y._index().items())
    old_keys = set(idx)
    detail["missing_in_new"] = sorted(old_keys - new_keys)[:10]
    detail["extra_in_new"] = sorted(new_keys - old_keys)[:10]
    ok = not (old_keys - new_keys) and len(new_keys) == len(old_keys)

    # 2) 每容器必须有 parsed 段（url 非空）
    bad_parsed = 0
    for key, (_slot, path) in Y._index().items():
        if not Y.read_parsed(path=path):
            bad_parsed += 1
    detail["containers_without_parsed"] = bad_parsed
    ok = ok and bad_parsed == 0

    # 3) 抽样逐字节比对 parsed.content_html / raw / 子页数
    import random
    keys = sorted(old_keys)
    random.seed(42)
    samp = random.sample(keys, min(SAMPLES, len(keys)))
    mismatch = []
    for key in samp:
        rec = idx[key]
        slot, path = Y.locate(key)
        if not path or not os.path.exists(path):
            mismatch.append((key, "no-container"))
            continue
        old_art = C.read_json(rec["parsed"], {}) or {}
        new_art = Y.read_parsed(path=path) or {}
        if (old_art.get("content_html") or "") != (new_art.get("content_html") or ""):
            mismatch.append((key, "content_html"))
        if len(old_art.get("pages") or []) != len(new_art.get("pages") or []):
            mismatch.append((key, "pages_count"))
        if len(old_art.get("comments") or []) != len(new_art.get("comments") or []) \
                or len(old_art.get("images") or []) != len(new_art.get("images") or []):
            mismatch.append((key, "comments_or_images"))
        if os.path.exists(rec["html"]):
            with io.open(rec["html"], "r", encoding="utf-8", errors="replace") as f:
                if f.read() != Y.read_raw(path=path):
                    mismatch.append((key, "raw_html"))
        if len(rec["subs"]) != len(Y.read_subpages(path=path)):
            mismatch.append((key, "subpages_count"))
    detail["sampled"] = len(samp)
    detail["sample_mismatch"] = mismatch[:12]
    ok = ok and not mismatch
    return ok, detail


def retire():
    """把旧目录整体移入 _pipeline/_old_layout_<日期>/（同卷 rename，可逆）。"""
    stamp = time.strftime("%Y%m%d")
    dst = os.path.join(SRC, "_old_layout_" + stamp)
    if not os.path.isdir(dst):
        os.makedirs(dst, exist_ok=True)
    moved = []
    for name in ("html", "parsed", "subpages"):
        src = os.path.join(SRC, name)
        if not os.path.isdir(src):
            continue
        tgt = os.path.join(dst, name)
        if os.path.exists(tgt):
            C.log("[s07] 目标已存在，跳过：%s" % tgt)
            continue
        os.rename(src, tgt)
        moved.append(name)
    C.log("[s07] 旧目录已移入（可逆）：%s → %s" % ("/".join(moved), dst))
    return moved


def main():
    apply_it = "--apply" in sys.argv
    verify_only = "--verify" in sys.argv
    retire_it = "--retire" in sys.argv

    if retire_it:
        ok, detail = verify()
        if not ok:
            C.log("[s07] 拒绝 retire：校验未过 %s" % json.dumps(detail, ensure_ascii=False)[:600])
            return 1
        retire()
        print("S07_RETIRED")
        return 0

    idx = legacy_index()
    if not idx:
        C.log("[s07] 旧布局已空（_pipeline/parsed 无分片），无事可做")
        print("S07_NOTHING")
        return 0

    if verify_only:
        ok, detail = verify()
        C.log("[s07] 校验 %s：%s" % ("通过" if ok else "不通过",
                                     json.dumps(detail, ensure_ascii=False)))
        print("S07_VERIFY_%s" % ("OK" if ok else "FAIL"))
        return 0 if ok else 1

    # 试算：各年分布与体量
    per_year = Counter()
    for key, rec in idx.items():
        art = C.read_json(rec["parsed"], {}) or {}
        per_year[Y.year_from_date(art.get("date"))] += 1
    C.log("[s07] 待转 %d 篇 | 年份分布 %s" % (len(idx), sorted(per_year.items())))
    if not apply_it:
        C.log("[s07] 试算结束（加 --apply 才写盘）。写盘只新建 data/<年>/material/*.jsonl，不动旧文件")
        print("S07_DRY %d" % len(idx))
        return 0

    import threading
    report = {"done": 0, "no_parsed": 0, "no_html": 0, "sub_read_fail": 0, "years": Counter()}
    lock = threading.Lock()
    t0 = time.time()
    items = sorted(idx.items())
    with ThreadPoolExecutor(max_workers=16) as ex:
        futs = [ex.submit(convert_one, k, r, report) for k, r in items]
        for i, f in enumerate(futs):
            f.result()
            if (i + 1) % 1000 == 0:
                with lock:
                    C.log("[s07] 已转 %d/%d (%.0fs)" % (i + 1, len(items), time.time() - t0))
    Y.forget_index()
    C.log("[s07] 转换完成：%d 篇，无 parsed %d，无 html %d，子页读失败 %d，用时 %.0fs"
          % (report["done"], report["no_parsed"], report["no_html"],
             report["sub_read_fail"], time.time() - t0))
    C.log("[s07] 各年落盘 %s" % sorted(report["years"].items()))

    ok, detail = verify()
    C.log("[s07] 校验%s：%s" % ("通过" if ok else "不通过", json.dumps(detail, ensure_ascii=False)[:1200]))
    if not ok:
        print("S07_VERIFY_FAIL")
        return 1
    print("S07_MIGRATED %d" % report["done"])
    C.log("[s07] 下一步：_scripts\\py.cmd _scripts\\build\\rebuild.py --check 看年维度计数，"
          "再决定 --retire 把旧目录移入 _pipeline/_old_layout_*")
    return 0


if __name__ == "__main__":
    sys.exit(main())
