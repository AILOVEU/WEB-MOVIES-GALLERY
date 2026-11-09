# -*- coding: utf-8 -*-
"""Add 'video' field to both config.js based on previews.json manifest."""
import json, io, os

WS = r"C:\Users\wh\WorkBuddy\2026-10-10-07-52-17"
WB = os.path.join(WS, ".workbuddy")
prev = json.load(io.open(os.path.join(WB, "previews.json"), encoding="utf-8"))
prev_map = {x["id"]: x for x in prev if x.get("url") and x["url"] != "ERROR"}
print("preview manifest:", len(prev_map))

for gall_f, name in [("javdb_watched/config.js", "watched"), ("javdb_want/config.js", "want")]:
    path = os.path.join(WS, gall_f)
    t = io.open(path, encoding="utf-8").read()
    cfg = json.loads(t[t.index("{"): t.rindex("}") + 1])
    n = 0
    for v in cfg["videos"]:
        p = prev_map.get(v["id"])
        if p and p.get("gall") == name:
            code = v["code"].replace("/", "_")
            v["video"] = f"../javdb_videos/{code}.mp4"
            n += 1
        else:
            v["video"] = ""
    head = t[: t.index("window.SITE_CONFIG")]
    io.open(path, "w", encoding="utf-8").write(
        head + "window.SITE_CONFIG = " + json.dumps(cfg, ensure_ascii=False, indent=2) + ";\n")
    print(name, "videos with preview:", n)
