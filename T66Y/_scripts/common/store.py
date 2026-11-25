# -*- coding: utf-8 -*-
"""records.json —— 按 tid 唯一的累积库（增量抓取的合并目标 + build 的唯一数据源）。

为什么不再直接扫 _rawdata 组装：
  列表按**发帖时间降序**（置顶帖除外），新帖插在最前面，旧帖整体后移 K 位。
  只重抓前几页会让后面的页错位：旧 p1 的尾部 K 条会被新 p2 的开头覆盖，直接丢帖。
  实测（2026-11-24）：p1 从 11-24 04:20 递减到 11-23 18:12，p2 接 11-23 17:00 递减，
  p3 接 11-23 11:40 —— 是严格的时间轴，不是"最后回复"顺序。

  所以数据按 tid 累积，页码在 build 阶段按顺序重新分配（**虚拟页码**），
  物理页码只用于抓取时定位，不再进入交付层。前端不需要改：对它来说
  页码仍是 1..N 连续整数，第 1 页最新。

结构：
  {
    "fid": 25,
    "updated": "YYYY-MM-DD HH:MM:SS",
    "watermark": {"tid": "...", "post_ts": 0, "page": 1, "seen": "..."},
    "records": {"<tid>": {...}}
  }

watermark = 上次抓取时列表里最新的那条（post_ts 最大、非置顶）。
下一轮抓取从 p1 往下翻，遇到它就说明后面的都已获取，可以停。
"""
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths as P
import t66y as T

# 每页多少条。站点实测每页 99 条（含置顶），虚拟页码沿用同一口径。
PAGE_SIZE = 99


def empty(fid):
    return {"fid": fid, "updated": "", "watermark": {}, "records": {}}


def load(fid):
    """读累积库。不存在或损坏返回空库（不抛，让调用方决定是否初始化）。"""
    path = P.RECORDS
    if not os.path.exists(path):
        return empty(fid)
    try:
        d = json.load(io.open(path, encoding="utf-8"))
    except Exception as e:
        print("[store] 警告：%s 解析失败（%s），按空库处理" % (path, e), flush=True)
        return empty(fid)
    if not isinstance(d, dict) or "records" not in d:
        return empty(fid)
    d.setdefault("fid", fid)
    d.setdefault("watermark", {})
    d.setdefault("records", {})
    return d


def save(fid, data):
    """直接写目标文件（沙盒守卫：不用 tmp/os.replace/.part）。"""
    P.ensure(P.WORK)
    data["fid"] = fid
    data["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    io.open(P.RECORDS, "w", encoding="utf-8").write(body)
    return len(body)


def sort_key(r):
    """交付层排序：置顶在前（stick 大的在前），其余按发帖时间降序，同时间按 tid 降序。

    置顶帖 post_ts 很老（2006/2011），不能参与时间排序，否则会沉到最后一页。
    """
    stick = r.get("stick") or 0
    post_ts = r.get("post_ts") or 0
    try:
        tid = int(r.get("tid") or 0)
    except (TypeError, ValueError):
        tid = 0
    return (-stick, -post_ts, -tid)


def merge(data, new_recs):
    """把新抓到的记录合并进累积库。

    - 新 tid：插入
    - 旧 tid：**保留库里原样，不覆盖**（2026-11-25 用户决定：不要计数更新）
    返回 (新增数, 跳过数)。

    为什么不再覆盖旧记录：
      旧实现用新抓到的回复数/下载数/最后回复覆盖库里的值，一轮下来 60~70 条
      「更新」全是这类计数变动，对清单页没有意义，还会让 records.json 每轮都变。
      现在只认新帖，已入库的 tid 一律不动 —— 重抓同一页是幂等的，库只会增长。
      代价：旧帖的回复数/最后回复停在首次入库那一刻，不再刷新。
    """
    recs = data["records"]
    added = skipped = 0
    for r in new_recs:
        tid = r.get("tid")
        if not tid:
            continue
        if tid in recs:
            skipped += 1
            continue
        added += 1
        recs[tid] = r
    return added, skipped


def pick_watermark(recs):
    """从一批记录里挑出"最新的一条"：非置顶、post_ts 最大。用作下一轮的停止标志。"""
    best = None
    for r in recs:
        if r.get("stick"):
            continue
        if not r.get("post_ts"):
            continue
        if best is None or r["post_ts"] > best["post_ts"]:
            best = r
    if not best:
        return {}
    return {"tid": best["tid"], "post_ts": best["post_ts"],
            "page": best.get("page") or 0,
            "seen": time.strftime("%Y-%m-%d %H:%M:%S")}


def init_from_raw(fid):
    """首次运行：从 _rawdata 现有页初始化累积库（按 tid 去重，保留页码最小的那条）。

    只在 records.json 不存在时调用。之后一律走增量合并。
    """
    raw = P.RAW
    if not os.path.isdir(raw):
        return empty(fid), 0
    pat = "fid%d_p" % fid
    seen = {}
    dup = 0
    for fn in sorted(os.listdir(raw)):
        if not (fn.startswith(pat) and fn.endswith(".html")):
            continue
        m = fn[len(pat):-5]
        if not m.isdigit():
            continue
        page = int(m)
        html = io.open(os.path.join(raw, fn), encoding="utf-8", errors="ignore").read()
        for r in T.parse_list_page(html, page):
            tid = r["tid"]
            if tid in seen:
                dup += 1
                continue
            seen[tid] = r
    data = empty(fid)
    data["records"] = seen
    data["watermark"] = pick_watermark(seen.values())
    return data, dup


def group_pages(recs, page_size=PAGE_SIZE):
    """把记录按顺序切成虚拟页码 -> {page: [recs]}。同时把 page 字段写回每条记录。"""
    ordered = sorted(recs, key=sort_key)
    pages = {}
    for i, r in enumerate(ordered):
        pg = i // page_size + 1
        r["page"] = pg
        pages.setdefault(pg, []).append(r)
    return pages


def stats(data):
    recs = data.get("records") or {}
    wm = data.get("watermark") or {}
    return {
        "records": len(recs),
        "watermark_tid": wm.get("tid") or "",
        "watermark_seen": wm.get("seen") or "",
        "updated": data.get("updated") or "",
    }
