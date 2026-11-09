# -*- coding: utf-8 -*-
"""LTX 资源库增量抓取：https://ltx.libray.workers.dev/api/items
输出 data/site/ltx.js（window.JAVDB_LTX：code -> [{link,name,sizeMB,size,files,date,tags:["LTX"]}]）。

规则（**只增不删**）：
  - 新番号：整条追加。
  - 已有番号：只补它此前没见过的链接（按 link 字符串去重）；旧链接、旧番号一律保留。
  - 抓前把旧 ltx.js 备份到 _backup/<日期>/，原始 JSON 记录到 _pipeline/<日期>/（过程文件按日期归档）。

size 来自 ed2k 链接本身（`ed2k://|file|名|字节数|哈希|/` 的第 4 段），magnet 链接不带 size 则留空。
**magnet 一律留空、不再补体积**（2026-11-06 用户定的规矩：以后获取 ltx 跳过 `ltx_magnet_size.py`，
就用没 size 的版本；页面本来对空 size 是 `${m.size?...:''}`，不显示体积栏，不影响浏览与排序）。

用法：
  python ltx_fetch.py            # 抓一次并增量合并
  python ltx_fetch.py --dry-run  # 只报告会有多少新增，不写盘
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
import json, io, os, subprocess, time, urllib.parse

import win_utf8        # noqa: F401  固定 UTF-8，管道下不砸进程
import atomic_io       # noqa: E402  backup_path / record_path / pipeline_dir

SITE  = os.path.join(ROOT, "data", "site")
LTXJS = os.path.join(SITE, "ltx.js")
API   = "https://ltx.libray.workers.dev/api/items"
PX    = "http://127.0.0.1:7890"
UA    = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"
TAG   = "LTX"


def human(b):
    mb = b / 1048576
    return ("%.2fGB" % (mb / 1024)) if mb >= 1024 else ("%.0fMB" % mb)


def parse_link(link, code):
    """ed2k 里带名字与字节数；magnet 只解析 dn 当名字、无 size。tags 统一 LTX。"""
    if link.startswith("ed2k:"):
        segs = link.split("|")
        try:
            name, size = segs[2], int(segs[3])
        except (IndexError, ValueError):
            name, size = code, 0
        return {"link": link, "name": name,
                "sizeMB": round(size / 1048576) if size else 0,
                "size": human(size) if size else "",
                "files": 0, "date": "", "tags": [TAG]}
    name = code
    if "?" in link:
        for kv in link.split("?", 1)[1].split("&"):
            if kv.startswith("dn="):
                try:
                    name = urllib.parse.unquote(kv[3:])
                except Exception:
                    pass
    return {"link": link, "name": name, "sizeMB": 0, "size": "",
            "files": 0, "date": "", "tags": [TAG]}


def load_existing():
    if not os.path.isfile(LTXJS):
        return {}
    t = io.open(LTXJS, encoding="utf-8").read()
    i = t.index("=", t.index("window.JAVDB_LTX")) + 1
    obj, _ = json.JSONDecoder().raw_decode(t[i:].lstrip())
    return obj


def write_ltxjs(data):
    tmp = LTXJS + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write("/* LTX 资源库磁力/ed2k：code -> [{link,name,sizeMB,size,files,date,tags}]"
                "（源 ltx.libray.workers.dev/api/items，tags 统一 LTX，只增不删） */\n")
        f.write("window.JAVDB_LTX = ")
        f.write(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
        f.write(";\n")
    os.replace(tmp, LTXJS)  # 同目录原子替换（非沙盒删除路径，安全）


def main():
    ap = argparse.ArgumentParser(description="LTX 资源库增量抓取（只增不删）")
    ap.add_argument("--dry-run", action="store_true", help="只报告新增量，不写盘/不备份")
    args = ap.parse_args()

    # 1) 抓取（curl 走代理，ltx 站不需 cookie）
    r = subprocess.run(["curl", "-s", "--ssl-no-revoke", "--max-time", "60",
                        "-x", PX, "-A", UA, "-H", "Accept: application/json",
                        API], capture_output=True)
    raw = r.stdout.decode("utf-8", "replace")
    if not raw.lstrip().startswith("{"):
        print("[ERR] 抓取失败或不是 JSON（前 200 字）：", raw[:200])
        return 2
    fetched = json.loads(raw)
    items = fetched.get("items", [])

    # 原始响应按日期归档（过程文件）
    rec = atomic_io.record_path("ltx_items.json")
    io.open(rec, "w", encoding="utf-8").write(raw)

    # 2) 增量合并（只增不删）
    old = load_existing()
    new_codes, new_links = 0, 0
    touched = set()          # 本轮真的加进东西的番号 = 下一步「只查新增」的工作集
    merged = {c: list(v) for c, v in old.items()}
    for it in items:
        code = (it.get("code") or "").strip()
        if not code:
            continue
        have = {m.get("link") for m in merged.get(code, [])}
        additions = []
        for lk in (it.get("links") or []):
            if lk not in have:
                additions.append(parse_link(lk, code))
                have.add(lk)
                new_links += 1
        if additions:
            touched.add(code)
            if code not in merged:
                new_codes += 1
                merged[code] = additions
            else:
                merged[code] = merged[code] + additions

    print(f"抓取 {len(items)} 条 / {sum(len(x.get('links') or []) for x in items)} 链接；"
          f"新增番号 +{new_codes}，新增链接 +{new_links}；合并后总番号 {len(merged)}")

    # 本轮工作集（含「旧番号补新链接」）落 _pipeline/<日期>/，只作留档与「只增不删」复核用；
    # 它不再驱动任何后续查询——magnet 补体积（ltx_magnet_size.py）已按约定停用。
    # 无增量也写空文件（不留昨天的名单，也不删文件——避开沙盒删除路径）。
    lst = atomic_io.record_path("ltx_new_codes.txt")
    if not args.dry_run:
        io.open(lst, "w", encoding="utf-8").write("\n".join(sorted(touched)) + ("\n" if touched else ""))
    print(f"本轮新增/变更番号 {len(touched)} 个 -> {lst}"
          + ("（dry-run 未写）" if args.dry_run else ""))

    if args.dry_run:
        print("[dry-run] 未写盘。")
        return 0
    if new_codes == 0 and new_links == 0:
        print("无增量，ltx.js 保持不变（仍写一次以刷新，可选）。")

    # 3) 备份旧文件再写回（同日已备份过则改名为 ltx_before_<HHMMSS>.js，
    #    不覆盖当天第一份改前快照——与 ltx_merge_table.py 同一规矩）
    if os.path.isfile(LTXJS):
        import shutil
        bp = atomic_io.backup_path("ltx.js")
        if os.path.exists(bp):
            bp = os.path.join(os.path.dirname(bp), "ltx_before_%s.js" % time.strftime("%H%M%S"))
        shutil.copy2(LTXJS, bp)
        print("已备份旧 ltx.js ->", bp)
    write_ltxjs(merged)
    print("已写 data/site/ltx.js")
    mag_new = sum(1 for c in touched for m in merged[c]
                  if m["link"].startswith("magnet:") and not m.get("size"))
    if mag_new:
        print(f"其中 {mag_new} 条新 magnet 无体积 —— 按约定就留空，"
              f"不跑 ltx_magnet_size.py（2026-11-06 定：以后获取 ltx 跳过补体积）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
