# -*- coding: utf-8 -*-
"""流水线步骤 0：把当前数据备份到 `_backup/<当天日期>/`

约定（本项目）：备份前先建当天日期目录（YYYY-MM-DD），备份件放进该目录并保留原名，
例如 `_backup/2026-11-03/data.js.pre-update.bak`。

备份对象（与 data.js 一起构成可回退/可比对的完整快照）:
  data/data.js                    -> data.js.pre-update.bak
  _rawdata/_raw_posts.json        -> _raw_posts.json.pre-update.bak
  _rawdata/details.json           -> details.json.pre-update.bak
  _rawdata/magnets.json           -> magnets.json.pre-update.bak
同时在同目录写 `manifest.txt`（文件清单 + 大小 + md5 + 源路径），便于日后确认备份时刻的状态。

用法:
  python _scripts/tools/backup_data.py                        # 备份到 _backup/<今天>/
  python _scripts/tools/backup_data.py --day=2026-11-01       # 指定日期目录（补档/重命名用）
  python _scripts/tools/backup_data.py --reason=增量更新       # 写进 manifest 的说明
  python _scripts/tools/backup_data.py --dir=<画廊目录>
"""
import hashlib
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # -> _scripts/（common.py 所在）
from common import (gallery_dir, datajs_path, meta_file, backup_dir,
                    atomic_write, arg_val)

GDIR = gallery_dir()
DAY = arg_val("--day=") or time.strftime("%Y-%m-%d")
REASON = arg_val("--reason=") or "pre-update"
NOW = time.strftime("%Y-%m-%d %H:%M:%S")

if not DAY.replace("-", "").isdigit() or len(DAY) != 10:
    raise SystemExit("--day 需为 YYYY-MM-DD 格式, 实际收到: %r" % DAY)
try:
    time.strptime(DAY, "%Y-%m-%d")
except ValueError:
    raise SystemExit("--day 不是有效日期: %r（应为 YYYY-MM-DD）" % DAY)

DST = backup_dir(GDIR, DAY)          # 先建当天日期目录（已存在则复用）
print("backup dir:", DST)

TARGETS = [
    ("data.js", datajs_path(GDIR)),
    ("_raw_posts.json", meta_file(GDIR, "_raw_posts.json")),
    ("details.json", meta_file(GDIR, "details.json")),
    ("magnets.json", meta_file(GDIR, "magnets.json")),
]

rows = []
new = same = miss = 0
for name, src in TARGETS:
    if not os.path.exists(src):
        print("  跳过（源不存在）:", src)
        miss += 1
        continue
    with open(src, "rb") as f:
        blob = f.read()
    md5 = hashlib.md5(blob).hexdigest()
    dst = os.path.join(DST, name + ".pre-update.bak")
    if os.path.exists(dst):
        with open(dst, "rb") as f:
            old = hashlib.md5(f.read()).hexdigest()
        if old == md5:
            print("  已存在且内容相同: %s" % os.path.basename(dst))
            same += 1
        else:
            print("  当日快照已刷新（内容变化）: %s" % os.path.basename(dst))
            shutil.copy2(src, dst)
            os.utime(dst, None)
            new += 1
    else:
        shutil.copy2(src, dst)
        os.utime(dst, None)           # 用源时间戳不便比较, 统一记为备份时刻
        print("  已备份: %s (%d bytes)" % (os.path.basename(dst), len(blob)))
        new += 1
    rows.append("%-34s %10d  %s  <- %s"
                % (os.path.basename(dst), len(blob), md5, os.path.relpath(src, GDIR).replace("\\", "/")))

manifest = [
    "# _backup/%s 备份清单" % DAY,
    "生成时间: %s" % NOW,
    "原因: %s" % REASON,
    "画廊根目录: %s" % GDIR,
    "",
    "文件 | 字节 | md5 | 来源(相对根目录)",
    *[r for r in rows],
    "",
    "新增 %d | 内容相同跳过 %d | 源缺失 %d" % (new, same, miss),
]
mfile = os.path.join(DST, "manifest.txt")
atomic_write(mfile, "\n".join(manifest) + "\n")
print("DONE 新增=%d 相同=%d 缺失=%d | 清单 -> %s" % (new, same, miss, mfile))
