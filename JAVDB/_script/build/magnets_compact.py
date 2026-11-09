# -*- coding: utf-8 -*-
"""magnets.js 安全压缩（方案1，保留 link/size 原样，ed2k 也原样，绝不重建链接）。

做的瘦身（都向后兼容前端：m.name||code、m.size?、m.files?、m.date?、(m.tags||[])）：
  1) 去美化缩进 → 紧凑 JSON（separators=(",",":")）；
  2) 删 name（当 name 大小写等于番号时，前端回退用 code）；
  3) tags 去重；空 tags 省略；
  4) files=0 / date 空 省略。
不改：link、size、sizeMB（前端要显示体积、链接必须原样）。

用法：
  python magnets_compact.py            # 就地压缩 data/site/magnets.js（先备份到 _backup/<日期>/）
  python magnets_compact.py --check    # 只报告压缩前后大小，不写盘
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

import win_utf8        # noqa: F401
import atomic_io       # noqa: E402

SITE   = os.path.join(ROOT, "data", "site")
OUT    = os.path.join(SITE, "magnets.js")
MAG_VAR = "window.JAVDB_MAGNETS"
HEAD = "/* 磁力链接库：code -> [{link,name,sizeMB,size,files,date,tags}]（自动生成，紧凑格式，已安全瘦身） */\n"


def norm_entry(code, m):
    """就地对单条做安全瘦身；保留 link/size/sizeMB 原样。"""
    n = m.get("name")
    if isinstance(n, str) and n.upper() == str(code).upper():
        m.pop("name")
    if "tags" in m:
        tg = list(dict.fromkeys(m.get("tags") or []))
        if tg:
            m["tags"] = tg
        else:
            m.pop("tags")
    if not m.get("files"):
        m.pop("files", None)
    if not m.get("date"):
        m.pop("date", None)
    return m


def normalize(data):
    return {c: [norm_entry(c, dict(m)) for m in (v or [])] for c, v in data.items()}


def load_out():
    t = io.open(OUT, encoding="utf-8").read()
    i = t.index("=", t.index(MAG_VAR)) + 1
    obj, _ = json.JSONDecoder().raw_decode(t[i:].lstrip())
    return obj, t


def render(data):
    return HEAD + MAG_VAR + " = " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n"


def main():
    ap = argparse.ArgumentParser(description="magnets.js 安全压缩（保留链接/体积）")
    ap.add_argument("--check", action="store_true", help="只报告压缩前后大小，不写盘")
    args = ap.parse_args()

    if not os.path.isfile(OUT):
        print("找不到", OUT)
        return 1
    data, raw = load_out()
    before = len(raw.encode("utf-8"))
    nd = normalize(data)
    after = len(render(nd).encode("utf-8"))
    mb = lambda b: b / 1048576
    print(f"番号 {len(nd)} | 压缩前 {mb(before):.1f}MB -> 压缩后 {mb(after):.1f}MB "
          f"（省 {mb(before - after):.1f}MB / {(before - after) / before * 100:.0f}%）")
    if args.check:
        print("[--check] 未写盘。")
        return 0
    if after >= before:
        print("已是紧凑/最省，无需改写。")
        return 0
    bp = atomic_io.backup_path("magnets.js")
    shutil.copy2(OUT, bp)
    print("已备份旧 magnets.js ->", bp)
    tmp = OUT + ".tmp"
    io.open(tmp, "w", encoding="utf-8").write(render(nd))
    os.replace(tmp, OUT)
    # 回读自检
    d2, _ = load_out()
    assert len(d2) == len(nd), "回读番号数不符"
    print("已写 data/site/magnets.js，回读校验通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
