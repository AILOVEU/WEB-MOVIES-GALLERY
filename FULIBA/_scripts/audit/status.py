# -*- coding: utf-8 -*-
"""实时进度面板：抓取/解析/图片/建站 各阶段状态统计（结果写 _logs/<日期>/_stat.txt）。"""
import glob
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # _scripts/
import common as C

out = C.log_path("_stat.txt")        # _logs/<今天>/_stat.txt
lines = []


def w(s):
    lines.append(s)


def alive(pattern):
    """统计在跑的 python 抓取进程数。

    注意：不要用 PowerShell 子进程 + 引号嵌套（Windows 下极易被外层引号截断），
    改用 tasklist 按进程名计数，稳定无转义问题。
    """
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq python.exe", "/NH"],
                           capture_output=True, text=True, timeout=30)
        n = sum(1 for line in (r.stdout or "").splitlines()
                if "python.exe" in line.lower())
        return n
    except Exception:
        return -1


urls = C.read_json(os.path.join(C.SRC, "urls.json")) or {}
total = len(urls)

yst = C.Y.stats()
n_html = yst["with_raw"]
sizes = []
for _u, _k, _slot, _m, p in C.Y.iter_meta():
    try:
        sizes.append(os.path.getsize(p))
    except OSError:
        pass

st = C.state_load("html")
done = [k for k, v in st.items() if v.get("status") == "DONE"]
fail = [k for k, v in st.items() if v.get("status") == "FAIL"]
circ = [k for k, v in st.items() if v.get("status") == "CIRCUIT"]

w("=== fuliba2023 抓取进度 %s ===" % time.strftime("%Y-%m-%d %H:%M:%S"))
w("")
w("【阶段 1 抓取 HTML（年度容器）】")
w("  目标总数      : %d" % total)
w("  已落盘        : %d  (%.1f%%)" % (n_html, n_html * 100.0 / max(total, 1)))
w("  剩余          : %d" % (total - n_html))
w("  台账 DONE     : %d" % len(done))
w("  台账 FAIL     : %d" % len(fail))
w("  熔断记录      : %d" % len(circ))
w("  活跃进程      : %d" % alive("s02_fetch_html"))
if sizes:
    avg = sum(sizes) / float(len(sizes))
    w("  平均单篇容器    : %.1f KB（原始+解析+子页）" % (avg / 1024.0))
    w("  原料占用     : %.0f MB" % (sum(sizes) / 1048576.0))
    w("  全量预估     : %.0f MB" % (avg * total / 1048576.0))

# 图片
imgs = glob.glob(os.path.join(C.IMAGES_DIR, "*", "*"))
imgs = [p for p in imgs if os.path.isfile(p)]
w("")
w("【阶段4 图片落地】")
w("  本地图片      : %d" % len(imgs))

# 解析产物（年度容器）
st = C.Y.stats()
w("")
w("【阶段3 解析】")
w("  年度容器      : %d（含解析 %d / 含原始 HTML %d / 子页 %d）"
  % (st["containers"], st["with_parsed"], st["with_raw"], st["subpages"]))
w("  评论合计      : %d 条（meta 行汇总）" % st["comments"])
w("  年份分布      : %s" % sorted(st["by_year"].items()))

# 站点产物
site = os.path.join(C.ROOT, "index.html")
if os.path.exists(site):
    pages = C.Y.pages_total()
    w("")
    w("【阶段6 离线站】")
    w("  文章页        : %d（分片 <年>/part<i>.html 内锚点）" % pages)
    w("  index.html    : 已生成")

# D 盘容量
try:
    import shutil
    du = shutil.disk_usage("D:\\")
    w("")
    w("【容量】")
    w("  D 盘可用     : %.1f GB" % (du.free / 1073741824.0))
except Exception:
    pass

with open(out, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
print("written", out)
