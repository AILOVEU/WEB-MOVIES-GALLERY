# -*- coding: utf-8 -*-
"""把新站抓取结果（_pipeline/<日期>/items_all.json）增量并入 data/site/ltx.js。

背景：2026-11-19 起旧接口 ltx.libray.workers.dev/api/items 已停用（返回 migrated 提示），
新站 ltxlib.dpdns.org 的 API 加了 Cloudflare Turnstile + ECDH/AES-GCM 加密，
纯 Python 抓不到，须用浏览器过验证后由 _script/cookie/ltx_harvest.js 导出 items_all.json。
本脚本只负责「合并」，不负责抓取。

规则与 ltx_fetch.py 完全一致（**只增不删**）：
  - 新番号整条追加；已有番号只补没见过的链接（按 link 字符串去重）。
  - 旧链接、旧番号一律保留；magnet 的 size/sizeMB 一律留空（复用 ltx_fetch.parse_link）。
  - 改前备份 ltx.js 到 _backup/<日期>/（同日已有则 ltx_before_<HHMMSS>.js）。
  - 原始抓取结果已在 _pipeline/<日期>/items_all.json（harvest 脚本落的）。

用法：
  python ltx_merge_harvest.py --dry-run                       # 只看增量，不写盘
  python ltx_merge_harvest.py                                 # 用当日 items_all.json 合并
  python ltx_merge_harvest.py --input <path/to/items_all.json>  # 指定输入
"""
# __JDB_BOOT__ 统一 ROOT/sys.path 引导
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
import io, json, os, shutil, time

import win_utf8        # noqa: F401
import atomic_io
import ltx_fetch       # 复用 parse_link / load_existing / write_ltxjs（同一套 schema 与去重规则）

LTXJS = ltx_fetch.LTXJS


def find_input(explicit=None):
    if explicit:
        return explicit
    return atomic_io.record_path("items_all.json")


def main():
    ap = argparse.ArgumentParser(description="新站 LTX 抓取结果增量并入 ltx.js（只增不删）")
    ap.add_argument("--dry-run", action="store_true", help="只报告增量，不写盘/不备份")
    ap.add_argument("--input", help="items_all.json 路径（缺省 _pipeline/<日期>/items_all.json）")
    args = ap.parse_args()

    src = find_input(args.input)
    if not os.path.isfile(src):
        print("[ERR] 找不到抓取结果：", src)
        print("      先用浏览器过 Turnstile 后跑 _script/cookie/ltx_harvest.js 导出。")
        return 2
    items = json.loads(io.open(src, encoding="utf-8").read())
    if not isinstance(items, list):
        print("[ERR] items_all.json 不是数组")
        return 2

    old = ltx_fetch.load_existing()
    merged = {c: list(v) for c, v in old.items()}
    oldlinks = {m.get("link") for v in old.values() for m in v}
    new_codes = new_links = 0
    touched = set()
    for it in items:
        code = (it.get("code") or "").strip()
        lk = it.get("link")
        if not code or not lk:
            continue
        have = {m.get("link") for m in merged.get(code, [])}
        if lk in have:
            continue
        additions = [ltx_fetch.parse_link(lk, code)]
        touched.add(code)
        if code not in merged:
            new_codes += 1
            merged[code] = additions
        else:
            merged[code] = merged[code] + additions
        new_links += 1

    print(f"抓取结果 {len(items)} 条；新增番号 +{new_codes}，新增链接 +{new_links}；"
          f"合并后总番号 {len(merged)}，总链接 {sum(len(v) for v in merged.values())}")

    lst = atomic_io.record_path("ltx_new_codes.txt")
    if not args.dry_run:
        io.open(lst, "w", encoding="utf-8").write("\n".join(sorted(touched)) + ("\n" if touched else ""))
    print(f"本轮新增/变更番号 {len(touched)} 个 -> {lst}" + ("（dry-run 未写）" if args.dry_run else ""))

    if args.dry_run:
        print("[dry-run] 未写盘。")
        return 0
    if new_links == 0:
        print("无增量，ltx.js 保持不变。")
        return 0

    if os.path.isfile(LTXJS):
        bp = atomic_io.backup_path("ltx.js")
        if os.path.exists(bp):
            bp = os.path.join(os.path.dirname(bp), "ltx_before_%s.js" % time.strftime("%H%M%S"))
        shutil.copy2(LTXJS, bp)
        print("已备份旧 ltx.js ->", bp)
    ltx_fetch.write_ltxjs(merged)
    print("已写 data/site/ltx.js")
    mag_new = sum(1 for c in touched for m in merged[c]
                  if m["link"].startswith("magnet:") and not m.get("size"))
    if mag_new:
        print(f"其中 {mag_new} 条新 magnet 体积留空（按约定不跑 ltx_magnet_size.py）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
