# -*- coding: utf-8 -*-
import json, io, os
ROOT = r"C:/Users/wh/WorkBuddy/2026-10-10-07-52-17"
OUT = os.path.join(ROOT, "top250.js")
txt = io.open(OUT, encoding="utf-8").read()
start = txt.index("window.JAVDB_RANKINGS = ") + len("window.JAVDB_RANKINGS = ")
data, _ = json.JSONDecoder().raw_decode(txt[start:])
keep = {"top250": data["top250"]}
with io.open(OUT, "w", encoding="utf-8") as f:
    f.write("/* JavDB TOP250 排行榜合集（自动生成，勿手改） */\n")
    f.write("window.JAVDB_RANKINGS = ")
    f.write(json.dumps(keep, ensure_ascii=False, indent=1))
    f.write(";\n")
    f.write("window.JAVDB_TOP250 = window.JAVDB_RANKINGS.top250.variants.all.items;\n")
n = sum(len(v["items"]) for v in keep["top250"]["variants"].values())
print(f"kept top250 only: {n} entries across {len(keep['top250']['variants'])} variants")
