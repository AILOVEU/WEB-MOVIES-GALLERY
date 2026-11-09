# -*- coding: utf-8 -*-
"""s04q2_purge_recycle.py — 收尾清空隔离区（目录级删除，非逐文件删除）

清空对象（§1.34 / §1.35）：
  _orphans/            误下载的降级产物（s04s 隔离）+ 0 字节空壳（s04r 移入）+ 重复短名（s04t 隔离）
  _recycle_avatars/    早期头像回收目录（通常已空）

用法: python s04q2_purge_recycle.py
"""
import os, sys, shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # _scripts/
import common as C

# 仅这两个受控目录，杜绝误删（s04r 隔离下来的旧文件区，2026-11-04 起归 _backup）
TARGETS = [
    os.path.join(C.BACKUP_DIR, "_orphans"),
    os.path.join(C.BACKUP_DIR, "_recycle_avatars"),
]

total_n = 0
total_sz = 0
found = False
for t in TARGETS:
    if not os.path.isdir(t):
        continue
    found = True
    n = 0
    sz = 0
    for dp, dn, fn in os.walk(t):
        for f in fn:
            n += 1
            try:
                sz += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    print("待清空 %s: %d 个文件, %.1f MB" % (os.path.basename(t), n, sz / 1048576))
    total_n += n
    total_sz += sz

if not found:
    print("NOT_FOUND")
    raise SystemExit(0)

for t in TARGETS:
    if os.path.isdir(t):
        shutil.rmtree(t)
        print("removed:", t)

print("PURGE_OK %d %.1f" % (total_n, total_sz / 1048576))
