# -*- coding: utf-8 -*-
"""把外部「LTX 链接一览表」CSV 并进 data/site/ltx.js（**只增不删**，与 ltx_fetch.py 同一套 schema）。

为什么需要：ltx.libray.workers.dev 的 api/items 只给站内整理的链接，用户从评论区/网盘整理的表里
常有站内没有的条目（同番号不同版本、不同哈希）。这一步把表里的链接补进 ltx.js，已存在的原样不动。

归属番号（决定写到哪个 key）按优先级：
  1) 「视频链接」列的 javdb id → movies.js 的 code（**最权威**，能纠文件名里的错号/漏前缀）
  2) 文件名片段的**字面命中**（试 带/去前导零、带/不带数字前缀、带/不带短横 几种拼法，命中现有 key 就用）
  3) **归一命中**：norm(字母段 + 去前导零数字) 命中 ltx.js → 沿用站内拼写，否则命中 movies.js → 沿用片库拼写
     （例：ipzz-037 / IPZZ37 / 0619snis933 / cawb00012hhb 各按位补零差异都并到同一个 key；
       300MIUM-1457 的字母段是 MIUM，仍能命中站内 key「300MIUM-1457」，前缀不会被丢）
  4) 归一后**撞冲突组**（库里同时有 'SERO-0211' 和 'SERO-211' 这种同 norm 不同号）→ **不猜、不新建**，
     列入「无法归属」交人工指定（挂了再摘就得改已有数据，违背「只增不删」）
  5) 库里彻底没有 → 按文件名建 key，并在报告里单列「新建 key（需人眼看一眼）」

去重按 **link 字符串**（和 ltx_fetch.py 一致）：表里已有、或这轮重复出现的链接，一条不写。
空链接行（只有 javdb 链接、没有任何 ed2k/magnet）整行丢弃，不留任何记录。
改前快照落 _backup/<日期>/，本轮新增明细落 _pipeline/<日期>/ltx_table_added.json（过程文件）。

用法：
  python ltx_merge_table.py --csv <一览表.csv> --dry-run   # 只报增量，不写盘
  python ltx_merge_table.py --csv <一览表.csv>             # 增量合并并写回 ltx.js
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
import csv, io, json, os, re, shutil, time

import win_utf8        # noqa: F401
import atomic_io       # noqa: E402
import ltx_fetch as L  # schema 只有一份：load_existing / parse_link / write_ltxjs / human

MOVIES_JS = os.path.join(ROOT, "data", "site", "movies.js")

# 文件名里的番号候选：字母段（前面可带数字前缀，如 300MIUM）+ 分隔 + 数字段
CAND_RE = re.compile(r"(\d*)([A-Za-z]{2,7})[-_ ]*(\d{2,6})", re.I)
# key 自身也要能归一（ltx.js 里混着 ipzz-037 / 28DDH-021 之类拼法）
KEY_RE = re.compile(r"^\s*(\d*)([A-Za-z]{2,7})[-_ ]*0*(\d{1,6})\s*$", re.I)


def norm(s):
    """'IPZZ-037' / 'ipzz37' / '0619snis933FHD' 的候选 -> ('SNIS', 933) 形态的键；抽不出就返回 None。"""
    m = KEY_RE.match(s.strip()) if s else None
    if not m:
        return None
    return (m.group(2).upper(), int(m.group(3)))


def load_json_var(path, var):
    t = io.open(path, encoding="utf-8").read()
    i = t.index("=", t.index(var)) + 1
    return json.JSONDecoder().raw_decode(t[i:].lstrip())[0]


def build_indexes(ltx, movies):
    """norm -> 站内拼写（ltx.js 优先，其次 movies.js）；同时收集冲突（同一 norm 多种拼写）与字面拼写表。

    冲突是真实存在的：ltx.js/movies.js 里同时有 'SERO-0211' 与 'SERO-211'、'390JAC-068' 与 'JAC-068'
    这类同 norm 不同号的 key（2026-11-06 实测 107 组）。归一命中但冲突的，一律不猜——交给字面命中或放弃，
    免得把链接挂到别的番号上（挂了再想摘就得动已有数据，违背「只增不删」）。
    """
    idx, conflict, exact = {}, {}, set()
    for src, keys in (("ltx", ltx), ("movies", movies)):
        for k in keys:
            exact.add(k.upper())
            n = norm(k)
            if not n:
                continue
            if n in idx and idx[n][1] != k:
                conflict.setdefault(n, set()).add(idx[n][1])
                conflict[n].add(k)
                if src == "ltx":                    # 站内拼写优先覆盖片库拼写
                    idx[n] = (src, k)
            elif n not in idx:
                idx[n] = (src, k)
    return idx, conflict, exact


def variants(pre, letters, digits):
    """文件名片段 -> 可能的站内拼写（带/去前导零、带/不带数字前缀、带/不带短横）。"""
    d = int(digits)
    pad = digits if len(digits) >= 3 else "%03d" % d
    out = []
    for head in ([pre + letters, letters] if pre else [letters]):
        out += [head + "-" + pad, head + "-" + digits, head + "-" + str(d), head + digits]
    return out


def code_from_name(fname):
    """文件名里找番号候选；优先返回能命中索引的（含带数字前缀的整体），否则返回字母段+数字段。"""
    hits = []
    for m in CAND_RE.finditer(fname or ""):
        pre, letters, digits = m.group(1), m.group(2).upper(), m.group(3)
        hits.append((pre, letters, digits))
    return hits


def resolve(row, idx, conflict, exact):
    """返回 (key, 依据)。key=None 表示无法归属（不写、不留记录）。"""
    conf_hit = None
    if row["jcode"]:
        return row["jcode"], "javdb"
    for pre, letters, digits in row["cands"]:
        hit = [v for v in variants(pre, letters, digits) if v.upper() in exact]
        if hit:
            return hit[0], "字面命中"
    for pre, letters, digits in row["cands"]:
        for cand in ([pre + letters, letters] if pre else [letters]):
            n = norm(cand + "-" + digits)
            if n in idx and n not in conflict:      # 冲突的 norm 不猜
                return idx[n][1], "index:" + idx[n][0]
            if n in conflict:
                conf_hit = n
    if conf_hit:                                     # 命中了多拼写冲突：既不猜也不新建第三个 key
        return None, "norm 冲突(需人工指定):" + "|".join(sorted(conflict[conf_hit]))
    for pre, letters, digits in row["cands"]:        # 建 key：字母段 + 数字（不足 3 位补零，站内惯例）
        d = int(digits)
        return "%s-%03d" % (letters, d) if d < 1000 else "%s-%d" % (letters, d), "新建"
    return None, "无候选"


def read_rows(path):
    rows = list(csv.reader(io.open(path, encoding="utf-8-sig", newline="")))
    hdr = [h.strip() for h in rows[0]]
    def col(*names):
        for n in names:
            if n in hdr:
                return hdr.index(n)
        return None
    i_link = col("链接")
    i_jav = col("视频链接", "javdb链接")
    i_date = col("评论日期", "日期")
    i_user = col("用户名")
    if i_link is None or i_jav is None:
        raise SystemExit("表头认不出来：期望含「链接」「视频链接」，实际 %r" % (hdr,))
    out = []
    for r in rows[1:]:
        r = list(r) + [""] * (len(hdr) - len(r))
        link = (r[i_link] or "").strip()
        javdb = (r[i_jav] or "").strip()
        jid = javdb.rstrip("/").rsplit("/", 1)[-1] if "javdb.com/" in javdb else ""
        name = ""
        if link.startswith("ed2k:"):
            segs = link.split("|")
            name = segs[2] if len(segs) > 2 else ""
        out.append({"link": link, "jid": jid, "name": name, "cands": code_from_name(name),
                    "date": (r[i_date] or "").strip() if i_date is not None else "",
                    "user": (r[i_user] or "").strip() if i_user is not None else ""})
    return out


def main():
    ap = argparse.ArgumentParser(description="外部 LTX 一览表 CSV 增量并入 ltx.js（只增不删）")
    ap.add_argument("--csv", required=True, help="一览表 CSV 路径（UTF-8，含「链接」「视频链接」列）")
    ap.add_argument("--dry-run", action="store_true", help="只报增量，不写盘/不备份")
    args = ap.parse_args()

    ltx = L.load_existing()
    movies = load_json_var(MOVIES_JS, "window.JAVDB_MOVIES")
    byid = {v.get("id"): k for k, v in movies.items() if isinstance(v, dict) and v.get("id")}
    idx, conflict, exact = build_indexes(ltx, movies)

    rows = read_rows(args.csv)
    have = {m["link"] for v in ltx.values() for m in v}
    stats = {"行": len(rows), "空链接(丢弃)": 0, "非链接格式": 0, "已有链接": 0, "表内重复": 0}
    todo, seen, unmatched = [], set(), []
    for r in rows:
        if not r["link"]:
            stats["空链接(丢弃)"] += 1
            continue
        if not (r["link"].startswith("ed2k:") or r["link"].startswith("magnet:")):
            stats["非链接格式"] += 1
            continue
        if r["link"] in have:
            stats["已有链接"] += 1
            continue
        if r["link"] in seen:
            stats["表内重复"] += 1
            continue
        seen.add(r["link"])
        r["jcode"] = byid.get(r["jid"], "")
        key, why = resolve(r, idx, conflict, exact)
        r["key"], r["why"] = key, why
        if not key:
            unmatched.append(r)
            continue
        todo.append(r)

    new_codes = [r["key"] for r in todo if r["key"] not in ltx]
    bywhy = {}
    for r in todo:
        bywhy[r["why"]] = bywhy.get(r["why"], 0) + 1
    print("表内 %d 行：已有链接 %d、空链接丢弃 %d、非链接 %d、表内重复 %d -> 待写 %d 条"
          % (stats["行"], stats["已有链接"], stats["空链接(丢弃)"], stats["非链接格式"], stats["表内重复"], len(todo)))
    print("归属依据:", bywhy)
    print("涉及番号 %d 个；其中 ltx.js 已有番号 %d、需新建 key %d"
          % (len({r["key"] for r in todo}), len({r["key"] for r in todo} - set(new_codes)), len(set(new_codes))))
    for k in sorted(set(new_codes)):
        print("  新建 key:", k, "<-", [r["name"][:40] or r["link"][:40] for r in todo if r["key"] == k][:2])
    if unmatched:
        print("无法归属（未写入）:", len(unmatched))
        for r in unmatched[:10]:
            print("   ", r["link"][:70], "| name=", r["name"][:40], "| javdb=", r["jid"], "|", r["why"])
    print("归一冲突 key 组数（同 norm 多拼写，全程不猜）:", len(conflict))

    if args.dry_run:
        print("[dry-run] 未写盘。")
        return 0
    if not todo:
        print("无增量，ltx.js 不变。")
        return 0

    merged = {c: list(v) for c, v in ltx.items()}
    added = []
    for r in todo:
        entry = L.parse_link(r["link"], r["key"])      # 字段与 ltx_fetch.py 完全一致，不额外加私有字段
        merged.setdefault(r["key"], []).append(entry)
        added.append({"code": r["key"], "link": r["link"], "name": entry["name"], "size": entry["size"],
                      "why": r["why"], "jid": r["jid"], "csv_date": r["date"], "user": r["user"]})
    bp = atomic_io.backup_path("ltx.js")
    if os.path.exists(bp):
        # 同一天已经跑过一步（比如上午刚跑 ltx_fetch.py）：备份名加时间戳，绝不覆盖当日已有的「改前快照」，
        # 否则那一步的 before 状态就找不回来了（2026-11-06 实测到这个坑）。
        bp = os.path.join(os.path.dirname(bp), "ltx_before_%s.js" % time.strftime("%H%M%S"))
    shutil.copy2(L.LTXJS, bp)
    print("已备份旧 ltx.js ->", bp)
    L.write_ltxjs(merged)
    rec = atomic_io.record_path("ltx_table_added.json")
    io.open(rec, "w", encoding="utf-8").write(json.dumps(added, ensure_ascii=False, indent=1))
    print("已写 data/site/ltx.js：新增链接 %d 条 / 番号 %d 个（新建 key %d）"
          % (len(added), len({a["code"] for a in added}), len(set(new_codes))))
    print("新增明细 ->", rec)
    print("⚠ 页面 ?v= 不刷新会吃缓存：跑一下 build_collection_js.bump_refs()")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
