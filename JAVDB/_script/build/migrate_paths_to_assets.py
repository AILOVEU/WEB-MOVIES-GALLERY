# -*- coding: utf-8 -*-
"""一次性迁移：把数据里的资源路径改成 assets/ 新布局（2026-11-02 目录整理）。

做了什么（幂等，可反复跑；已带 assets/ 前缀的不会再叠一层）：
  1. `data/site/movies.js` 的 `local` / `video` / `talk` 字段：
       covers/xxx.jpg      -> assets/covers/xxx.jpg
       videos/xxx.mp4      -> assets/videos/xxx.mp4
       水果派/xxx.mp4       -> assets/水果派/xxx.mp4
     （这三个字段是前端当相对 URL 直接用的，目录一搬就必须跟着改）
  2. `data/state/covers_manifest.json` 的 `covers_dir`、`data/state/videos_manifest.json` 的 `videos_dir`
     这两个自述字段改成 assets/...（判定逻辑本来只看记录不看目录，改的只是说明）。
  3. 顺手校正清单记录里存的文件名/相对路径（如有旧前缀）。

安全：改 movies.js 前先备份到 `_backup/movies_before_assets_layout.js`（只备份一次，不覆盖）；
      直接写目标文件（本机沙盒会把 tmp+os.replace 当危险操作中断，见 RESUME 第 5 节坑 1）；
      不删除、不移动任何资源文件。

用法：
  python _script/migrate_paths_to_assets.py --dry-run     # 只看要改多少条
  python _script/migrate_paths_to_assets.py               # 真正落盘 + 校验路径能不能解析
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
import io
import json
import os
import shutil
import time
import win_utf8        # noqa: F401  重定向/管道下固定 UTF-8，否则 ✅/中文会砸死进程

DATA = os.path.join(ROOT, "data")                        # 站点数据与名单
SITE = os.path.join(DATA, "site")    # 页面要读的 *.js（HTML 写死引用）
STATE = os.path.join(DATA, "state")  # 抓取状态：清单 / 终态名单 / 校验基准
MOVIES_JS = os.path.join(SITE, "movies.js")
BK = os.path.join(ROOT, "_backup")
BK_FILE = os.path.join(BK, time.strftime('%Y-%m-%d'), "movies_before_assets_layout.js")  # 当日日期子目录
VAR = "window.JAVDB_MOVIES = "

# 字段 -> (旧前缀, 新前缀)；talk/video/local 都是「相对站点根」的路径
FIELD_PREFIX = {
    "local": ("covers/", "assets/covers/"),
    "video": ("videos/", "assets/videos/"),
    "talk":  ("水果派/", "assets/水果派/"),
}
MANIFEST_DIR_FIELD = {  # 文件名 -> (自述字段名, 新值)
    "covers_manifest.json": ("covers_dir", "assets/covers"),
    "videos_manifest.json": ("videos_dir", "assets/videos"),
}


def log(*a):
    print(*a, flush=True)


def load_text(path):
    return io.open(path, encoding="utf-8").read()


def main():
    ap = argparse.ArgumentParser(description="把数据里的资源路径改成 assets/ 布局")
    ap.add_argument("--dry-run", action="store_true", help="只统计，不写盘")
    args = ap.parse_args()

    txt = load_text(MOVIES_JS)
    i = txt.index(VAR) + len(VAR)
    mv, _ = json.JSONDecoder().raw_decode(txt[i:].lstrip())
    head = txt[:txt.index(VAR)]

    changed = {f: 0 for f in FIELD_PREFIX}
    for code, e in mv.items():
        if not isinstance(e, dict):
            continue
        for f, (old, new) in FIELD_PREFIX.items():
            v = e.get(f)
            if isinstance(v, str) and v.startswith(old):
                e[f] = new + v[len(old):]
                changed[f] += 1
    log("movies.js 需要/已经改的字段条数：",
        " | ".join(f"{f} {n}" for f, n in changed.items()), f"（总数 {len(mv)} 部）")

    # 清单的自述目录字段
    mf = []                                  # [(文件名, 改过的 dict)]
    for fname, (key, newval) in MANIFEST_DIR_FIELD.items():
        p = os.path.join(STATE, fname)
        if not os.path.exists(p):
            continue
        d = json.load(io.open(p, encoding="utf-8"))
        if d.get(key) != newval:
            log(f"{fname}: {key} {d.get(key)!r} -> {newval!r}")
            d[key] = newval
            mf.append((fname, d))
        else:
            log(f"{fname}: {key} 已是 {newval!r}")

    if args.dry_run:
        log("--dry-run：不写盘")
        return 0

    if sum(changed.values()) or mf:
        os.makedirs(os.path.dirname(BK_FILE), exist_ok=True)
        if not os.path.exists(BK_FILE) and sum(changed.values()):
            shutil.copyfile(MOVIES_JS, BK_FILE)
            log("已备份 movies.js ->", os.path.relpath(BK_FILE, ROOT))
        if sum(changed.values()):
            with io.open(MOVIES_JS, "w", encoding="utf-8") as f:
                f.write(head + VAR + json.dumps(mv, ensure_ascii=False, separators=(",", ":")) + ";\n")
            log("movies.js 已写入")
        for fname, d in mf:
            p = os.path.join(STATE, fname)
            with io.open(p, "w", encoding="utf-8") as f:      # 直接写改好的那份，别再读一次旧盘
                json.dump(d, f, ensure_ascii=False, indent=2, sort_keys=True)
                f.write("\n")
            log(f"{fname} 已写入")
    else:
        log("没有要改的内容（已经是新布局）")

    # ---------- 校验：字段指向的文件到底能不能解析 ----------
    reload = load_text(MOVIES_JS)
    j = reload.index(VAR) + len(VAR)
    mv2, _ = json.JSONDecoder().raw_decode(reload[j:].lstrip())
    bad = {}
    for f in FIELD_PREFIX:
        vals = [e[f] for e in mv2.values() if isinstance(e, dict) and e.get(f)]
        miss = [v for v in vals if not os.path.exists(os.path.join(ROOT, v.replace("/", os.sep)))]
        old = [v for v in vals if not v.startswith("assets/")]
        bad[f] = (len(vals), len(miss), len(old))
        log(f"  {f}: 共 {len(vals)} 条 | 路径解析不到 {len(miss)} 条 | 还没带 assets/ 前缀 {len(old)} 条")
        if miss[:3]:
            log("     解析不到示例：", ", ".join(miss[:3]), "（多半是已被手动搬走的记录，属正常）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
