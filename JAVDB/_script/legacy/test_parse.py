# -*- coding: utf-8 -*-
import io, re, json, importlib.util, sys, html as htmllib
spec = importlib.util.spec_from_file_location("spider", r"C:/Users/wh/WorkBuddy/2026-10-10-07-52-17/.workbuddy/magnet_fill_spider.py")
# can't import (runs main); instead copy parse essentials
src = io.open(r"C:/Users/wh/WorkBuddy/2026-10-10-07-52-17/.workbuddy/magnet_fill_spider.py", encoding="utf-8").read()
ns = {}
# exec only up to task list
head = src.split("# ---- task list ----")[0]
exec(compile(head, "spider_head", "exec"), ns)
h = io.open(r"C:/Users/wh/WorkBuddy/2026-10-10-07-52-17/.workbuddy/probe_detail.html", encoding="utf-8", errors="ignore").read()
status, items = ns["parse"](h)
print("status:", status, "items:", len(items))
print(json.dumps(items[:2], ensure_ascii=False, indent=1))
