# -*- coding: utf-8 -*-
"""解析已抓 HTML -> 结构化数据。

产出（全部在过程区 _pipeline/，删了要重算）：
  _pipeline/index.json         列表元数据累积库（按抓取顺序去重、新帖插最前），供画廊列表用
  _pipeline/parsed/{tid}.json  每帖完整数据：article + comments(楼层) + 图片远端URL + 头像
  _pipeline/last_new.txt       本轮新增 tid（update.py / validate.py 跨步骤读它）
读入：原始抓取件 _rawdata/list_pN.html 与 _rawdata/posts/<tid>_pN.html（只读，不写回）。

不再生成 _pipeline/images.json：它早年给 images.py 当下载清单，现在 images.py 直接
扫 parsed/（gather），那个文件写了没人读，属死输出（2026-11-03 去掉）。
页数口径：pages_fetched 只统计“真的带来新楼层的页”——站点把越界请求钳制回末页时，
那一页的 pid 全是重复的，不算一页（旧口径会多记 1 页，导致前端出现“2 页/4 楼”这种矛盾）。

幂等：纯读取磁盘 HTML 重新生成 JSON，可反复跑。
备份：累积库快照不在本脚本里做，走 `python _scripts/inc/backup.py snap`（写 `_backup/YYYYMMDD/`）。
"""
import argparse
import glob
import io
import json
import os
import re
import sys

_SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # _scripts
sys.path.insert(0, os.path.join(_SCRIPTS, "common"))                      # 公共库：paths/discuz/pannet
import discuz as D
import paths as P

ROOT = P.ROOT
RAW_DIR = P.RAW                 # 原始抓取件（只读）
POSTS_DIR = P.RAW_POSTS         # 帖子页原始 HTML
PARSED_DIR = P.PARSED           # 过程区：_pipeline/parsed
INDEX_JSON = P.INDEX_JSON
LAST_NEW = P.LAST_NEW


def read(p):
    return io.open(p, encoding="utf-8", errors="ignore").read()


def build_index(pages=10):
    metas, seen = [], {}
    for p in range(1, pages + 1):
        path = os.path.join(RAW_DIR, "list_p%d.html" % p)
        if not os.path.exists(path):
            continue
        for r in D.parse_list_page(read(path)):
            if r["tid"] in seen:
                seen[r["tid"]]["list_pages"].append(p)
                continue
            r["list_pages"] = [p]
            seen[r["tid"]] = r
            metas.append(r)
    return metas


def parse_thread(tid):
    files = sorted(glob.glob(os.path.join(POSTS_DIR, "%s_p*.html" % tid)),
                   key=lambda f: int(re.search(r"_p(\d+)\.html", f).group(1)))
    by_pid, order = {}, []
    real_pages = 0          # 只计“带来新楼层”的页：越页被钳制回来的重复页不算
    for f in files:
        new_in_page = 0
        for post in D.parse_thread_page(read(f))["posts"]:
            pid = post["pid"]
            if pid not in by_pid:
                by_pid[pid] = post
                order.append(pid)
                new_in_page += 1
        if new_in_page:
            real_pages += 1
    posts = [by_pid[p] for p in order]
    if not posts:
        return None
    # 楼层按 pid 升序（时间序，Discuz pid 自增）
    posts.sort(key=lambda x: int(x["pid"]))
    for i, post in enumerate(posts):
        post["floor"] = i + 1
    article = posts[0]
    article["is_first"] = True
    comments = posts[1:]
    images = []
    iseen = set()
    for post in posts:
        for u in post["images"]:
            if u not in iseen:
                iseen.add(u); images.append(u)
    return {"tid": tid, "post_count": len(posts), "pages_fetched": real_pages,
            "pages_downloaded": len(files),
            "article": article, "comments": comments, "images": images}


def load_store():
    """读累积库 index.json（历次抓取的全部帖，按“新在前”排序）。"""
    p = INDEX_JSON
    if os.path.exists(p):
        try:
            return json.load(io.open(p, encoding="utf-8"))
        except Exception:
            log("WARN index.json 损坏，原件存进 _backup/<日期>/ 后重建")
            try:
                P.ensure(P.backup_dir())
                dst = os.path.join(P.backup_dir(), "index.json.corrupt")
                with open(p, "rb") as fsrc, open(dst, "wb") as fdst:
                    fdst.write(fsrc.read())
                log("     已存：", dst)
            except Exception:
                pass
    return []


def merge_store(current, store):
    """新帖(当前列表里 store 没过的)插到最前；已存在的保留原条目与位置(不更新)。返回 (merged, new_tids)。"""
    have = {t["tid"] for t in store}
    new = [t for t in current if t["tid"] not in have]
    return new + store, [t["tid"] for t in new]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true", help="不累积，仅用当前列表重建 index.json")
    ap.add_argument("--pages", type=int, default=10,
                    help="读 list_p1..pN.html 入库；必须和 crawl.py list --pages 一致，否则多抓的页等于白抓")
    a = ap.parse_args()
    P.ensure(PARSED_DIR)
    current = build_index(pages=a.pages)
    if a.rebuild:
        merged, new_tids = current, [t["tid"] for t in current]
    else:
        store = load_store()
        merged, new_tids = merge_store(current, store)
        # 不在这里做滚动备份了：快照统一由 _scripts/inc/backup.py 按日期写 `_backup/YYYYMMDD/`，
        # update.py 每轮在写库前自动跑一次（避免同一堆数据两份备份路径、两套命名）。
    # 帖内引用补录：有原始 HTML、但从未出现在列表页的帖（crawl.py refs 抓回来的），
    # 元数据从帖子页自己解析；拿不到标题（“提示信息”页 = 帖已删）就跳过，不造假条目。
    seen = {t["tid"] for t in merged}
    extra = []
    for f in sorted(glob.glob(os.path.join(POSTS_DIR, "*_p1.html"))):
        tid = os.path.basename(f).split("_p")[0]
        if tid in seen:
            continue
        meta = D.parse_thread_meta(read(f))
        if meta["tid"] != tid or not meta["title"]:
            continue
        data = parse_thread(tid)
        meta["replies"] = max((data["post_count"] - 1) if data else 0, 0)
        meta.update(avatar="", list_pages=[], via="post_link")   # via 只留数据层，前端不打标
        extra.append(meta)
    if extra:
        merged = merged + extra            # 引用帖多是大年前的老帖，排末尾，不冒充最新
        new_tids = new_tids + [m["tid"] for m in extra]
        log("帖内引用补录 %d 帖：%s" % (len(extra), ", ".join(m["tid"] for m in extra[:12])
                                       + (" ..." if len(extra) > 12 else "")))
    io.open(INDEX_JSON, "w", encoding="utf-8").write(
        json.dumps(merged, ensure_ascii=False, indent=1))
    log("index: 累积 %d 帖（本次列表 p1-p%d 共 %d 帖，新增 %d）"
        % (len(merged), a.pages, len(current), len(new_tids)))

    images = set()
    parsed_n = 0
    for m in merged:
        tid = m["tid"]
        if not glob.glob(os.path.join(POSTS_DIR, "%s_p*.html" % tid)):
            continue
        data = parse_thread(tid)
        if not data:
            continue
        io.open(os.path.join(PARSED_DIR, "%s.json" % tid), "w", encoding="utf-8").write(
            json.dumps(data, ensure_ascii=False))
        parsed_n += 1
        images.update(data["images"])
        # 头像按策略不抓取，也不再汇总 avatars 清单（images.json 已停产）
    log("parsed %d threads; content images=%d" % (parsed_n, len(images)))
    # 供 update.py 读取本次新增数
    io.open(LAST_NEW, "w", encoding="utf-8").write("\n".join(new_tids))


def log(*a):
    print("[parse]", *a, flush=True)


if __name__ == "__main__":
    main()
