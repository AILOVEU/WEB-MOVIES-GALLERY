# -*- coding: utf-8 -*-
"""本地化演员头像：把 actors.js / series.js 里的 avatar（远程 c0.jdbstatic.com）下到
`assets/avatars/{id}.jpg`。

要点（与封面同一套经验）：
  · c0.jdbstatic.com 走代理会返 403 拦截页冒充成功，所以**直连优先、失败再退代理**；
  · 用 covers_manifest.is_valid_image 按文件头 magic bytes 判真伪，绝不把 HTML 当图片；
  · 已存在且合法 → 跳过（可反复跑/续传）；--force 强制重下；
  · 确实拿不到(403/404/410) → 记进 state/avatars_unavailable.json，下次不再重试；
  · 瞬时失败(000/超时/5xx) 只算 fail，下次会再试。
下完记得跑 build_collection_js.py，给 js 里本地已有文件的条目补 `avatar_local` 字段，
前端 collection.html 会优先用本地头像、没有才显示名字首字。

用法：
  python avatars_localize.py                      # 默认 actors.js，8 路
  python avatars_localize.py --js series.js
  python avatars_localize.py --workers 6 --force
"""
# __JDB_BOOT__ 统一 ROOT/sys.path 引导：脚本可从 _script 任意子目录运行、跨目录彼此 import
import os as _os, sys as _sys
ROOT = _os.path.dirname(_os.path.abspath(__file__))
while not _os.path.isdir(_os.path.join(ROOT, "data")) and ROOT != _os.path.dirname(ROOT):
    ROOT = _os.path.dirname(ROOT)
SR = _os.path.join(ROOT, "_script")
for _d in [SR] + [_os.path.join(SR, _x) for _x in _os.listdir(SR)
               if _os.path.isdir(_os.path.join(SR, _x)) and _x not in ("legacy", "__pycache__") and not _x.startswith(".")]:
    if _d not in _sys.path:
        _sys.path.insert(0, _d)
# __JDB_BOOT_END__
import argparse
import io
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

DATA = os.path.join(ROOT, "data")
SITE = os.path.join(DATA, "site")
STATE = os.path.join(DATA, "state")
ASSETS = os.path.join(ROOT, "assets")
AVDIR = os.path.join(ASSETS, "avatars")
UNAVAIL = os.path.join(STATE, "avatars_unavailable.json")
PROXY = "http://127.0.0.1:7890"

import win_utf8               # noqa: F401  管道/重定向下固定 UTF-8，避免中文/emoji 砸进程
import covers_check as cc     # 复用 _curl_code（带图片 Accept 头 + Referer）
from covers_manifest import is_valid_image

_lock = threading.Lock()
stat = {"ok": 0, "already": 0, "blocked": 0, "fail": 0, "unavail_skip": 0}


def load_items(js):
    p = os.path.join(SITE, js)
    t = io.open(p, encoding="utf-8").read()
    d, _ = json.JSONDecoder().raw_decode(t[t.index("=", t.index("window.")) + 1:].lstrip())
    out = []
    for it in d.get("items", []):
        iid = it.get("id")
        url = (it.get("avatar") or "").strip()
        if iid and url.startswith("http"):
            out.append((iid, url))
    return out


def load_unavail():
    if os.path.exists(UNAVAIL):
        try:
            return set(json.load(io.open(UNAVAIL, encoding="utf-8")).get("ids", []))
        except Exception:
            return set()
    return set()


def save_unavail(ids):
    payload = {
        "version": 1,
        "desc": "CDN 上确实取不到的演员头像（403/404/410），avatars 本地化默认不再重试；"
                "这些演员在前端会退回名字首字",
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(ids),
        "ids": sorted(ids),
    }
    with io.open(UNAVAIL, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def fetch_one(iid, url, force, unavail):
    dst = os.path.join(AVDIR, iid + ".jpg")
    if not force and os.path.exists(dst) and is_valid_image(dst):
        with _lock:
            stat["already"] += 1
        return
    if not force and iid in unavail:
        with _lock:
            stat["unavail_skip"] += 1
        return
    got = False
    blocked = False
    for proxy_on in (False, True):        # 直连优先，失败再走代理
        code = cc._curl_code(url, dst, proxy_on, None)
        if is_valid_image(dst):
            got = True
            break
        if code in ("403", "404", "410"):
            blocked = True
            break
        # 其余（000/超时/5xx）：瞬时失败，换下一种模式再试
    with _lock:
        if got:
            stat["ok"] += 1
            unavail.discard(iid) if iid in unavail else None
        else:
            # 清掉可能留下的非图片垃圾文件，避免脏文件占位
            try:
                if os.path.exists(dst) and not is_valid_image(dst):
                    os.remove(dst)
            except OSError:
                pass
            if blocked:
                stat["blocked"] += 1
                unavail.add(iid)
            else:
                stat["fail"] += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--js", default="actors.js", help="actors.js 或 series.js")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--force", action="store_true", help="已存在也重下、忽略不可得名单")
    args = ap.parse_args()

    os.makedirs(AVDIR, exist_ok=True)
    items = load_items(args.js)
    unavail = load_unavail()
    todo = [x for x in items]
    print(f"[avatars] {args.js}: 共 {len(items)} 个带头像；已下 {stat['already']} 起始为 0，开始下载…", flush=True)
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(fetch_one, iid, url, args.force, unavail) for iid, url in todo]
        for _ in futs:
            pass
    if not args.force:
        save_unavail(unavail)
    dur = time.time() - t0
    print(f"[avatars] 完成：本次新下 {stat['ok']} / 已存在跳过 {stat['already']} / "
          f"确实取不到 {stat['blocked']} / 之前标记跳过 {stat['unavail_skip']} / "
          f"瞬时失败 {stat['fail']}，用时 {dur:.0f}s", flush=True)
    print("下一步跑 build_collection_js.py 给 actors.js/series.js 补 avatar_local 字段。", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
