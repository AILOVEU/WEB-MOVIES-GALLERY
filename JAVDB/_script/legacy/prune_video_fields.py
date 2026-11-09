# -*- coding: utf-8 -*-
"""Prune video fields in config.js that point to non-existent files.
Run after any video download batch to keep configs truthful."""
import json, io, os, re

ROOT = r"C:\Users\wh\WorkBuddy\2026-10-10-07-52-17"
VID = os.path.join(ROOT, "javdb_videos")

def load_cfg(path):
    t = io.open(path, encoding="utf-8").read()
    body = t[t.find("{"):]
    cfg = json.loads(body.rsplit(";", 1)[0].strip())
    return cfg

def save_cfg(path, cfg, orig):
    # preserve original header (window.SITE_CONFIG = ...) and trailing ;
    head = orig[:orig.find("{")]
    txt = head + json.dumps(cfg, ensure_ascii=False, indent=1) + ";\n"
    io.open(path, "w", encoding="utf-8").write(txt)

for name, folder in [("javdb_watched", "javdb_watched"), ("javdb_want", "javdb_want")]:
    p = os.path.join(ROOT, folder, "config.js")
    orig = io.open(p, encoding="utf-8").read()
    cfg = load_cfg(p)
    kept = removed = 0
    for v in cfg["videos"]:
        vf = v.get("video")
        if vf:
            fn = os.path.basename(vf)
            if os.path.exists(os.path.join(VID, fn)) and os.path.getsize(os.path.join(VID, fn)) > 50000:
                kept += 1
            else:
                del v["video"]
                removed += 1
    save_cfg(p, cfg, orig)
    print(f"{name}: video kept={kept} pruned={removed}")
