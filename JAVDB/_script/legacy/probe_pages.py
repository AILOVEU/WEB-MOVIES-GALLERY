# -*- coding: utf-8 -*-
"""Probe real page count for collection lists and a sample item by requesting ?page=N until empty."""
import io, os, re, subprocess, time, html as htmllib

WB = r"C:/Users/wh/WorkBuddy/2026-10-10-07-52-17/.workbuddy"
CK = io.open(os.path.join(WB, "javdb_cookie.txt"), encoding="utf-8").read().strip()
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def curl(url, timeout=30):
    r = subprocess.run(["curl", "-s", "--ssl-no-revoke", "--max-time", str(timeout),
                        "-x", "http://127.0.0.1:7890", "-A", UA, "-H", f"Cookie: {CK}",
                        "-H", "Referer: https://javdb.com/",
                        "-H", "Accept-Language: zh-TW,zh;q=0.9,en;q=0.8", url],
                       capture_output=True, text=True, encoding="utf-8", errors="ignore")
    return r.stdout or ""


ACTOR = re.compile(r'<div class="box actor-box" id="actor-([^"]+)"', re.S)
SERIES = re.compile(r'<div class="box" id="series-([^"]+)"', re.S)
MITEM = re.compile(r'<div class="item">\s*<a href="(/v/[^"]+)"', re.S)


def count(html, kind):
    if kind == "actor":
        return len(set(ACTOR.findall(html)))
    if kind == "series":
        return len(set(SERIES.findall(html)))
    return len(MITEM.findall(html))


def probe(base, kind, start=1, limit=40, gap=2.0):
    print(f"--- probe {kind}: {base} ---", flush=True)
    empty_streak = 0
    p = start
    total = 0
    while p <= limit:
        url = base if p == 1 else f"{base}?page={p}"
        h = curl(url)
        if "sign_in" in h[:3000] or len(h) < 2000:
            print(f"  page{p}: LOGIN WALL / empty (len={len(h)}) STOP", flush=True)
            break
        n = count(h, kind)
        total += n
        print(f"  page{p}: items={n}", flush=True)
        if n == 0:
            empty_streak += 1
            if empty_streak >= 1:
                print(f"  -> page{p} empty, last page = {p-1}", flush=True)
                break
        else:
            empty_streak = 0
        p += 1
        time.sleep(gap)
    print(f"  TOTAL items={total}\n", flush=True)
    return total


if __name__ == "__main__":
    probe("https://javdb.com/users/collection_actors", "actor", limit=30)
    probe("https://javdb.com/users/collection_series", "series", limit=30)
    probe("https://javdb.com/actors/Q7G7", "movie", limit=30)
    probe("https://javdb.com/series/e28d", "movie", limit=30)
