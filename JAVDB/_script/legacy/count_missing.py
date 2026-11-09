# -*- coding: utf-8 -*-
import json, io, os
ROOT = r"C:/Users/wh/WorkBuddy/2026-10-10-07-52-17"
def load_js(path, var):
    txt = io.open(os.path.join(ROOT, path), encoding="utf-8").read()
    i = txt.index(var) + len(var)
    obj, _ = json.JSONDecoder().raw_decode(txt[i:].lstrip())
    return obj
movies = load_js("movies.js", "window.JAVDB_MOVIES = ")
mags = load_js("magnets.js", "window.JAVDB_MAGNETS = ")
missing = [c for c in movies if c not in mags or not mags[c]]
print(f"movies={len(movies)} magnet_codes={len(mags)} missing={len(missing)}")
# old checkpoint?
ck = os.path.join(ROOT, ".workbuddy", "magnets.json")
if os.path.exists(ck):
    st = load_js(".workbuddy/magnets.json", "")  # raw json file
    st = json.load(io.open(ck, encoding="utf-8"))
    print(f"checkpoint ids={len(st)}")
