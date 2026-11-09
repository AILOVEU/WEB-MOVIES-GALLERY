# -*- coding: utf-8 -*-
"""增量轮的回归基准（2026-11-02 从项目外的 _JAVBUS_BASELINE_20261031 吸收进来）。

validate.py 管的是"图片字节在不在、JS 语法对不对"；本工具管另一件更要紧的事：
**这一轮有没有动到历史**。累积库最怕的不是新帖抓不到，而是旧帖被改写/被重排/台账被清。

用法：
  python _scripts/inc/guard.py snap <label>     # 拍当前状态快照 -> _pipeline/baselines/<label>.json
  python _scripts/inc/guard.py diff <a> <b>     # 比对两份快照，硬判"只新增、历史不动"，结论另存一行到 _logs/<YYYYMMDD>_guard_history.log

update.py 每轮自动跑：snap pre -> 抓取/入库/构建 -> snap post -> diff pre post。

判 FAIL 的口径（只新增，历史不动）：
  1) index.json 历史条目消失            2) 历史条目内容被改写
  3) 历史条目相对顺序被打乱             4) imgmap 台账条目减少或历史 key 丢失
  5) imgmap 的 value 不再是 assets/images 前缀（= 台账被退回远程 URL，离线库当场退化）
  6) 原始件/派生件条目减少：_rawdata/posts、_rawdata/list_pN.html、_pipeline/parsed 文件数，data/posts 详情帖数（分片改造后文件数恒定，改判分片内 tid 行数）
  7) crawl_done 与 index 对称差不为空（抓了必须入库，入库必须有断点）
只报告不判失败：assets/images 的文件数/字节变化（你把整份复制走并删了本机图片目录，减少是预期）。

指纹口径沿用历史 baseline（别轻改，改了就和旧快照对不上）：
  md5( io.open(p, encoding="utf-8", errors="ignore").read().encode("utf-8", "ignore") )
"""
import hashlib
import io
import json
import os
import sys
import time

_SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # _scripts
sys.path.insert(0, os.path.join(_SCRIPTS, "common"))                      # 公共库：paths/discuz/pannet
import paths as P

try:  # Windows GBK 控制台无法编码中文，强制 stdout 为 UTF-8
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

LAYOUT = "v3"          # 快照口径版本；v1=项目外旧布局(v2 数据/根 data.js)，v2=一帖一文件 posts/<tid>.js；v3=详情分片 shard_<i>.js（data_posts 指标从"文件数"改判"分片内详情帖数"），三版互不可比


def fp(path):
    if not os.path.exists(path):
        return None
    return hashlib.md5(io.open(path, encoding="utf-8", errors="ignore").read().encode("utf-8", "ignore")).hexdigest()


def rel(p):
    return os.path.relpath(p, P.ROOT).replace("\\", "/")


def tree(path):
    """目录 -> (文件数, 总字节, 路径:大小 的排序指纹)；不存在返回 (0,0,"")。"""
    n = size = 0
    lines = []
    if os.path.isdir(path):
        for dirpath, _dirs, files in os.walk(path):
            for f in files:
                p = os.path.join(dirpath, f)
                s = os.path.getsize(p)
                n += 1
                size += s
                lines.append("%s:%d" % (rel(p), s))
    blob = hashlib.md5("\n".join(sorted(lines)).encode("utf-8", "ignore")).hexdigest() if lines else ""
    return n, size, blob


def data_threads():
    """data/posts/shard_*.js 里的详情帖数 = 各分片中以 window.THREADS[" 开头的行数之和。

    详情层从"一帖一文件"改成分片后，文件数恒定（SHARD_N 片），不再能反映历史有没有被动；
    分片内每个 tid 恰好一行赋值（build.py 的 SHARD 格式，json.dumps 单行不换行），故按行首前缀计数，
    正文里偶含同名串也因不在行首而不被误计。这是"详情条目只增不减"的回归信号。"""
    n = 0
    if os.path.isdir(P.DATA_POSTS):
        for f in os.listdir(P.DATA_POSTS):
            if not f.endswith(".js"):
                continue
            for line in io.open(os.path.join(P.DATA_POSTS, f), encoding="utf-8", errors="ignore"):
                if line.startswith('window.THREADS["'):
                    n += 1
    return n


def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        return json.load(io.open(path, encoding="utf-8"))
    except Exception:
        return default


def snap():
    label = sys.argv[2]
    rows = load_json(P.INDEX_JSON, [])
    per_row, order = {}, []
    for r in rows:
        tid = str(r.get("tid"))
        per_row[tid] = hashlib.md5(json.dumps(r, sort_keys=True, ensure_ascii=False).encode("utf-8", "ignore")).hexdigest()
        order.append(tid)
    done = sorted(set(io.open(P.CRAWL_DONE, encoding="utf-8").read().split())) if os.path.exists(P.CRAWL_DONE) else []
    imgmap = load_json(P.IMGMAP, {})
    bad_val = sorted(v for v in imgmap.values() if not str(v).startswith(P.IMG_REL + "/"))
    rn, rb, rf = tree(P.RAW_POSTS)
    ln, lb, lf = tree(P.RAW)                      # 含 list_pN.html（posts/ 也在这里，故另记 list 数）
    pn, pb, pf = tree(P.PARSED)
    dn, db, df = tree(P.DATA_POSTS)              # 分片：文件数恒定(≈SHARD_N)，字节/指纹仅作变更参考
    dt = data_threads()                          # 详情帖数 = 分片内赋值行数，"只增不减"的真信号
    imgn, imgb, imgf = tree(P.IMAGES)
    lists = sorted(x for x in (os.listdir(P.RAW) if os.path.isdir(P.RAW) else [])
                   if x.startswith("list_p") and x.endswith(".html"))
    rep = {
        "layout": LAYOUT,
        "label": label,
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "index_count": len(rows),
        "index_rows": per_row,
        "index_order": order,
        "crawl_done_count": len(done),
        "crawl_done": done,
        "imgmap_count": len(imgmap),
        "imgmap_keys": sorted(imgmap.keys()),
        "imgmap_bad_values": bad_val,
        "raw_posts_files": rn, "raw_posts_bytes": rb, "raw_posts_fp": rf,
        "list_pages": lists, "raw_root_files": ln, "raw_root_bytes": lb, "raw_root_fp": lf,
        "parsed_files": pn, "parsed_bytes": pb, "parsed_fp": pf,
        "data_posts_files": dn, "data_posts_bytes": db, "data_posts_fp": df, "data_posts_threads": dt,
        "images_files": imgn, "images_bytes": imgb, "images_fp": imgf,   # 只报告，不判失败
        "data_js_fp": fp(P.DATA_INDEX),
        "index_html_fp": fp(os.path.join(P.ROOT, "index.html")),
        "file_fp": {rel(p): fp(p) for p in [P.INDEX_JSON, P.IMGMAP, P.CRAWL_DONE, P.DATA_INDEX,
                                            os.path.join(P.ROOT, "index.html")] if os.path.exists(p)},
    }
    P.ensure(P.BASELINES)
    out = os.path.join(P.BASELINES, "%s.json" % label)
    json.dump(rep, io.open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("[guard] snap %s -> %s" % (label, out))
    print("[guard] index=%d crawl_done=%d imgmap=%d(异常值 %d) raw_posts=%d parsed=%d data_posts=%d帖 images=%d"
          % (rep["index_count"], rep["crawl_done_count"], rep["imgmap_count"], len(bad_val), rn, pn, dt, imgn))
    return 0


def hist(line):
    """结论另存一行流水到 _logs/<YYYYMMDD>_guard_history.log（日志区，删了不丢数据；按日分文件）。"""
    try:
        P.ensure(P.LOGS)
        with io.open(P.GUARD_LOG, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), line))
    except Exception:
        pass


def load_snap(label):
    p = os.path.join(P.BASELINES, "%s.json" % label)
    if not os.path.exists(p):
        print("[guard] 快照不存在：%s（先跑 guard.py snap %s）" % (p, label))
        sys.exit(2)
    d = json.load(io.open(p, encoding="utf-8"))
    if d.get("layout") != LAYOUT:
        print("[guard] 快照 %s 的口径是 layout=%s，与当前 v2 不可比（项目外旧目录那份就是 v1）"
              % (label, d.get("layout")))
        sys.exit(2)
    return d


def diff():
    a = load_snap(sys.argv[2])
    b = load_snap(sys.argv[3])
    old_ids, new_ids = set(a["index_rows"]), set(b["index_rows"])
    added = sorted(new_ids - old_ids, key=lambda t: b["index_order"].index(t) if t in b["index_order"] else 10 ** 9)
    removed = sorted(old_ids - new_ids)
    changed = sorted(t for t in old_ids & new_ids if a["index_rows"][t] != b["index_rows"][t])
    order_a = [t for t in a["index_order"] if t in new_ids]
    order_b = [t for t in b["index_order"] if t in old_ids]
    fail = []
    print("=" * 62)
    print("guard diff %s(%s) -> %s(%s)" % (sys.argv[2], a["at"], sys.argv[3], b["at"]))
    print("index.json : %d -> %d 条" % (a["index_count"], b["index_count"]))
    print("  新增 %d: %s" % (len(added), ",".join(added[:40]) + (" ..." if len(added) > 40 else "")))
    print("  消失 %d: %s" % (len(removed), ",".join(removed)))
    print("  历史条目被改写 %d: %s" % (len(changed), ",".join(changed)))
    print("  历史条目相对顺序保持: %s" % (order_a == order_b))
    if removed:
        fail.append("有历史条目从 index.json 消失")
    if changed:
        fail.append("历史条目内容被改写")
    if order_a != order_b:
        fail.append("历史条目顺序被打乱")

    d_old, d_new = set(a["crawl_done"]), set(b["crawl_done"])
    print("crawl_done : %d -> %d（新增 %d）" % (len(d_old), len(d_new), len(d_new - d_old)))
    if d_new - d_old:
        print("  本轮新抓完: %s" % ",".join(sorted(d_new - d_old)[:40]))

    print("imgmap 台账: %d -> %d（净 %+d）" % (a["imgmap_count"], b["imgmap_count"],
                                              b["imgmap_count"] - a["imgmap_count"]))
    lost_keys = sorted(set(a["imgmap_keys"]) - set(b["imgmap_keys"]))
    if b["imgmap_count"] < a["imgmap_count"]:
        fail.append("imgmap 台账条目减少（历史映射被清，旧文章图片会退回远程 URL）")
    if lost_keys:
        fail.append("imgmap 有历史 key 丢失 %d 条，例如 %s" % (len(lost_keys), ",".join(lost_keys[:5])))
    bad = b.get("imgmap_bad_values") or []
    print("  台账 value 前缀异常（非 %s/）: %d" % (P.IMG_REL, len(bad)))
    if bad:
        fail.append("台账里有 %d 条 value 不再是本地路径，例如 %s" % (len(bad), "; ".join(map(str, bad[:3]))))

    print("只增不减检查：")
    for key, name in (("raw_posts_files", "_rawdata/posts 原始 HTML"),
                      ("parsed_files", "_pipeline/parsed 解析件"),
                      ("data_posts_threads", "data/posts 详情帖数(分片内)")):
        print("  %-26s %d -> %d" % (name, a[key], b[key]))
        if b[key] < a[key]:
            fail.append("%s 条目数减少（%d -> %d）" % (name, a[key], b[key]))
    la, lb_ = set(a["list_pages"]), set(b["list_pages"])
    print("  %-26s %d -> %d" % ("_rawdata/list_pN.html", len(la), len(lb_)))
    if la - lb_:
        fail.append("列表页原始 HTML 丢失: %s" % ",".join(sorted(la - lb_)))

    sym = d_new ^ set(new_ids)
    print("crawl_done 与 index 对称差: %d" % len(sym))
    if sym:
        fail.append("抓了却没入库 / 入库了却没断点记录: %s" % ",".join(sorted(sym)[:20]))

    print("参考（不计失败）：assets/images 本机 %d 个/%d B -> %d 个/%d B%s"
          % (a["images_files"], a["images_bytes"], b["images_files"], b["images_bytes"],
             "  <- 本机图片被删属预期（人工整份复制到硬盘后删掉）"
             if b["images_files"] < a["images_files"] else ""))
    for k in sorted(set(a["file_fp"]) | set(b["file_fp"])):
        va, vb = a["file_fp"].get(k), b["file_fp"].get(k)
        if va != vb:
            print("  变更文件: %-28s %s -> %s" % (k, (va or "-")[:8], (vb or "-")[:8]))
    concl = "通过（只新增，历史不动）" if not fail else "不通过"
    print("\n[guard] 结论: %s" % concl)
    for f in fail:
        print("  ! " + f)
    print("=" * 62)
    hist("diff %s->%s index %d->%d imgmap %d->%d %s" % (sys.argv[2], sys.argv[3], a["index_count"],
                                                        b["index_count"], a["imgmap_count"], b["imgmap_count"], concl))
    return 1 if fail else 0


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("snap", "diff"):
        print(__doc__)
        return 2
    if sys.argv[1] == "snap":
        return snap()
    if len(sys.argv) < 4:
        print("用法: python _scripts/inc/guard.py diff <a> <b>")
        return 2
    return diff()


if __name__ == "__main__":
    sys.exit(main())
