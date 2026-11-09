# -*- coding: utf-8 -*-
"""生成 data/remote_urls.js —— 影片 id → 原站图片 URL 映射表

数据来源（均为已抓取的离线文件, 不访问网络）:
  _rawdata/_raw_posts.json  items[].img    → 原始海报 URL
  _rawdata/details.json     {id}.shots     → 原始剧照 URL 列表

输出 data/remote_urls.js, 供 index.html 的「原始图片模式」使用。
URL 若以 9dy 的 uploads 目录开头则去掉公共前缀（文件内以常量拼回），减小体积。
幂等: 源文件不变则输出不变, 可反复执行。

用法: python _scripts/pipeline/08_build_remote_urls.py [--dir=<画廊目录>]
"""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import gallery_dir, meta_file

GDIR = gallery_dir()
POSTS = meta_file(GDIR, "_raw_posts.json")
DETAILS = meta_file(GDIR, "details.json")
OUT = os.path.join(GDIR, "data", "remote_urls.js")

PREFIX = "https://www.9dy.top/wp-content/uploads/"

with open(POSTS, encoding="utf-8") as f:
    posts = {it["id"]: it for it in json.load(f)["items"]}
with open(DETAILS, encoding="utf-8") as f:
    details = json.load(f)

mapping = {}
n_poster = n_shots = 0
odd_urls = []            # 非 uploads 前缀的 URL（保留完整路径）
for pid, it in posts.items():
    img = (it.get("img") or "").strip()
    if img:
        mapping[str(pid)] = {"p": img}
        n_poster += 1
        if not img.startswith(PREFIX):
            odd_urls.append(img)

for pid, det in details.items():
    shots = det.get("shots") or []
    if not shots or det.get("error"):
        continue
    rec = mapping.setdefault(str(pid), {})
    rec["sh"] = [s for s in shots if s]
    n_shots += 1

# 序列化: 9dy uploads 下的 URL 去公共前缀
def enc(u):
    if u.startswith(PREFIX):
        return u[len(PREFIX):]
    return "~" + u          # "~" 开头表示完整 URL（非 9dy uploads）

out = {
    "v": 1,
    "rp": PREFIX,
    "m": {pid: {k: [enc(u) for u in v["sh"]] if k == "sh"
                else enc(v[k]) for k in v}
          for pid, v in mapping.items()},
}

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    f.write("/* 自动生成: _scripts/pipeline/08_build_remote_urls.py — 影片 id → 原站图片 URL */\n")
    f.write("window.REMOTE_URLS=")
    json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    f.write(";\n")

size = os.path.getsize(OUT)
print("poster:%d shots-ids:%d movies:%d -> %s (%.1f KB)" % (n_poster, n_shots, len(mapping), OUT, size / 1024))
if odd_urls:
    print("non-9dy URLs kept verbatim: %d (e.g. %s)" % (len(odd_urls), odd_urls[0]))
