# -*- coding: utf-8 -*-
"""Flush checkpoint (store) into magnets.js now, atomically. Safe to run standalone."""
import json, io, os, shutil, datetime
WB   = r"C:/Users/wh/WorkBuddy/2026-10-10-07-52-17/.workbuddy"
ROOT = r"C:/Users/wh/WorkBuddy/2026-10-10-07-52-17"
CKPT = os.path.join(WB, "magnets_fill_ckpt.json")
SRC  = os.path.join(ROOT, "magnets.js")
BAK  = os.path.join(ROOT, "magnets.js.bak")

def load_js(path, var):
    txt = io.open(path, encoding="utf-8").read()
    i = txt.index(var) + len(var)
    obj, _ = json.JSONDecoder().raw_decode(txt[i:].lstrip())
    return obj

store = json.load(io.open(CKPT, encoding="utf-8"))
mags  = load_js(SRC, "window.JAVDB_MAGNETS = ")

before = len([c for c in mags if mags.get(c)])
added = 0
data = dict(mags)
for c, lst in store.items():
    if c not in data or not data.get(c) or len(lst) > len(data.get(c, [])):
        data[c] = lst
        added += 1
after = len([c for c in data if data.get(c)])

# backup then atomic write
shutil.copy2(SRC, BAK)
tmp = SRC + ".tmp"
with io.open(tmp, "w", encoding="utf-8") as f:
    f.write("/* 磁力链接库：code -> [{link,name,sizeMB,size,files,date,tags}]（自动生成，勿手改） */\n")
    f.write("window.JAVDB_MAGNETS = ")
    f.write(json.dumps(data, ensure_ascii=False, indent=1))
    f.write(";\n")
os.replace(tmp, SRC)
btih = sum(len(v) for v in data.values())
print(f"[{datetime.datetime.now():%H:%M:%S}] flush done: ckpt_codes={len(store)} movies_with_mag_before={before} merged_keys={added} movies_with_mag_after={after} total_magnets={btih}", flush=True)
