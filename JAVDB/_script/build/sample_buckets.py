# -*- coding: utf-8 -*-
"""把 samples.js 分桶成 data/site/smp/smp_<b>.js（16 桶，懒加载）。

- 分桶键与 magnet_buckets.py / 前端 magBucket() **完全一致**：
  大写去非 [A-Z0-9] 后逐字符 h=(h*31+ord)&0xffffffff，再 % N。
  这样前端用同一个 bucket 函数就能定位剧照分桶，不用两套逻辑。
- 每桶：window.JAVDB_SMP_<b> = { code: [url, ...] }（紧凑）
- 数据源：data/state/samples.js（只记远程地址，不下载）

用法：
  python sample_buckets.py            # 依据 samples.js 重建全部分桶
  python sample_buckets.py --check     # 只报告每桶大小/番号数
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
import io
import json
import os

import win_utf8        # noqa: F401

N = 16
SITE = os.path.join(ROOT, "data", "site")
STATE = os.path.join(ROOT, "data", "state")
SMPDIR = os.path.join(SITE, "smp")
SRC = os.path.join(STATE, "samples.js")
VAR = "window.JAVDB_SAMPLES"


def bucket(code, n=N):
    s = "".join(ch for ch in str(code).upper() if ("A" <= ch <= "Z") or ("0" <= ch <= "9"))
    h = 0
    for ch in s:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return h % n


def load_source():
    if not os.path.isfile(SRC):
        return {}
    t = io.open(SRC, encoding="utf-8").read()
    i = t.index(VAR) + len(VAR)
    rest = t[i:].lstrip()
    if rest.startswith("="):
        rest = rest[1:].lstrip()
    d, _ = json.JSONDecoder().raw_decode(rest)
    return d or {}


def rebuild():
    data = load_source()
    if not data:
        print("[smp] 没有 samples.js，先跑 magnet_spider.py")
        return 0, {}
    os.makedirs(SMPDIR, exist_ok=True)
    buckets = {}
    for code, urls in data.items():
        if not urls:
            continue
        buckets.setdefault(bucket(code), {})[code] = urls

    total = 0
    for b in range(N):
        obj = buckets.get(b, {})
        total += sum(len(v) for v in obj.values())
        body = ("/* 剧照分桶 %d/%d：window.JAVDB_SMP_<b>，bucket=Σ(31*char)%%%d；前端按需注入 */\n"
                % (N, b, N)
                + "window.JAVDB_SMP_%d = " % b
                + json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + ";\n")
        io.open(os.path.join(SMPDIR, "smp_%d.js" % b), "w", encoding="utf-8").write(body)

    # 清掉多余旧桶
    keep = {"smp_%d.js" % b for b in range(N)}
    for fn in os.listdir(SMPDIR):
        if fn.startswith("smp_") and fn.endswith(".js") and fn not in keep:
            try:
                os.remove(os.path.join(SMPDIR, fn))
            except OSError:
                pass
    return total, buckets


def main():
    ap = argparse.ArgumentParser(description="剧照地址分桶")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()

    data = load_source()
    if a.check:
        buckets = {}
        for code, urls in data.items():
            if urls:
                buckets.setdefault(bucket(code), []).append((code, len(urls)))
        print("[smp] 番号 %d 个 / 剧照 %d 张" % (len(data), sum(len(v) for v in data.values())))
        for b in range(N):
            rows = buckets.get(b, [])
            print("  桶 %2d: %3d 番号 / %4d 张" % (b, len(rows), sum(n for _, n in rows)))
        return 0

    total, buckets = rebuild()
    print("[smp] 已重建 %d 桶：%d 个番号 / %d 张剧照 -> %s"
          % (len([b for b in buckets if buckets[b]]), len(data), total, SMPDIR))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
