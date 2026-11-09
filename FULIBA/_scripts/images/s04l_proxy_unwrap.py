# -*- coding: utf-8 -*-
"""
s04l_proxy_unwrap.py — 站点图片代理地址还原下载

背景：正文里有一类图片地址形如
  https://fuliba2025.net/s.php?u=tvax4.sinaimg.cn/large/xxx.jpg
这是站点自建的取图代理。**代理本身已 404**（站点侧失效），
但 `u=` 参数里藏着真实原图地址 —— 实测 60/60 直取成功。

本脚本：还原内层地址 → 直取 → 按**原始 URL** 的哈希落盘并记账
（保证 s04d_rewrite 的 URL→local 映射仍然对得上）。

用法: python s04l_proxy_unwrap.py
"""
import os, sys, json, time, threading, hashlib, glob
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # _scripts/
import common as C
import s04b_image_policy as POL
import s04i_fetch_body_fast as FI

import requests, urllib3
urllib3.disable_warnings()

MAP = os.path.join(C.SRC, "unwrap_all.json")
if not os.path.exists(MAP):
    MAP = os.path.join(C.SRC, "proxy_unwrap_map.json")


def main():
    C.guard_or_exit("s04l")          # 图片库不可用时不下载、不改 manifest
    mapping = C.read_json(MAP, {}) or {}
    if not mapping:
        # 一次性收尾脚本：没有待还原的代理地址就 nothing-to-do（不能算失败，
        # 否则 update/rebuild 会在 s04l 之后「失败即停」，跳不到 s04f/s06）。
        C.log("[s04l] 无 %s，无站点代理图待还原，跳过" % os.path.basename(MAP))
        print("S04L_SKIP no_unwrap_map")
        return 0

    mpath = os.path.join(C.SRC, "image_manifest.json")
    man = C.read_json(mpath, {}) or {}

    # 只处理磁盘上还没有的
    disk = set()
    for p in glob.glob(os.path.join(C.IMAGES_DIR, "*", "*")):
        b = os.path.basename(p)
        if "." in b:
            try:
                if os.path.getsize(p) > 0:
                    disk.add(b.rsplit(".", 1)[0])
            except OSError:
                pass

    def on_disk(u):
        return hashlib.sha1(u.encode("utf-8")).hexdigest()[:20] in disk

    todo = [(u, i) for u, i in mapping.items() if not on_disk(u)]
    C.log("[s04l] 可还原 %d | 待下载 %d" % (len(mapping), len(todo)))
    if not todo:
        return 0

    tls = threading.local()
    local = {}
    lock = threading.Lock()
    cnt = {"ok": 0, "fail": 0}

    def one(item):
        u, inner = item
        s = getattr(tls, "s", None)
        if s is None:
            s = requests.Session()
            s.trust_env = False
            s.verify = False
            if C.proxy_alive():      # 代理不通时降级直连（同 s04i）
                s.proxies.update(C.PROXIES)
            tls.s = s
        host = inner.split("/")[2].lower()
        ref = "https://weibo.com/" if "sinaimg" in host else "https://fuliba2023.net/"
        h = C.headers(ref)
        for vu in FI.sina_variants(inner):
            try:
                r = s.get(vu, headers=h, timeout=20)
            except Exception:
                continue
            try:
                if r.status_code == 200:
                    data = r.content
                    if data and len(data) >= 100:
                        if POL.too_many_pixels(data):
                            continue
                        cdata, cext = POL.compress_bytes(data, r.headers.get("Content-Type", ""))
                        if cdata is not None:
                            data, ext = cdata, cext
                        else:
                            ext = FI.ext_of(data, r.headers.get("Content-Type", ""))
                            if len(data) > POL.HARD_KEEP_KB * 1024:
                                continue
                        if ext == ".bin":   # §8.2：非图片(HTML 错误页)不落盘
                            continue
                        # 按**原始 URL** 落盘，保证 URL→local 映射正确
                        path = FI.store_image(u, ext, data)
                        with lock:
                            local[u] = {"local": FI.rel_from_root(path),
                                        "bytes": os.path.getsize(path),
                                        "ctype": r.headers.get("Content-Type", ""),
                                        "ok": True, "via": "proxy_unwrap",
                                        "origin": inner}
                            cnt["ok"] += 1
                        return
            finally:
                try:
                    r.close()
                except Exception:
                    pass
        with lock:
            cnt["fail"] += 1

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = [pool.submit(one, it) for it in todo]
        for i, f in enumerate(futs):
            f.result()
            if (i + 1) % 100 == 0:
                C.log("[s04l] 进度 %d/%d 成功 %d 失败 %d 速率 %.1f/s"
                      % (i + 1, len(todo), cnt["ok"], cnt["fail"],
                         (i + 1) / max(time.time() - t0, 0.001)))

    diskman = C.read_json(mpath, {}) or {}
    diskman.update(local)
    C.write_json(mpath, diskman)
    C.log("[s04l] 完成：成功 %d 失败 %d" % (cnt["ok"], cnt["fail"]))
    print("S04L_OK %d %d" % (cnt["ok"], cnt["fail"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
