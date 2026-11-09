# -*- coding: utf-8 -*-
"""Probe: how many top250 codes have a javdb id, and how many are already downloaded."""
import json, os

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def loadjs(name):
    raw = open(os.path.join(WS, name), encoding="utf-8").read()
    return json.loads(raw[raw.index("{"): raw.rindex("}") + 1])


movies = loadjs("movies.js")
print("movies count:", len(movies))

ranks = loadjs("top250.js")
variants = ranks["top250"]["variants"]
print("variant keys:", " ".join(variants.keys()))

codes = []
seen = set()
for v in variants.values():
    for it in v["items"]:
        c = it["code"]
        if c not in seen:
            seen.add(c)
            codes.append(c)
allc = [it["code"] for it in variants["all"]["items"]]
print("unique codes over all variants:", len(codes))
print("unique codes in 'all' variant:", len(set(allc)))

have = {c: movies[c]["id"] for c in codes if c in movies and movies[c].get("id")}
print("with id:", len(have), " without:", len(codes) - len(have))
print("without sample:", " ".join([c for c in codes if c not in have][:20]))

man = json.load(open(os.path.join(WS, "videos_manifest.json"), encoding="utf-8"))["items"]
mancodes = {v["code"].lower() for v in man.values()}
print("manifest records:", len(man), "present:", sum(1 for v in man.values() if v.get("present")))
print("downloaded (all variants):", sum(1 for c in codes if c.lower() in mancodes))
print("downloaded ('all' variant):", sum(1 for c in set(allc) if c.lower() in mancodes))
todo = [c for c in codes if c in have and c.lower() not in mancodes]
print("todo (has id & not downloaded):", len(todo))
vdir = os.path.join(WS, "videos")
if os.path.isdir(vdir):
    print("files present in videos/:", len(os.listdir(vdir)))
else:
    print("videos/ missing")
