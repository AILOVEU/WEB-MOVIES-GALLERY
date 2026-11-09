# -*- coding: utf-8 -*-
"""把 magnets.js 分桶成 data/site/mag/mag_<b>.js（方案2 懒加载，16 桶）。

- 分桶键 bucket(code) = 大写去非[A-Z0-9] 后逐字符 h=(h*31+ord)&0xffffffff，再 % N。
  与前端 magBucket() 完全一致（JS 用 >>>0 同样 32 位截断）。
- 每桶：window.JAVDB_MAG_<b> = { code: [{link,name,sizeMB,size,files,date,tags}] }（紧凑）。
- ltx.js 不在此列（单独加载，不分桶）。
- 数据源默认 data/state/magnets.js；不存在则回退 data/site/magnets.js（迁移期兼容）。

用法：
  python magnet_buckets.py            # 依据源 magnets.js 重建全部桶
  python magnet_buckets.py --check     # 只报告每桶大小/番号数
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
import json, io, os

import win_utf8        # noqa: F401

N    = 16
SITE = os.path.join(ROOT, "data", "site")
STATE = os.path.join(ROOT, "data", "state")
MAGDIR = os.path.join(SITE, "mag")
HEAD = ("/* 磁力分桶 %s/%s：window.JAVDB_MAG_<b>，bucket=Σ(31*char)%%%s；前端按需注入 */\n"
        % (N, "b", N))


def bucket(code, n=N):
    # 与前端 magBucket 完全一致：只保留 [A-Z0-9]（丢弃 - 及任何非 ASCII），逐字符 h=(h*31+ord)&0xffffffff
    s = "".join(ch for ch in str(code).upper() if ("A" <= ch <= "Z") or ("0" <= ch <= "9"))
    h = 0
    for ch in s:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return h % n


def load_source():
    for p in (os.path.join(STATE, "magnets.js"), os.path.join(SITE, "magnets.js")):
        if os.path.isfile(p):
            t = io.open(p, encoding="utf-8").read()
            i = t.index("=", t.index("window.JAVDB_MAGNETS")) + 1
            obj, _ = json.JSONDecoder().raw_decode(t[i:].lstrip())
            return obj, p
    raise SystemExit("找不到 magnets.js 源（state/ 或 site/）")


def rebuild(n=N):
    """从源 magnets.js 重建全部桶文件；返回 (总字节, 每桶番号数列表)。供 magnet_spider 结束时调用。"""
    data, _src = load_source()
    buckets = [[] for _ in range(n)]
    for code, entries in data.items():
        buckets[bucket(code, n)].append((code, entries))
    os.makedirs(MAGDIR, exist_ok=True)
    total = 0
    for b in range(n):
        sub = {c: e for c, e in buckets[b]}
        body = "window.JAVDB_MAG_%d = " % b + json.dumps(sub, ensure_ascii=False, separators=(",", ":")) + ";\n"
        io.open(os.path.join(MAGDIR, "mag_%d.js" % b), "w", encoding="utf-8").write(body)
        total += len(body.encode("utf-8"))
    return total, [len(x) for x in buckets]


def main():
    ap = argparse.ArgumentParser(description="magnets.js 分桶（16）")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    data, src = load_source()
    buckets = [[] for _ in range(N)]   # 每桶收集 (code, entries)
    for code, entries in data.items():
        buckets[bucket(code)].append((code, entries))

    os.makedirs(MAGDIR, exist_ok=True)
    total = 0
    for b in range(N):
        sub = {c: e for c, e in buckets[b]}
        body = "window.JAVDB_MAG_%d = " % b + json.dumps(sub, ensure_ascii=False, separators=(",", ":")) + ";\n"
        outp = os.path.join(MAGDIR, "mag_%d.js" % b)
        if not args.check:
            io.open(outp, "w", encoding="utf-8").write(body)
        sz = len(body.encode("utf-8"))
        total += sz
        print("mag_%-2d.js  番号 %4d  %.2fMB" % (b, len(sub), sz / 1048576))
    print(f"源 {src} | 共 {N} 桶 {total/1048576:.1f}MB | 番号合计 {sum(len(x) for x in buckets)}")
    if args.check:
        print("[--check] 未写桶文件。")
    else:
        # 桶文件一变就刷页面版本号：前端 loadMag 靠 ?v=BUILD_V 破缓存（没这一步会读到旧磁力）
        import build_collection_js as bcj
        ver, touched = bcj.bump_refs()
        print("页面版本号 -> ?v=%s（改动文件：%s）" % (ver, ", ".join(touched) or "无，已是当日版本"))


if __name__ == "__main__":
    main()
