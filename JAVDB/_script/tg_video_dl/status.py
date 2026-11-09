# -*- coding: utf-8 -*-
"""下载进度状态：按 manifest + 本批清单算完成量、百分比、近期速率与 ETA。

用法：python status.py [本批清单文件]   默认 D:/vid/list_codes2.log
"""
# [own] 分类=download 自写脚本（tg_video_dl 下载器，原属 a_TELE_TURRIT_APP/Tools/download，2026-11-03 迁出仓库），非 Telegram 上游
import json
import os
import sys
import time
from pathlib import Path

MIB = 1048576
import tg_dl
VID = Path(tg_dl.OUT_DEFAULT)

# 上一批（关注表 25 部）已完成的名字，用于区分"本批新增"
PREV = {"IPX-901.mp4", "SDDE-702.mp4", "JUQ-220.mp4", "URE-129.mp4", "JUR-558.mp4",
        "SDAM-046.mp4", "STARS-168.mp4", "SNIS-693.mp4", "SODS-065.mp4", "IPX-536.mp4",
        "SDDE-625.mp4", "SONE-385.mov", "IPX-023.mp4", "JUQ-209.mp4", "IPX-034.mp4",
        "SSNI-497.mp4", "MFYD-063.mp4", "MEYD-514.mp4", "START-353.mp4", "DLDSS-030.mp4",
        "JUQ-285.mp4", "ADN-499.mp4", "SSIS-858.mp4", "NDRA-094.mp4", "JUR-759.mp4"}


def main():
    # 本批清单（--list 的输出）可能落在旧目录，逐个候选路径取第一个存在的
    cands = ([Path(sys.argv[1])] if len(sys.argv) > 1 else []) + [
        VID / "list_codes2.log", Path(r"D:\vid") / "list_codes2.log"]
    plan = {}
    for lst in cands:
        if not lst.exists():
            continue
        for line in lst.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("  msg "):
                p = line.split()
                plan[p[4]] = (int(p[1]), float(p[2]) * MIB)     # 清单里的 MB 实为 MiB
        if plan:
            break
    mp = tg_dl.manifest_path(VID)      # 已改住 data/state/tg_manifest.json；老位置存在时会自动回退
    man = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else {}

    done = {v["name"]: v for v in man.values() if v["name"] in plan}
    inflight = sorted(VID.glob("*.part.p*"))
    cur_bytes = sum(f.stat().st_size for f in inflight)
    cur_name = inflight[0].name.split(".mp4")[0].split(".mov")[0] if inflight else ""

    plan_bytes = sum(s for _, s in plan.values())
    done_bytes = sum(v["size"] for v in done.values())
    got = done_bytes + cur_bytes
    all_bytes = sum(v["size"] for v in man.values())
    print("当前目录：%s" % VID)
    print("库内总计 %d 部 %.2f GiB（manifest %d 条）" % (len(man), all_bytes / 1048576 / 1024, len(man)))
    if plan:
        print("本批目标 %d 部 %.2f GiB" % (len(plan), plan_bytes / 1048576 / 1024))
    print("已完成   %d 部 %.2f GiB%s" % (len(done), done_bytes / 1048576 / 1024,
                                    "，正在下 %s" % cur_name if cur_name else ""))
    if cur_name:
        for f in inflight:
            print("     %-30s %7.1f MiB" % (f.name, f.stat().st_size / MIB))
    pct = got * 100.0 / plan_bytes if plan_bytes else 0
    print("总进度   %.1f%%" % pct if plan_bytes else "（无本批清单，不算百分比）")

    # 速率：直接量 30 秒内目录字节增长（mtime 推算会因合并后 mtime 继承而不准，曾虚高到 5 MB/s）
    def total():
        s = 0
        for f in VID.iterdir():
            if f.name == "_bad_slices" or f.is_dir():
                continue
            if f.suffix.lower() in (".mp4", ".mov", ".mkv", ".json") or ".part" in f.name:
                s += f.stat().st_size
        return s

    t0, a = time.time(), total()
    time.sleep(30)
    t1, b = time.time(), total()
    span = max(t1 - t0, 1)
    rate = (b - a) / 1024 / span
    print("实测速率：近 %.0f 秒增长 %.1f MiB -> %.0f KB/s" % (span, (b - a) / 1048576, rate))
    left = plan_bytes - got
    if rate > 0 and plan_bytes:
        print("剩余 %.2f GiB -> 约 %.1f 小时（%.0f 分钟）"
              % (left / 1048576 / 1024, left / 1024 / rate / 3600, left / 1024 / rate / 60))
    print("磁盘剩余 %.1f GB" % (shutil_free() / 1048576 / 1024))


def shutil_free():
    import shutil
    return shutil.disk_usage("D:/").free


main()
