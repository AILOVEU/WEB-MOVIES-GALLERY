# -*- coding: utf-8 -*-
"""封面扁平目录 → 64 分桶 迁移（一次性、幂等）。

做两件事：
  1) 把 assets/covers/ 顶层的图片文件搬进 assets/covers/<bucket(stem)%64>/<name>（bucket 同 covers_manifest.cover_bucket）；
  2) 把 movies.js 里仍为扁平的 local（assets/covers/X.jpg 或 covers/X.jpg，不含子目录）重写为带分桶子目录。
清单 covers_manifest.json 的 file 只存文件名、find_on_disk 已改递归，故不用动。
只搬“顶层散文件”；已在子目录里的不动 → 可安全重跑。

用法：
  python covers_reshard.py --check     # 预览：将搬多少文件、重写多少 local，不动盘
  python covers_reshard.py             # 执行（先备份 movies.js 到 _backup/<日期>/）
"""
# __JDB_BOOT__
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
import json, io, os, shutil

import win_utf8            # noqa: F401
import atomic_io           # noqa: E402
import covers_manifest as cm   # noqa: E402  复用 cover_bucket / local_field / IMG_EXT

MOVIES_JS = os.path.join(ROOT, "data", "site", "movies.js")
MV_VAR = "window.JAVDB_MOVIES = "   # 项目统一约定：等号两侧各一空格，多个脚本按此严格定位


def plan_files():
    moves = []
    if os.path.isdir(cm.COVERS):
        for n in os.listdir(cm.COVERS):
            full = os.path.join(cm.COVERS, n)
            if not os.path.isfile(full):
                continue
            if os.path.splitext(n)[1].lower() not in cm.IMG_EXT:
                continue
            b = cm.cover_bucket(os.path.splitext(n)[0])
            moves.append((n, os.path.join(cm.COVERS, b, n)))
    return moves


def do_files(moves, apply):
    moved = 0
    for name, dst in moves:
        if apply:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if not os.path.exists(dst):
                shutil.move(os.path.join(cm.COVERS, name), dst)
                moved += 1
        else:
            moved += 1
    return moved


def load_movies():
    t = io.open(MOVIES_JS, encoding="utf-8").read()
    i = t.index(MV_VAR) + len(MV_VAR)
    head = t[:i]
    d, _ = json.JSONDecoder().raw_decode(t[i:].lstrip())
    return head, d


def rewrite_local(apply):
    if not os.path.isfile(MOVIES_JS):
        print("找不到 movies.js，跳过 local 重写")
        return 0
    head, d = load_movies()
    changed = 0
    for c, e in d.items():
        loc = (e.get("local") or "").replace("\\", "/")
        if not loc:
            continue
        for pre in ("assets/covers", "covers"):
            if loc.startswith(pre + "/"):
                rest = loc[len(pre) + 1:]
                if "/" not in rest:                      # 仍是扁平 → 需加子目录
                    if apply:
                        e["local"] = pre + "/" + cm.cover_bucket(os.path.splitext(rest)[0]) + "/" + rest
                    changed += 1
                break
    if apply and changed:
        bp = atomic_io.backup_path("movies.js")
        shutil.copy2(MOVIES_JS, bp)
        print("已备份 movies.js ->", bp)
        tmp = MOVIES_JS + ".tmp"
        io.open(tmp, "w", encoding="utf-8").write(head + json.dumps(d, ensure_ascii=False, separators=(",", ":")) + ";\n")
        os.replace(tmp, MOVIES_JS)
    return changed


def import_from(src, move=False):
    """把外部（扁平存放的）封面目录导入合并到当前 64 桶树。
    对每个图片文件按同一 cover_bucket(stem) 放进 assets/covers/<b>/<原名>；
    目标已存在则跳过（不覆盖已下载/已有的）。默认 copy（保留源），--move 才搬走。"""
    if not os.path.isdir(src):
        print("源目录不存在:", src)
        return
    put = shutil.move if move else shutil.copy2
    done = dup = nonimg = 0
    for root, _d, files in os.walk(src):
        for n in files:
            if os.path.splitext(n)[1].lower() not in cm.IMG_EXT:
                nonimg += 1
                continue
            b = cm.cover_bucket(os.path.splitext(n)[0])
            d = os.path.join(cm.COVERS, b)
            os.makedirs(d, exist_ok=True)
            dst = os.path.join(d, n)
            if os.path.exists(dst):
                dup += 1
                continue
            try:
                put(os.path.join(root, n), dst)
                done += 1
            except OSError as e:
                print("  跳过", n, "：", e)
    print(f"导入完成（{'move' if move else 'copy'}）：新增 {done} | 已存在跳过 {dup} | 非图片 {nonimg}")
    print("→ 下一步跑一次体检重新登记+挂 local： python _script/manifest/covers_check.py")


def main():
    ap = argparse.ArgumentParser(description="封面扁平→64分桶迁移 / 外部旧封面导入合并")
    ap.add_argument("--check", action="store_true", help="只预览，不动盘")
    ap.add_argument("--import", dest="import_dir", metavar="SRC", help="从该目录把扁平旧封面导入当前分桶树")
    ap.add_argument("--move", action="store_true", help="导入时把源文件搬走（默认复制）")
    args = ap.parse_args()

    if args.import_dir:
        import_from(args.import_dir, move=args.move)
        return

    apply = not args.check

    moves = plan_files()
    print("分桶数 N =", cm.COVERS_N)
    print("待搬封面文件（顶层散文件）：%d 个" % len(moves))
    ch = rewrite_local(apply)
    print("待重写 local（扁平路径）：%d 条" % ch)
    if apply:
        m = do_files(moves, True)
        print("已搬动 %d 个文件进子目录；已重写 movies.js 的 local" % m)
        # 复核：顶层应几乎无散图片
        left = sum(1 for n in os.listdir(cm.COVERS)
                   if os.path.isfile(os.path.join(cm.COVERS, n))
                   and os.path.splitext(n)[1].lower() in cm.IMG_EXT)
        print("复核：covers 顶层剩余散图片 =", left)
    else:
        do_files(moves, False)
        print("[--check] 未动盘。")


if __name__ == "__main__":
    main()
