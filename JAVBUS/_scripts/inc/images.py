# -*- coding: utf-8 -*-
"""下载所有原图 + 头像到本地（幂等、断点续传、并发）。

约定（保留原图）：
- 内容图：assets/images/{tid}/{原名}（imgur 哈希名天然唯一；同 tid 内冲突才追加序号）。**不缩放/不重编码**。
- 头像：  assets/avatars/{原名}（uc 头像按 uid 分片名，唯一）。（目前策略不抓头像）
- 映射：  _pipeline/imgmap.json = {url: "assets/images/tid/name"}，供 build.py 改写 HTML（永久台账）。

下载：curl --ssl-no-revoke -x 代理 -e referer，存在且体积>2000B 跳过；失败重试；直接写目标（无 tmp）。
落盘位置：一律写本机 `assets/images/`（缺则自动重建）。把整份 JAVBUS 复制到移动硬盘、再删本机
`assets/images/` 是每轮收尾的人工步骤，本脚本不参与；imgmap.json 里的历史条目因此不会因本机字节消失而被清掉。
用法： python _scripts/inc/images.py all [--workers=6] [--only=tid,tid]
（台账本身已是累积式的：只更新本轮真正下过的 URL，历史条目一律保留，所以没有 --rebuild-map。）
失败清单写 `_logs/<YYYYMMDD>_img_fail.txt`（属日志区，随时可删，当日重跑会重写；日期前缀由
 paths.dated() 自动加上）。
"""
import argparse
import concurrent.futures as cf
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit, urlunsplit, quote

_SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # _scripts
sys.path.insert(0, os.path.join(_SCRIPTS, "common"))                      # 公共库：paths/discuz/pannet
import paths as P

ROOT = P.ROOT
ASSETS_DIR = P.ASSETS              # 静态资源统一入口
IMG_DIR = P.IMAGES                 # assets/images：图片字节唯一落盘地
AV_DIR = os.path.join(ASSETS_DIR, "avatars")
IMG_REL = P.IMG_REL                # 台账/引用里用的 posix 相对路径前缀
AV_REL = P.AV_REL
PARSED_DIR = P.PARSED              # 过程区：读 parsed/*.json 里的图片 URL
MAP_PATH = P.IMGMAP                # 过程区：永久台账
IMG_FAIL = P.IMG_FAIL              # 日志区：失败清单，删了不丢数据
PROXY = os.environ.get("JAVBUS_PROXY", "http://127.0.0.1:7890")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def log(*a):
    print("[%s]" % time.strftime("%H:%M:%S"), *a, flush=True)


def safe_name(url, idx=0):
    """从 URL 取文件名；无扩展/附件 aid 兜底。"""
    path = url.split("?")[0].split("#")[0]
    base = os.path.basename(path).strip()
    if not base or "." not in base:
        # 附件 aid=NNN
        m = re.search(r"aid[=_](\d+)", url)
        base = ("aid_%s" % m.group(1)) if m else hashlib.md5(url.encode()).hexdigest()[:12]
        base += ".jpg"
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base)
    if idx:
        stem, ext = os.path.splitext(base)
        base = "%s_%d%s" % (stem, idx, ext)
    return base


def gather():
    """从 parsed/*.json 收集内容原图 (tid, url)。**头像不抓取**（用户要求，前端用缺省占位图）。"""
    content = []
    for fn in sorted(os.listdir(PARSED_DIR)) if os.path.isdir(PARSED_DIR) else []:
        if not fn.endswith(".json"):
            continue
        d = json.load(io.open(os.path.join(PARSED_DIR, fn), encoding="utf-8"))
        tid = d["tid"]
        for u in d["images"]:
            content.append((tid, u))
    return content


def target_for(kind, tid, url, used):
    if kind == "avatar":
        # 头像按 URL 哈希命名（uc 路径尾段会跨 uid 碰撞，如 .../22_avatar_middle.jpg）
        ext = os.path.splitext(url.split("?")[0])[1] or ".jpg"
        ext = ext if re.match(r"^\.(jpe?g|png|gif|webp|bmp)$", ext, re.I) else ".jpg"
        rel = os.path.join(AV_REL.replace("/", os.sep), hashlib.md5(url.encode()).hexdigest()[:16] + ext)
    else:
        nm = safe_name(url)
        key = (tid, nm.lower())
        idx = 0
        while key in used:
            idx += 1
            nm = safe_name(url, idx)
            key = (tid, nm.lower())
        used.add(key)
        rel = os.path.join(IMG_REL.replace("/", os.sep), tid, nm)
    return rel.replace("\\", "/")


def looks_image(path):
    """魔术字节校验：防防盗链站返回 HTML/文本被当图片静默存盘。"""
    try:
        with open(path, "rb") as f:
            h = f.read(16)
    except Exception:
        return False
    if h[:3] == b"\xff\xd8\xff":          # JPEG
        return True
    if h[:8] == b"\x89PNG\r\n\x1a\n":     # PNG
        return True
    if h[:6] in (b"GIF87a", b"GIF89a"):   # GIF
        return True
    if h[:4] == b"RIFF" and h[8:12] == b"WEBP":  # WEBP
        return True
    if h[:2] == b"BM":                    # BMP
        return True
    return False


def enc_url(u):
    """对 URL 路径/查询百分号编码（修复中文文件名等非 ASCII 导致 curl 000）。已编码的不变。"""
    try:
        p = urlsplit(u)
        if all(ord(c) < 128 for c in u):
            return u
        return urlunsplit((p.scheme, p.netloc, quote(p.path), quote(p.query, safe="=&"), p.fragment))
    except Exception:
        return u


def download(url, rel):
    """rel 为相对 ROOT 的路径；幂等：存在且为合法图片则跳过；下载后校验魔术字节。"""
    full = os.path.join(ROOT, rel)
    if os.path.exists(full) and os.path.getsize(full) > 1200 and looks_image(full):
        return "skip"
    os.makedirs(os.path.dirname(full), exist_ok=True)
    u = enc_url(url)
    for attempt in range(3):
        r = subprocess.run(["curl", "-sL", "--ssl-no-revoke", "--max-time", "40",
                            "-x", PROXY, "-A", UA, "-e", "https://www.javbus.com/",
                            "-o", full, u], capture_output=True, text=True,
                           encoding="utf-8", errors="ignore")
        if r.returncode == 0 and os.path.exists(full) and os.path.getsize(full) > 1200 and looks_image(full):
            return "ok"
        # 非图片（HTML/403 文本）或太小：删掉损坏文件后重试
        if os.path.exists(full):
            try:
                os.remove(full)
            except Exception:
                pass
        time.sleep(1 + attempt)
    return "fail"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", nargs="?", default="all", choices=["all", "images"])
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--only", type=str, default="")
    a = ap.parse_args()
    only = set(x.strip() for x in a.only.split(",") if x.strip()) or None
    # 本机 assets/images/ 就位（上一轮人工复制走之后会被删掉，这里重建空目录，新图全落这里）
    if not os.path.isdir(IMG_DIR):
        os.makedirs(IMG_DIR, exist_ok=True)
        log("本机 assets/images 目录已重建 -> %s" % IMG_DIR)
    content = gather()
    if only:
        content = [(t, u) for (t, u) in content if t in only]

    jobs = []  # (url, dest_rel) 按 URL 全局去重（同图仅存一份，跳帖共用）
    used = set()
    seen_url = set()
    # 先载入已有映射：它既是累加台账的基底，也用于“同图已入库”的复用判定
    prev = {}
    if os.path.exists(MAP_PATH):
        try:
            prev = json.load(io.open(MAP_PATH, encoding="utf-8"))
        except Exception:
            prev = {}
    urlmap = dict(prev)
    reused = []
    for tid, u in content:
        if u in seen_url:
            continue
        seen_url.add(u)
        old = prev.get(u)
        if old:
            old_full = os.path.join(ROOT, old)
            # 2026-11-03 修：同一张图被第二个帖引用时，旧逻辑会把它下到新帖目录并
            # 把台账改写过去（实测 168421 的 5 张图漂到 151820/）——既重复占盘，
            # 又让“历史条目不动”的口径破掉。现在：本机在档就复用，不重下也不改台账。
            if os.path.exists(old_full) and looks_image(old_full):
                reused.append((u, old))
                continue
        jobs.append((u, target_for("content", tid, u, used)))
    log("jobs=%d (content images)；同图复用台账已有 %d" % (len(jobs), len(reused)))

    okcnt = [0, 0, 0]  # ok, skip, fail
    fails = []
    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(download, u, rel): (u, rel) for u, rel in jobs}
        done = 0
        for fu in cf.as_completed(futs):
            u, rel = futs[fu]
            st = fu.result()
            okcnt[{"ok": 0, "skip": 1, "fail": 2}[st]] += 1
            if st in ("ok", "skip"):
                urlmap[u] = rel
            else:
                fails.append(u)
            done += 1
            if done % 100 == 0:
                log("  %d/%d ok=%d skip=%d fail=%d" % (done, len(jobs), okcnt[0], okcnt[1], okcnt[2]))
    # 台账保留策略（2026-10-30 定，配合"人工整份复制到移动硬盘 + 删本机 assets/images/"的流程）：
    # imgmap.json 记录的是"这张图曾经成功落盘"，属永久台账。用户复制走之后会把本机 assets/images/ 删掉，
    # 此时历史条目对应的字节在本机不可见是**预期状态**。若照旧按"文件此刻在不在"过滤，几千条映射
    # 会被清空，build.py 于是把所有旧文章的图片退回远程 URL —— 离线库当场退化，还会随下一轮整份
    # 复制覆盖掉硬盘上的正确版本。所以只有本轮真正下过的 URL 才需要校验存在性+魔术字节。
    attempted = set(u for (u, _rel) in jobs)
    final, dropped = {}, 0
    for (u, rel) in urlmap.items():
        if u not in attempted:
            final[u] = rel                                   # 历史条目：本机看不到也保留
            continue
        full = os.path.join(ROOT, rel)
        if os.path.exists(full) and looks_image(full):
            final[u] = rel
        else:
            dropped += 1                                     # 本轮下失败：不入台账，build 保留远程 URL
    io.open(MAP_PATH, "w", encoding="utf-8").write(json.dumps(final, ensure_ascii=False, indent=1))
    log("DONE ok=%d skip=%d fail=%d 复用=%d; map=%d（历史保留 %d / 本轮落地 %d / 本轮剔除 %d）-> %s"
        % (okcnt[0], okcnt[1], okcnt[2], len(reused), len(final), len(final) - okcnt[0],
           okcnt[0], dropped, MAP_PATH))
    if fails:
        P.ensure(os.path.dirname(IMG_FAIL))
        io.open(IMG_FAIL, "w", encoding="utf-8").write("\n".join(fails))
        log("失效/下载失败 %d 条 -> %s" % (len(fails), IMG_FAIL.replace(P.ROOT + os.sep, "").replace("\\", "/")))


if __name__ == "__main__":
    main()
