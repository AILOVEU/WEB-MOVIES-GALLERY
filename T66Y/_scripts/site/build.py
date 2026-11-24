# -*- coding: utf-8 -*-
"""组装 T66Y 离线清单（输出全部落在 data/）：

  data/index.js       window.T66Y_META = {generated,fid,forum,total_pages,pages_done,count,
                                          pages_per_shard,shards,tags,page_min,page_max}
                      window.T66Y_PTAG = [ "0,3,5", ... ]   每页命中的标签序号（相对 page_min，逗号分隔）
  data/titles.js      window.T66Y_TITLES = "tid\\tpage\\ttitle\\n..."   全库标题索引（关键词搜索用，按需加载）
  data/shard_XXX.js   window.T66Y_PAGE["<page>"] = [ {tid,title,url,...tags} ]  每片 PAGES_PER_SHARD 页

为什么要三层而不是一个大文件：
  全库 4957 页 ≈ 49 万条，每条约 380 字节 ≈ 185 MB。一次性加载浏览器扛不住。
  所以：标签筛选先用 T66Y_PTAG（每页几十字节）算出候选页，只加载候选页所在分片；
  关键词搜索用 T66Y_TITLES（约 20 MB，只在真输入关键词时才拉）；
  默认视图只加载最新的前几片，滚动/翻页再继续。

分片按**页码**切（不是按 tid）：页码是天然的时间轴，第 1 页最新。
一片 50 页 = 5000 条，约 1.9 MB。

数据源是累积库 `_pipeline/records.json`（按 tid 唯一），不是直接扫 `_rawdata`：
  列表按发帖时间降序，新帖插最前、旧帖整体后移，按页覆盖会丢帖（详见 store.py）。
  页码在这里**按顺序重新分配**（虚拟页码），只用于前端分片定位，与抓取时的物理页码无关。

标签：标题关键词标签来自 _scripts/config/tags.json（tag_record），
结构化标签另加：置顶(stick)、锁帖(locked)、多页(multi)、新回复(new)。
标签在 build 阶段算，改 tags.json 重跑 build 即可，不用重抓。

去重：累积库本身按 tid 唯一，这里不再需要去重。
"""
import io
import json
import os
import shutil
import sys
import time

_SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_SCRIPTS, "common"))
import paths as P
import store as S
import t66y as T

FORUM_NAME = {25: "國產原創區", 2: "亞洲無碼原創區", 15: "亞洲有碼原創區",
              4: "歐美原創區", 5: "動漫原創區", 26: "中字原創區",
              28: "AI破解原創區", 21: "HTTP下載區"}

# 结构化标签（不来自 tags.json，由字段直接推出）
STRUCT_TAGS = [
    ("stick", "置頂", "#f0c060"),
    ("locked", "鎖帖", "#8a93a3"),
    ("multi", "多頁", "#5aa9ff"),
    ("new", "新回復", "#7dd3a0"),
]


def log(*a):
    print("[build]", *a, flush=True)


def load_pages(fid):
    """从累积库读记录并按顺序切成虚拟页码，返回 {page: [records]}。

    累积库不存在时自动从 _rawdata 初始化（首次运行/库被删）。
    """
    if not os.path.exists(P.RECORDS):
        log("累积库不存在，从 _rawdata 初始化 …")
        data, dup = S.init_from_raw(fid)
        if not data["records"]:
            return {}
        S.save(fid, data)
        log("已建立 %s：%d 条（去重丢 %d）" % (P.RECORDS, len(data["records"]), dup))
    else:
        data = S.load(fid)
    recs = list((data.get("records") or {}).values())
    if not recs:
        return {}
    return S.group_pages(recs)


def attach_tags(recs, tags):
    """给每条记录加 tags 字段（关键词标签 + 结构化标签）。"""
    for r in recs:
        ks = T.tag_record(r, tags)
        if r.get("stick"):
            ks.append("stick")
        if r.get("locked"):
            ks.append("locked")
        if r.get("pages", 0) > 1:
            ks.append("multi")
        if r.get("is_new"):
            ks.append("new")
        seen, out = set(), []
        for k in ks:
            if k not in seen:
                seen.add(k)
                out.append(k)
        r["tags"] = out
    return recs


def backup_data():
    """覆盖 data/ 前留一份快照到 _bak/YYYY-MM-DD/（同日多次跑只留最后一次，不堆文件）。"""
    if not os.path.isdir(P.DATA):
        return 0
    dst = P.backup_dir()
    P.ensure(dst)
    # 先清掉当日旧快照：同一天跑多次 build 只保留最近一次，否则一天堆几十个文件
    for fn in os.listdir(dst):
        if fn.endswith(".js"):
            try:
                os.remove(os.path.join(dst, fn))
            except OSError:
                pass
    n = 0
    for fn in os.listdir(P.DATA):
        if fn.endswith(".js"):
            shutil.copy2(os.path.join(P.DATA, fn), os.path.join(dst, fn))
            n += 1
    return n


def build(fid=25):
    tags = T.load_tags(P.TAGS_JSON)
    if not tags:
        log("警告：%s 读不到或为空，只会有结构化标签" % P.TAGS_JSON)

    pages = load_pages(fid)
    if not pages:
        log("累积库为空且 _rawdata 下没有 fid=%d 的可用页，先跑 crawl.py" % fid)
        return 1

    data = S.load(fid)
    wm = data.get("watermark") or {}

    # 累积库已按 tid 唯一，这里不再去重（虚拟页码由 store.group_pages 分配）
    dup = 0

    all_recs = [r for pg in sorted(pages) for r in pages[pg]]
    attach_tags(all_recs, tags)

    # 站点真实总页数：从任一原始页的分页框读（value="1/4957"），读不到就退回虚拟页数
    total_pages = 0
    if os.path.isdir(P.RAW):
        pat = "fid%d_p" % fid
        for fn in sorted(os.listdir(P.RAW)):
            if not (fn.startswith(pat) and fn.endswith(".html")):
                continue
            total_pages = T.page_count(
                io.open(os.path.join(P.RAW, fn), encoding="utf-8", errors="ignore").read())
            if total_pages:
                break
    if not total_pages:
        total_pages = max(pages)

    # ---- 标签表：tags.json 顺序 + 结构化标签，只保留有命中的 ----
    tag_count = {}
    for r in all_recs:
        for k in r["tags"]:
            tag_count[k] = tag_count.get(k, 0) + 1

    tag_meta = []
    for t in tags:
        if tag_count.get(t["key"]):
            tag_meta.append({"key": t["key"], "label": t.get("label") or t["key"],
                             "color": t.get("color") or "", "n": tag_count[t["key"]]})
    for k, label, color in STRUCT_TAGS:
        if tag_count.get(k):
            tag_meta.append({"key": k, "label": label, "color": color,
                             "n": tag_count[k], "struct": True})

    tag_index = {t["key"]: i for i, t in enumerate(tag_meta)}

    # ---- 每页标签位图（相对 page_min 的数组，每项是标签序号逗号串）----
    page_min = min(pages)
    page_max = max(pages)
    ptag = [""] * (page_max - page_min + 1)
    for pg in sorted(pages):
        idxs = sorted({tag_index[k] for r in pages[pg] for k in r["tags"] if k in tag_index})
        ptag[pg - page_min] = ",".join(str(i) for i in idxs)

    # ---- 落盘 ----
    P.ensure(P.DATA)
    n_bak = backup_data()

    # 分片
    buckets = {}
    for pg in sorted(pages):
        buckets.setdefault(P.shard_of_page(pg), {})[str(pg)] = pages[pg]

    shard_names = []
    total = 0
    for si in sorted(buckets):
        obj = buckets[si]
        total += sum(len(v) for v in obj.values())
        body = ("/* 自动生成，勿手改：T66Y 第 %d-%d 页 */\n"
                "window.T66Y_PAGE = window.T66Y_PAGE || {};\n"
                "window.T66Y_PAGE = Object.assign(window.T66Y_PAGE, %s);\n"
                % (si * P.PAGES_PER_SHARD + 1, (si + 1) * P.PAGES_PER_SHARD,
                   json.dumps(obj, ensure_ascii=False, separators=(",", ":"))))
        io.open(P.shard_path(si), "w", encoding="utf-8").write(body)
        shard_names.append("shard_%03d" % si)

    # 清掉不再有数据的分片
    keep = {"%s.js" % n for n in shard_names}
    removed = 0
    for fn in os.listdir(P.DATA):
        if fn.startswith("shard_") and fn.endswith(".js") and fn not in keep:
            try:
                os.remove(os.path.join(P.DATA, fn))
                removed += 1
            except OSError:
                pass

    # 标题索引：按分片切（每片约 300 KB），关键词搜索时渐进加载，
    # 不用一次拉 30 MB 的全库索引。
    title_files = []
    for si in sorted(buckets):
        lines = []
        for pg in sorted(buckets[si], key=lambda x: int(x)):
            for r in buckets[si][pg]:
                lines.append("%s\t%d\t%s" % (r["tid"], int(pg),
                                             (r["title"] or "").replace("\t", " ").replace("\n", " ")))
        body = ("/* 自动生成，勿手改：第 %d-%d 页标题索引 */\n"
                "window.T66Y_T = window.T66Y_T || {};\n"
                "window.T66Y_T[%d] = %s;\n"
                % (si * P.PAGES_PER_SHARD + 1, (si + 1) * P.PAGES_PER_SHARD, si,
                   json.dumps("\n".join(lines), ensure_ascii=False)))
        io.open(os.path.join(P.DATA, "titles_%03d.js" % si), "w", encoding="utf-8").write(body)
        title_files.append("titles_%03d" % si)

    # 清掉不再有数据的标题片，以及旧版单文件 titles.js
    keep_t = {"%s.js" % n for n in title_files}
    for fn in os.listdir(P.DATA):
        if fn == "titles.js" or (fn.startswith("titles_") and fn.endswith(".js") and fn not in keep_t):
            try:
                os.remove(os.path.join(P.DATA, fn))
            except OSError:
                pass

    meta = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "fid": fid,
        "forum": FORUM_NAME.get(fid, "fid=%d" % fid),
        # total_pages 是站点真实总页数（从分页框读），不是已抓的最大页码 ——
        # 两者混用会让界面显示「100/300 页」这种假进度。
        "total_pages": total_pages,
        "pages_done": len(pages),
        "page_min": page_min,
        "page_max": page_max,
        "count": total,
        "dedup_dropped": dup,
        "pages_per_shard": P.PAGES_PER_SHARD,
        "shards": shard_names,
        "title_shards": title_files,
        "tags": tag_meta,
        # 水位线：下一轮增量抓取遇到这条就停（store.py 维护）
        "watermark_tid": wm.get("tid") or "",
        "watermark_seen": wm.get("seen") or "",
    }
    io.open(P.DATA_INDEX, "w", encoding="utf-8").write(
        "/* T66Y 离线清单 · 自动生成，勿手改 */\n"
        "window.T66Y_META = " + json.dumps(meta, ensure_ascii=False, separators=(",", ":")) + ";\n"
        "window.T66Y_PTAG = " + json.dumps(ptag, ensure_ascii=False, separators=(",", ":")) + ";\n")

    log("data/index.js: %d 帖 / %d 页 / %d 片（去重丢 %d，清理旧片 %d，备份 %d 文件）"
        % (total, len(pages), len(shard_names), dup, removed, n_bak))
    log("data/titles_*.js: %d 片标题索引" % len(title_files))
    log("标签命中：%s" % " ".join("%s=%d" % (t["label"], t["n"]) for t in tag_meta))
    return 0


if __name__ == "__main__":
    fid = int(sys.argv[1]) if len(sys.argv) > 1 else 25
    sys.exit(build(fid))
