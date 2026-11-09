# -*- coding: utf-8 -*-
"""Merge TOP250 variant items' full info into movies.js; slim top250.js to {rank, code}."""
import json, io, os

ROOT = r"C:/Users/wh/WorkBuddy/2026-10-10-07-52-17"

def load_js(path, var):
    txt = io.open(os.path.join(ROOT, path), encoding="utf-8").read()
    i = txt.index(var) + len(var)
    obj, _ = json.JSONDecoder().raw_decode(txt[i:].lstrip())
    return obj

movies = load_js("movies.js", "window.JAVDB_MOVIES = ")
rankings = load_js("top250.js", "window.JAVDB_RANKINGS = ")

added = kept = 0
top = rankings["top250"]
for vk, v in top["variants"].items():
    new_items = []
    for it in v["items"]:
        code = (it.get("code") or "").strip()
        if not code:
            continue
        new_items.append({"rank": it["rank"], "code": code})
        if code in movies:
            kept += 1
        else:
            movies[code] = {
                "id": it.get("id", ""),
                "code": code,
                "title": it.get("title", ""),
                "url": it.get("url", ""),
                "img": it.get("cover", ""),
                "score": it.get("score"),
                "votes": it.get("votes"),
                "date": it.get("date", ""),
                "tags": it.get("tags", []),
                "local": "covers/%s.jpg" % code,
            }
            added += 1
    v["items"] = new_items

with io.open(os.path.join(ROOT, "movies.js"), "w", encoding="utf-8") as f:
    f.write("/* 全量影片静态信息，key=规范化番号code（原JavDB id保留在id字段）（自动生成） */\n")
    f.write("window.JAVDB_MOVIES = ")
    f.write(json.dumps(movies, ensure_ascii=False, indent=1))
    f.write(";\n")

with io.open(os.path.join(ROOT, "top250.js"), "w", encoding="utf-8") as f:
    f.write("/* TOP250 排行榜：仅结构（rank+code），影片详情统一从 movies.js 按 code 取（自动生成，勿手改） */\n")
    f.write("window.JAVDB_RANKINGS = ")
    f.write(json.dumps(rankings, ensure_ascii=False, indent=1))
    f.write(";\n")

total_items = sum(len(v["items"]) for v in top["variants"].values())
print(f"movies: added={added} already_had={kept} total={len(movies)}")
print(f"top250.js: {len(top['variants'])} variants, {total_items} slim items")
