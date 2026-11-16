# -*- coding: utf-8 -*-
"""统计 --codes 命中清单：重复发布、与已下重叠、实际需新下的体积。"""
# [own] 分类=download 自写脚本（tg_video_dl 下载器，原属 a_TELE_TURRIT_APP/Tools/download，2026-11-03 迁出仓库），非 Telegram 上游
import json
import os
from collections import Counter

import tg_dl

os.chdir(r"D:\vid")
hits = []
for line in open("list_codes.log", encoding="utf-8"):
    if line.startswith("  msg "):
        p = line.split()
        hits.append((int(p[1]), float(p[2]), p[4]))

MP = tg_dl.manifest_path(tg_dl.OUT_DEFAULT)     # 已住 data/state/tg_manifest.json（不再跟着资源目录搬）
man = json.load(open(str(MP), encoding="utf-8"))
done_names = {v["name"] for v in man.values()}
done_msg = {v["msg"] for v in man.values()}

codes = Counter(os.path.splitext(f)[0] for _, _, f in hits)
dup = {c: n for c, n in codes.items() if n > 1}
print("命中 %d 条 | 去重后番号 %d 个 | 重复发布 %s" % (len(hits), len(codes), dup))
waste = sum(s for c, _, s, _ in [(os.path.splitext(f)[0], m, s, f) for m, s, f in hits]
            if c in dup) - sum(max(s for cc, _, s, _ in
                                   [(os.path.splitext(f)[0], m, s, f) for m, s, f in hits] if cc == c)
                               for c in dup)
print("重复条目多占约 %.1f MiB" % waste)

ov = [f for _, _, f in hits if f in done_names]
print("与已下 25 部同文件名重叠 %d 条: %s" % (len(ov), ov))

keep, seen_code = [], set()
for m, s, f in sorted(hits, key=lambda x: -x[0]):          # 由新到旧，保留首个
    c = os.path.splitext(f)[0]
    if c in seen_code:
        continue
    if m in done_msg:                                       # 同一消息已下过
        seen_code.add(c)
        continue
    seen_code.add(c)
    keep.append((m, s, f))
tot = sum(k[1] for k in keep)
print("实际需新下 %d 部，共 %.1f MiB = %.2f GiB" % (len(keep), tot, tot / 1024))
print("最大的若干部:", sorted([(s, f) for _, s, f in keep], reverse=True)[:5])
