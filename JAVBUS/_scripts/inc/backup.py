# -*- coding: utf-8 -*-
r"""累积库快照备份：所有备份文件统一落 `_backup/<YYYYMMDD>/`（2026-11-03 定）。

备份什么（只备"删了就得重爬/重算"的那几件，合计约 0.8 MB/天）：
    _pipeline/index.json        列表累积库（400 帖的顺序与元数据）
    _pipeline/imgmap.json       图片永久台账（URL -> assets/images/...）
    _pipeline/crawl_done.txt    抓取断点（决定下一轮哪些帖算"已抓过"）
    data/data.js                交付层列表（能从 parsed 重生成，但线上打开的就是它）
    index.html                  前端（画廊+详情同一文件，手改的，没有生成源）
    _scripts/store/store_config.json   存放策略配置

用法：
  python _scripts/inc/backup.py snap             # 建/刷新今天的快照（update.py 每轮自动跑）
  python _scripts/inc/backup.py list             # 各日期目录：文件数（含子目录）、体积、当时账号数
  python _scripts/inc/backup.py prune --keep=14  # 只保留最近 14 天（update.py 每轮自动跑）

回退办法（手工，先确认再动，别直接覆盖 _pipeline）：
  copy _backup\20261103\index.json      _pipeline\index.json
  copy _backup\20261103\imgmap.json     _pipeline\imgmap.json
  copy _backup\20261103\crawl_done.txt   _pipeline\crawl_done.txt

安全边界（照 external_store 的老规矩）：
  - 只写 `_backup/<YYYYMMDD>/`，从不碰 _pipeline / data / assets；
  - 已存在且内容相同的文件不重复写（幂等）；内容不同且同一天已存过 -> 旧的改名
    `<名字>@<HHMMSS>` 留住，再写新的（“写库前”那份不会被事后覆盖）；
  - prune 只删"目录名是纯 8 位数字 + 目录里有本工具写的 MANIFEST.json"的日子目录，
    其余一律跳过（所以手工放的归档不会被 prune 误删）；
  - 删除只用 os.remove + os.rmdir（非空即失败），全程无 rmtree；
  - 直接写目标文件，不用 tmp/os.replace（沙盒守卫会中断进程）。
"""
import hashlib
import io
import json
import os
import re
import sys
import time

_SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # _scripts
sys.path.insert(0, os.path.join(_SCRIPTS, "common"))                      # 公共库：paths.py
import paths as P

try:  # Windows GBK 控制台无法编码中文，强制 stdout 为 UTF-8
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DAY_RE = re.compile(r"^\d{8}$")
MANIFEST = "MANIFEST.json"
KEEP_DAYS = 14
# (项目相对路径, 落到备份目录里的文件名)
TARGETS = [
    ("_pipeline/index.json", "index.json"),
    ("_pipeline/imgmap.json", "imgmap.json"),
    ("_pipeline/crawl_done.txt", "crawl_done.txt"),
    ("data/data.js", "data.js"),
    ("index.html", "index.html"),
    ("_scripts/store/store_config.json", "store_config.json"),
]


def sha256_of(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def counts():
    """当前几个关键账号数，写进 MANIFEST 方便日后判断该回退到哪一天。"""
    idx = json.load(io.open(P.INDEX_JSON, encoding="utf-8")) if os.path.exists(P.INDEX_JSON) else []
    mapn = len(json.load(io.open(P.IMGMAP, encoding="utf-8"))) if os.path.exists(P.IMGMAP) else 0
    done = len(io.open(P.CRAWL_DONE, encoding="utf-8").read().split()) if os.path.exists(P.CRAWL_DONE) else 0
    return len(idx), mapn, done


def snap():
    out = P.backup_dir()
    P.ensure(out)
    rows, miss = [], []
    for rel, name in TARGETS:
        src = os.path.join(P.ROOT, *rel.split("/"))
        if not os.path.isfile(src):
            miss.append(rel)
            continue
        blob = open(src, "rb").read()
        digest = hashlib.sha256(blob).hexdigest()
        dst = os.path.join(out, name)
        if os.path.isfile(dst) and sha256_of(dst) == digest:
            rows.append((name, len(blob), "same"))
            continue
        if os.path.isfile(dst):
            # 同一天已经存过一份不一样的：先把旧的改名留住（index.json@104007），
            # 别把“写库前”那份覆盖掉——同日多次 snap 都得有迹可逆
            prev = dst + "@" + time.strftime("%H%M%S")
            os.rename(dst, prev)
            rows.append((os.path.basename(prev), os.path.getsize(prev), "kept-prev"))
        with open(dst, "wb") as f:
            f.write(blob)
        rows.append((name, len(blob), "written"))
    n_idx, n_map, n_done = counts()
    man = {
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tool": "backup.py",
        "index_count": n_idx,
        "imgmap_count": n_map,
        "crawl_done_count": n_done,
        "files": {n: {"bytes": b, "state": s} for n, b, s in rows},
    }
    io.open(os.path.join(out, MANIFEST), "w", encoding="utf-8").write(
        json.dumps(man, ensure_ascii=False, indent=1))
    print("[backup] snap -> %s   （%d 帖 / 台账 %d 条 / 断点 %d）" % (out, n_idx, n_map, n_done))
    for n, b, s in rows:
        print("   %-20s %8.1f KB  %s" % (n, b / 1024.0, s))
    for m in miss:
        print("   缺失未备：%s" % m)
    return 0


def days():
    if not os.path.isdir(P.BACKUP):
        return []
    return sorted(d for d in os.listdir(P.BACKUP)
                  if DAY_RE.match(d) and os.path.isdir(os.path.join(P.BACKUP, d)))


def lst():
    ds = days()
    if not ds:
        print("[backup] %s 里还没有日期目录，先跑 backup.py snap" % P.BACKUP)
        return 0
    tot = 0
    for d in ds:
        dd = os.path.join(P.BACKUP, d)
        top = [f for f in os.listdir(dd) if os.path.isfile(os.path.join(dd, f))]
        # 递归统计：手工归档目录（如 20261031/legacy_baseline/）里可能是一堆子目录
        allf = [os.path.join(r, f) for r, _dirs, fs in os.walk(dd) for f in fs]
        size = sum(os.path.getsize(p) for p in allf)
        tot += size
        man = {}
        if MANIFEST in top:
            try:
                man = json.load(io.open(os.path.join(dd, MANIFEST), encoding="utf-8"))
            except Exception:
                man = {}
        note = "" if MANIFEST in top else "   ← 无 MANIFEST，prune 不会碰它（手工归档，自己决定去留）"
        print("  %s  %2d 个文件（顶层 %d）  %6.1f MB   index=%s imgmap=%s done=%s%s"
              % (d, len(allf), len(top), size / 1048576.0, man.get("index_count", "?"),
                 man.get("imgmap_count", "?"), man.get("crawl_done_count", "?"), note))
    print("[backup] 共 %d 个日期目录 / %.1f MB" % (len(ds), tot / 1048576.0))
    return 0


def prune(keep):
    ds = days()
    if len(ds) <= keep:
        print("[backup] 现有 %d 个日期目录，未超保留上限 %d，无需清理" % (len(ds), keep))
        return 0
    gone = freed = 0
    for d in ds[:len(ds) - keep]:
        dd = os.path.join(P.BACKUP, d)
        if MANIFEST not in os.listdir(dd):
            print("[backup] 跳过 %s：没有 %s，不是本工具建的目录" % (d, MANIFEST))
            continue
        removed, skip = 0, False
        for f in os.listdir(dd):
            p = os.path.join(dd, f)
            if os.path.isdir(p):
                print("[backup] 跳过 %s：里面还有子目录 %s/（只删纯文件目录）" % (d, f))
                skip = True
                break
            freed += os.path.getsize(p)
            os.remove(p)
            removed += 1
        if skip:
            continue
        try:
            os.rmdir(dd)                     # 非空即失败，绝不递归
            gone += 1
            print("[backup] 清除旧快照 %s（%d 个文件）" % (d, removed))
        except OSError as e:
            print("[backup] %s 未删干净（保留）：%r" % (d, e))
    print("[backup] prune 完成：删除 %d 个目录 / 释放 %.1f MB，保留最近 %d 天" % (gone, freed / 1048576.0, keep))
    return 0


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "snap"
    keep = KEEP_DAYS
    for a in sys.argv[2:]:
        if a.startswith("--keep="):
            keep = int(a.split("=", 1)[1])
    P.ensure(P.BACKUP)
    if cmd == "snap":
        return snap()
    if cmd == "list":
        return lst()
    if cmd == "prune":
        return prune(keep)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
