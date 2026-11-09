# -*- coding: utf-8 -*-
"""
s04i_fetch_body_fast.py — 正文图高速下载（专用通道）

与 s04b 的差异（针对实测瓶颈优化）：
- s04b 按「域名分组 + 每域 Semaphore(4) + 全局 workers」的模型，
  在**单一巨型域**（sinaimg 占待下载量的 86%）上被串行化：
  域内并发上限 4、Limiter 基础间隔 0.06s，吞吐被压到远低于通道能力。
- 实测 sinaimg 通道能力：并发 5/10 均为 **7 张/秒**（2.1 MB/s），与并发数基本无关
  → 是**带宽**上限而非并发上限，瓶颈在数据吞吐而非连接数。
- 本脚本对指定域用「多线程 + 无节流 + 大连接池」直取，实测可跑满带宽。

同时修正 s04b 的失败分类 bug：502/503/504 属暂时性（Cloudflare 回源失败），
不再写入永久失败，下轮自动重试。

用法：
  python s04i_fetch_body_fast.py --only=sinaimg --workers=10
  python s04i_fetch_body_fast.py --only=sinaimg --workers=10 --shards=2 --shard=0
"""
import os
import sys
import time
import json
import random
import threading
import hashlib
import glob
import io
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # _scripts/
import common as C
import s04b_image_policy as POL

import requests
import urllib3
urllib3.disable_warnings()


def mk_session(pool=32):
    """图床会话：trust_env=False 屏蔽系统代理环境变量，但**显式**走 Clash。

    代理端口不通时必须降级直连：实测 sinaimg 直连可 200 拿到原图，
    而代理挂了就全报 ProxyError —— 看起来像「图床死了」，其实是我们自己
    把可取的图归入了「未落地」（§8.1 图床通道只依赖代理可用）。
    """
    s = requests.Session()
    s.trust_env = False
    s.verify = False
    s.headers.update(C.headers("https://weibo.com/"))
    if C.proxy_alive():
        s.proxies.update(C.PROXIES)
    return s


def host_of(url):
    try:
        return url.split("/")[2].lower()
    except Exception:
        return "?"


def ref_for(host):
    if "sinaimg" in host:
        return "https://weibo.com/"
    return "https://fuliba2023.net/"


# sinaimg 的尺寸段：原图段位在部分图上响应极慢（实测单张 max 104~140 秒，
# 体积最大 5.5MB），而同图的 mw690 变体只需 0.6 秒 / 85KB。
# 策略：原图段先试一次，短超时内拿不到就降级到 mw690（小图足够归档浏览）。
SINA_SIZE_FALLBACK = ("mw690", "small")
SINA_ORIG_TIMEOUT = 12      # 原图段单次超时（秒）
SINA_FALLBACK_TIMEOUT = 20  # 降级段单次超时（秒）
SINA_ORIG_MAX_BYTES = 800 * 1024   # 原图超过此大小直接降级，不浪费带宽


def sina_variants(url):
    """给 sinaimg 的 URL 生成 (原图, 降级1, 降级2) 三个候选。"""
    parts = url.split("/")
    # https://host/<size>/<path...>  →  https://host/<mw690>/<path...>
    if len(parts) < 5 or "." not in parts[-1]:
        return [url]
    size = parts[3]
    tail = "/".join(parts[4:])
    out = [url]
    for alt in SINA_SIZE_FALLBACK:
        if alt != size:
            out.append("https://%s/%s/%s" % (parts[2], alt, tail))
    return out


# 像素数上限：超过此值的图直接跳过（不落地）。
# 实测 sinaimg 有 9800 万像素的巨图，PIL 解码一次要吃掉数百 MB 内存 + 数十秒，
# 是「下载卡住」的隐形杀手（表现为 DecompressionBombWarning + 进程 WS 飙到 660MB）。
MAX_PIXELS = 40_000_000


def too_many_pixels(data):
    """轻量像素数预检：只读图片头，不做完整解码。"""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        w, h = im.size
        im.close()
        return (w * h) > MAX_PIXELS
    except Exception:
        return False


def ext_of(data, ctype):
    ct = (ctype or "").split(";")[0].strip().lower()
    m = {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
         "image/gif": ".gif", "image/webp": ".webp", "image/bmp": ".bmp"}
    if ct in m:
        return m[ct]
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:4] == b"\x89PNG":
        return ".png"
    if data[:4] == b"GIF8":
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".bin"


def store_image(url, ext, data):
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]
    d = os.path.join(C.IMAGES_DIR, h[:2])
    os.makedirs(d, exist_ok=True)
    target = os.path.join(d, h + ext)
    for sib in glob.glob(os.path.join(d, h + ".*")):
        if os.path.abspath(sib) == os.path.abspath(target):
            continue
        try:
            if 0 < os.path.getsize(sib) <= len(data):
                return sib
            with open(sib, "wb"):
                pass
        except OSError:
            pass
    with open(target, "wb") as f:
        f.write(data)
    return target


def rel_from_root(p):
    """落盘路径 → 台账图片键 `images/<xx>/<hash>.ext`。
    不准改回 `os.path.relpath(p, ROOT)`：图片库已在 assets/images/ 下，
    那会造出第二种前缀的键，打断 s04d 的幂等判定（见 common.img_key_from_disk）。"""
    return C.img_key_from_disk(p)


def load_body_urls(keys=None):
    """从年度容器的 parsed 段收集正文图 URL（顶层 images 已含各分页并集）。

    keys 给了就只读那些篇（过滤在 open() 之前）。全树读一次要 20 多秒，
    而增量轮真正的新图只可能出现在本轮新增/重解析的那几篇里：
    旧文章的图要么已在 manifest（不重下），要么已被 2026-10-29 清掉地址（不会重现）。
    """
    body = set()
    for _u, _k, _slot, a in C.Y.iter_parsed(keys=keys):
        for im in a.get("images", []):
            if im.get("url"):
                body.add(im["url"])
    return sorted(body)


def safe_write_manifest(mpath, man, local):
    """合并式落盘：先重读磁盘 manifest 再叠加 local，避免与 s04h/s04b 并发写互相覆盖。"""
    disk = C.read_json(mpath, {}) or {}
    disk.update(local)
    C.write_json(mpath, disk)
    man.clear()
    man.update(disk)


def disk_has(url):
    """直接按 sha1[:20] 查磁盘上是否已有非空文件（不依赖 manifest，
    避免 manifest 被并发写覆盖后重复下载）。"""
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]
    d = os.path.join(C.IMAGES_DIR, h[:2])
    for p in glob.glob(os.path.join(d, h + ".*")):
        try:
            if os.path.getsize(p) > 0:
                return True
        except OSError:
            continue
    return False


def main():
    st = C.guard_or_exit("s04i")      # 拔盘/破损 -> 直接退出，绝不往本机写图
    only = None
    # 必须显式初始化：下面 `if list_path:` 无条件引用，缺省参数时否则 UnboundLocalError
    list_path = None
    workers = 10
    shards = 1
    shard = 0
    limit = 0
    allow_mass = False
    for a in sys.argv[1:]:
        if a.startswith("--only="):
            only = a.split("=", 1)[1]
        elif a.startswith("--workers="):
            workers = int(a.split("=", 1)[1])
        elif a.startswith("--shards="):
            shards = int(a.split("=", 1)[1])
        elif a.startswith("--shard="):
            shard = int(a.split("=", 1)[1])
        elif a.startswith("--limit="):
            limit = int(a.split("=", 1)[1])
        elif a.startswith("--list="):
            list_path = a.split("=", 1)[1]
        elif a == "--allow-mass-download":
            allow_mass = True
    # 本轮增量范围（update.py 传的）：只从这些篇里收集待下载图；不给 = 全树扫
    scope = C.scope_read(C.scope_arg())

    mpath = os.path.join(C.SRC, "image_manifest.json")
    man = C.read_json(mpath, {}) or {}
    new_only = C.new_images_only()
    if new_only:
        C.log("[s04i] 图片库模式=incremental（历史图在别的电脑）："
              "manifest 有条目即视为已下载，本轮只下 manifest 里没有的新图 → 本机 images/")
    if list_path:
        urls = C.read_json(list_path, []) or []
    else:
        urls = load_body_urls(keys=C.scope_keys(scope) if scope else None)
        if scope:
            C.log("[s04i] 范围模式：只从本轮 %d 篇收集正文图 URL（全树未扫）" % len(scope))

    def have(u):
        e = man.get(u) or {}
        loc = e.get("local")
        if not loc:
            return False
        if new_only:
            # 增量本机模式：历史图已被移到别的电脑，本机 stat 不到是常态。
            # manifest 有条目就算「已下载」，绝不因为「本机看不到文件」重下历史图；
            # 真正要下的只有 manifest 里根本没出现的那些 URL（= 本轮新增文章的图）。
            return True
        fp = C.img_disk_path(loc)
        # 0 字节 = 早期截断的空壳，不算已下载
        try:
            return os.path.exists(fp) and os.path.getsize(fp) > 0
        except OSError:
            return False

    todo = [u for u in urls if not have(u) and not disk_has(u)]
    if only:
        todo = [u for u in todo if only in host_of(u)]
    if shards > 1:
        def key(u):
            return int(hashlib.sha1(u.encode("utf-8")).hexdigest()[:8], 16)
        todo = [u for u in todo if key(u) % shards == shard]
    if limit:
        todo = todo[:limit]

    C.log("[s04i] 域=%s 分片 %d/%d workers=%d | 待下载 %d"
          % (only or "all", shard, shards, workers, len(todo)))
    # 全量重下熔断：图片库外迁后，「待下载」正常只等于本轮新增文章的图。
    # 超过阈值说明图片库不可用/迁移没做完/junction 被真实目录顶掉了，
    # 此时继续下载会往本机灌几十 GB，正是禁止的情形。
    cap = int(C.ST.load_cfg().get("max_new_images_per_run") or 0)
    if cap and len(todo) > cap and not (allow_mass or limit):
        C.log("[s04i] 熔断：待下载 %d > 单轮上限 %d（模式=%s）。"
              "请先确认移动硬盘已插入、迁移已做完（_scripts\\py.cmd _scripts\\store.py --status），"
              "确需批量补下再加 --allow-mass-download" % (len(todo), cap, st))
        print("S04I_BLOCKED_MASS_DOWNLOAD %d>%d" % (len(todo), cap))
        return 6
    if not todo:
        print("S04I_OK 0 0 0")
        return 0

    part = os.path.join(C.SRC, "image_manifest_part_fast%s%d.json" % (only or "all", shard))
    local = {}
    lock = threading.Lock()
    cnt = {"ok": 0, "fail": 0, "skip": 0}
    t0 = time.time()
    tls = threading.local()

    def one(u):
        s = getattr(tls, "s", None)
        if s is None:
            s = mk_session()
            tls.s = s
        ref = ref_for(host_of(u))
        h = C.headers(ref)
        host = host_of(u)
        variants = sina_variants(u) if "sinaimg" in host else [u]

        for vi, vu in enumerate(variants):
            is_sina = "sinaimg" in host
            if is_sina:
                tmo = SINA_ORIG_TIMEOUT if vi == 0 else SINA_FALLBACK_TIMEOUT
            else:
                tmo = 25
            try:
                r = s.get(vu, headers=h, timeout=tmo)
            except Exception:
                # 原图段超时 → 试降级段
                continue
            try:
                if r.status_code == 200:
                    data = r.content
                    if data and len(data) >= 100:
                        # 原图过大 → 不落地，直接降级（省带宽）
                        if is_sina and vi == 0 and len(data) > SINA_ORIG_MAX_BYTES:
                            continue
                        cdata, cext = POL.compress_bytes(data, r.headers.get("Content-Type", ""))
                        if cdata is not None:
                            data, ext = cdata, cext
                        else:
                            ext = ext_of(data, r.headers.get("Content-Type", ""))
                            if len(data) > POL.HARD_KEEP_KB * 1024:
                                with lock:
                                    cnt["skip"] += 1
                                return
                        # §8.2 铁律：只存魔数识别为图片的字节。.bin = 非图片（图床对失效路径
                        # 返回 200 的 HTML 错误页），必须丢弃，不得落盘，否则离线站破图。
                        if ext == ".bin":
                            with lock:
                                cnt["skip"] += 1
                            return
                        path = store_image(u, ext, data)
                        with lock:
                            local[u] = {"local": rel_from_root(path),
                                        "bytes": os.path.getsize(path),
                                        "ctype": r.headers.get("Content-Type", ""), "ok": True}
                            cnt["ok"] += 1
                        return
                    # 响应体过小 → 可能是错误页，换下一个候选
                    continue
                # 5xx / 429 / 403 → 暂时失败，不写 manifest，下轮重试
                if r.status_code in (403, 404, 410):
                    break
            finally:
                try:
                    r.close()
                except Exception:
                    pass

        with lock:
            cnt["fail"] += 1

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(one, u) for u in todo]
        for i, f in enumerate(futs):
            f.result()
            if (i + 1) % 1000 == 0:
                el = time.time() - t0
                C.log("[s04i] 进度 %d/%d  成功 %d 失败 %d 跳过 %d  速率 %.2f 张/秒"
                      % (i + 1, len(todo), cnt["ok"], cnt["fail"], cnt["skip"],
                         (i + 1) / max(el, 0.001)))
                with lock:
                    safe_write_manifest(mpath, man, local)
                    local = {}

    with lock:
        safe_write_manifest(mpath, man, local)
    C.log("[s04i] 完成：成功 %d 失败 %d 跳过 %d，用时 %.0fs"
          % (cnt["ok"], cnt["fail"], cnt["skip"], time.time() - t0))
    print("S04I_OK %d %d %d" % (cnt["ok"], cnt["fail"], cnt["skip"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
