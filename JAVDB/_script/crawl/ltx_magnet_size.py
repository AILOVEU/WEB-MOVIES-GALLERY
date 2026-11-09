# -*- coding: utf-8 -*-
"""【已停用，2026-11-06】给 ltx.js 里【无 size 的 magnet 链接】按 info_hash 去 sukebei.nyaa.si 补真实体积。

**获取 ltx 的流程里没有这一步**：用户 2026-11-06 定规矩「以后获取 ltx 跳过本脚本，就用没 size 的版本」。
理由：体积不在 LTX 接口里，只能逐条去 sukebei 查（4 秒限速 + 4 次退避），站点常年不通（当天实测代理与直连
都是 `http=000`，而同代理打 ltx 站 200），且「sukebei 确认无此种子」的条目 size 仍为空、下次全量重扫会再白跑。
前端对空 size 本就 `${m.size ? … : ''}` 不显示体积栏，留空零副作用。
脚本原样保留（**不删**），哪天要补体积仍可单独跑；跑的话请带 `--codes-file` 只查新增，别全量重扫。

- 只处理 magnet: 且 size 为空 的条目；查到的写回 size / sizeMB，**已有的不动**（幂等、可重跑）。
- 磁力体积不在 LTX 接口返回里，只能按 info_hash 去 sukebei 查（种子页 `<span class="size">(x GiB)</span>`）。
- 温和限速：每次请求间隔 SLEEP 秒；查不到就跳过（下轮再试）。
- 写盘前把旧 ltx.js 备份到 _backup/<日期>/，用同目录 tmp+os.replace 原子替换。
- **增量范围开关**（`--codes=` / `--codes-file=`）：只处理给定番号下的无 size magnet。
  不带参数＝全量重扫，会把历史确认查不到的条目一起再试一遍（每条最多 4 次退避）。

用法（仅在明确需要补体积时才跑）：
  python ltx_magnet_size.py --codes-file=_pipeline/<日期>/ltx_new_codes.txt   # 只查本轮新增（推荐）
  python ltx_magnet_size.py --codes=AB-123,CD-456                              # 只查指定番号
  python ltx_magnet_size.py                                                    # 全量重扫（会白跑，避免）
  python ltx_magnet_size.py --dry-run                                          # 只查询并报告，不写盘
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
import json, io, os, re, shutil, subprocess, time

import win_utf8        # noqa: F401
import atomic_io       # noqa: E402

LTXJS = os.path.join(ROOT, "data", "site", "ltx.js")
PX    = "http://127.0.0.1:7890"
UA    = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"
SLEEP = 4.0
TRIES = 4
HASH_RE  = re.compile(r"urn:btih:([0-9a-fA-F]{40})", re.I)
OG_RE    = re.compile(r'og:description"\s+content="[^"]*?\|\s*([\d.,]+ ?[KMGTPE]?i?B)\s*\|', re.I)
FSPAN_RE = re.compile(r'<span class="file-size">\(([\d.,]+ ?[KMGTPE]?i?B)\)</span>', re.I)
MULT = {"b":1,"kb":1000,"mb":1000**2,"gb":1000**3,"tb":1000**4,"pb":1000**5,"eb":1000**6,
        "kib":1024,"mib":1024**2,"gib":1024**3,"tib":1024**4,"pib":1024**5,"eib":1024**5*1024}


def human(b):
    mb = b / 1048576
    return ("%.2fGB" % (mb / 1024)) if mb >= 1024 else ("%.0fMB" % mb)


def to_bytes(s):
    m = re.match(r"([\d.,]+)\s*([KMGTPE]?i?B)", s.strip(), re.I)
    if not m:
        return 0
    val = float(m.group(1).replace(",", ""))
    return int(val * MULT.get(m.group(2).lower(), 0))


def load():
    t = io.open(LTXJS, encoding="utf-8").read()
    i = t.index("=", t.index("window.JAVDB_LTX")) + 1
    return json.JSONDecoder().raw_decode(t[i:].lstrip())[0]


def save(data):
    tmp = LTXJS + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write("/* LTX 资源库磁力/ed2k：code -> [{link,name,sizeMB,size,files,date,tags}]"
                "（源 ltx.libray.workers.dev/api/items，tags 统一 LTX，只增不删；magnet 体积按 info_hash 查 sukebei 补齐） */\n")
        f.write("window.JAVDB_LTX = ")
        f.write(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
        f.write(";\n")
    os.replace(tmp, LTXJS)


def fetch_once(hexhash):
    url = f"https://sukebei.nyaa.si/?q={hexhash}&f=0&c=0_0"
    r = subprocess.run(["curl", "-sL", "--ssl-no-revoke", "--retry", "2", "--retry-delay", "2",
                        "--max-time", "30", "-x", PX, "-A", UA, url], capture_output=True)
    html = r.stdout.decode("utf-8", "replace")
    if len(html) < 3000:      # CF 质询/限流常是短页；真正种子页 ~1.7万
        return None           # 传输失败/被拦（可重试）
    og = OG_RE.search(html)   # 首选：og:description 里的“总大小”
    if og:
        return to_bytes(og.group(1))
    spans = FSPAN_RE.findall(html)  # 兵底：各文件大小求和
    if spans:
        return sum(to_bytes(s) for s in spans)
    return 0                  # 拿到完整页但无大小（视为未收录，不重试）


def fetch_size(hexhash):
    """返回 bytes(>0)=找到 / 0=确认无 / None=传输失败需重试。"""
    for i in range(TRIES):
        got = fetch_once(hexhash)
        if got is not None:
            return got
        time.sleep(SLEEP * (i + 1))   # 退避
    return None


def read_codes_arg(codes, codes_file):
    """--codes/--codes-file -> 番号集合（None=不限定）。逗号/空格/换行分隔，.json 列表也行；整行 # 开头是注释。"""
    if not codes and not codes_file:
        return None
    lines = []
    if codes:
        lines.append(codes)
    if codes_file:
        t = io.open(codes_file, encoding="utf-8").read()
        if codes_file.lower().endswith(".json") or t.lstrip().startswith("["):
            lines.extend(str(c) for c in json.loads(t))
        else:
            lines.extend(ln for ln in t.splitlines() if not ln.strip().startswith("#"))
    out = set()
    for ln in lines:
        for tok in str(ln).replace(",", " ").split():
            if tok == "#":            # 行内注释：# 之后丢掉
                break
            if tok.strip():
                out.add(tok.strip())
    return out


def main():
    ap = argparse.ArgumentParser(description="magnet 按 info_hash 去 sukebei 补体积（只补空、幂等）")
    ap.add_argument("--dry-run", action="store_true", help="只查询报告，不写盘/不备份")
    ap.add_argument("--codes", help="只处理这些番号（逗号/空格分隔）")
    ap.add_argument("--codes-file", help="番号名单文件（每行一个，或 .json 列表）")
    args = ap.parse_args()

    data = load()
    scope = read_codes_arg(args.codes, args.codes_file)
    todo = [(c, m) for c, v in data.items() for m in v
            if m["link"].startswith("magnet:") and not m.get("size")
            and (scope is None or c in scope)]
    left = sum(1 for v in data.values() for m in v if m["link"].startswith("magnet:") and not m.get("size"))
    if scope is not None:
        print(f"范围限定：名单 {len(scope)} 个番号；全库无 size 的 magnet {left} 条，本轮只查其中 {len(todo)} 条")
    else:
        print(f"待补 magnet（当前 size 为空）：{len(todo)} 条")

    filled = miss = 0
    for code, m in todo:
        hm = HASH_RE.search(m["link"])
        if not hm:
            miss += 1
            print(f"  [{code}] 无 hex info_hash，跳过")
            continue
        b = fetch_size(hm.group(1))
        if b and b > 0:
            m["size"] = human(b)
            m["sizeMB"] = round(b / 1048576)
            filled += 1
            print(f"  [{code}] {hm.group(1)[:10]}… -> {m['size']}")
        elif b is None:
            miss += 1
            print(f"  [{code}] 传输失败/被限流，下次再试")
        else:
            miss += 1
            print(f"  [{code}] sukebei 确认无此种子")
        time.sleep(SLEEP)

    print(f"\n补齐 {filled} 条，未取到 {miss} 条")
    if args.dry_run:
        print("[dry-run] 未写盘。")
        return 0
    if filled == 0:
        print("没有新增，ltx.js 不变。")
        return 0
    bp = atomic_io.backup_path("ltx.js")
    shutil.copy2(LTXJS, bp)
    print("已备份旧 ltx.js ->", bp)
    save(data)
    print("已写 data/site/ltx.js")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
