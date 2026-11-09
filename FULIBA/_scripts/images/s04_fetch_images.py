# -*- coding: utf-8 -*-
"""
s04_fetch_images.py — 图片资源本地化
- 从 _pipeline/articles.json 收集全部图片 URL（去重）
- 下载到 images/<前2位>/<hash>.<ext>
- 生成 _pipeline/image_manifest.json：url -> {local, bytes, type, from}
- 回写 articles.json：images[].local，content_html 中 src 改写为本地相对路径
幂等：manifest 中已成功的直接复用；目标文件存在即跳过下载
"""
import hashlib
import os
import re
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # _scripts/
import common as C

from lxml import html as LH

# 正文内图片 src → 本地相对路径 的改写标记
IMG_SRC_ATTRS = ("src", "data-src", "data-original", "data-fl-origin")

EXT_BY_CTYPE = {
    "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
    "image/gif": ".gif", "image/webp": ".webp", "image/bmp": ".bmp",
    "image/svg+xml": ".svg", "image/avif": ".avif",
}
MAGIC = [
    (b"\xff\xd8\xff", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"GIF87a", ".gif"), (b"GIF89a", ".gif"),
    (b"BM", ".bmp"),
]


def guess_ext(content_type, data):
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct in EXT_BY_CTYPE:
        return EXT_BY_CTYPE[ct]
    for sig, ext in MAGIC:
        if data.startswith(sig):
            return ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:4] == b"<?xm" or data[:5] == b"<svg ":
        return ".svg"
    return ".bin"


def local_name(url, ext):
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]
    d = os.path.join(C.IMAGES_DIR, h[:2])
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, h + ext)


def rel_from_root(path):
    """落盘路径 → 台账图片键（必须是 `images/<xx>/<hash>.ext`，不能用通用 relpath：
    图片库 2026-11-04 已并入 assets/images/，relpath 会造出带 assets/ 前缀的第二种键，
    而 s04d 的幂等判定只认 images/ 前缀。口径单一来源：common.img_key_from_disk。"""
    return C.img_key_from_disk(path)


def download(session, url, pacer, referer=None, timeout=45, fast_fail=False):
    """下载单图，返回 (abs_path, content_type) 或 (None, None)。

    防盗链处理：sinaimg.cn 使用 Referer ACL（实测 fuliba2023.net / weibo.com /
    tva1.sinaimg.cn 均被 403 拒绝，但 Referer 为 weibo.com 时返回 200）。
    → 采用 Referer 轮换策略，逐个尝试直到成功。

    fast_fail=True：源侧已限流时（如 cravatar.cn 连续 30s 超时），
    单次尝试即放弃并快速跳过，避免大量线程集体卡在超时上拖垮整体进度。
    """
    if url.startswith("data:"):
        return None, None

    cands = []
    if referer:
        cands.append(referer)
    host = urllib.parse.urlparse(url).netloc.lower()
    if "sinaimg" in host:
        cands += ["https://weibo.com/", "https://tva1.sinaimg.cn/",
                  "https://fuliba2023.net/"]
    cands += [C.BASE + "/", "https://weibo.com/"]

    if fast_fail:
        cands = cands[:2]

    last = None
    for ref in cands:
        try:
            pacer.wait()
            r = session.get(url, headers=C.headers(ref), timeout=timeout, stream=True)
        except Exception as e:
            last = "EXC %s" % e
            if fast_fail:
                return None, None
            pacer.on_fail()
            continue
        if r.status_code == 200:
            data = r.content
            r.close()
            if data and len(data) >= 100:
                ext = guess_ext(r.headers.get("Content-Type", ""), data)
                path = local_name(url, ext)
                if not (os.path.exists(path) and os.path.getsize(path) == len(data)):
                    with open(path, "wb") as f:
                        f.write(data)
                pacer.on_ok()
                return path, r.headers.get("Content-Type", "")
            last = "SHORT %d bytes" % len(data)
            if fast_fail:
                return None, None
            pacer.on_fail()
            continue
        last = "HTTP %d" % r.status_code
        r.close()
        if r.status_code in (403, 429):
            # 防盗链/限流：换 Referer 重试，不计入熔断
            if fast_fail:
                return None, None
            time.sleep(0.6)
            continue
        if fast_fail:
            return None, None
        pacer.on_fail()
        break
    if last:
        pacer.on_fail()
    return None, None


def load_articles(keys=None):
    """读年度容器的解析产物（_pipeline/material/<年>/<key>.jsonl 的 parsed 段）。

    旧版先走 _pipeline/parsed/**/*.json、回退 _pipeline/articles.json；年度化后容器是唯一数据源，
    那个 350MB 的全量汇总已停产。

    keys：只要这些 16 位哈希对应的篇（过滤在 open() 之前，所以「范围模式」真的
    不碰另外七千个文件）；不给 = 全树，与历史行为一致。
    """
    arts = {}
    for url, _key, _slot, a in C.Y.iter_parsed(keys=keys):
        arts[url] = a
    return arts


def collect_urls(articles):
    """收集全部需本地化的图片 URL：正文图 + 评论头像，按 URL 去重。

    实测分布（4852 篇样本）：
    - 正文图 34105 张（去重），主源 tva1.sinaimg.cn 占 87%
    - 评论头像 37365 张（去重），**全部来自 cravatar.cn**（Gravatar），
      单张仅 1.8KB、0.03s/张，但数量比正文图还多。
    → 两者分开下载：头像源独立、极轻量，可高并发；正文图走 sinaimg，受代理隧道限制。
    """
    body, av = [], []
    seen_b, seen_a = set(), set()
    for a in articles.values():
        for im in a.get("images", []):
            u = im.get("url")
            if u and u not in seen_b:
                seen_b.add(u)
                body.append(u)
    for a in articles.values():
        for c in a.get("comments", []):
            u = c.get("avatar")
            if u and u not in seen_a:
                seen_a.add(u)
                av.append(u)
    return body, av



def download_batch(urls, manifest, workers, base, jitter, tag,
                   timeout=45, fast_fail=False):
    """下载一批 URL（流式提交，最多 24 个在途）。返回 (ok, fail)。"""
    if not urls:
        return 0, 0
    mpath = os.path.join(C.SRC, "image_manifest.json")
    pacer = C.Pacer(base=base, jitter=jitter, rest_every=200, rest_secs=2.0, name=tag)
    _tls = threading.local()

    def get_sess():
        s = getattr(_tls, "sess", None)
        if s is None:
            s = C.make_session(proxy=True)
            _tls.sess = s
        return s

    def do_one(u):
        try:
            return download(get_sess(), u, pacer, timeout=timeout, fast_fail=fast_fail)
        except Exception:
            return None, None

    ok = fail = 0
    t0 = time.time()
    total = len(urls)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        inflight = {}
        done_n = 0
        it = iter(urls)
        exhausted = False

        def harvest(fut):
            nonlocal done_n, ok, fail
            u = inflight.pop(fut)
            try:
                path, ctype = fut.result()
            except Exception:
                path, ctype = None, None
            if path:
                manifest[u] = {
                    "local": rel_from_root(path),
                    "bytes": os.path.getsize(path),
                    "ctype": ctype,
                    "ok": True,
                }
                ok += 1
            else:
                # 源侧限流导致的失败是「暂时性」的，不写 manifest 永久失败标记，
                # 否则下一轮 watch 就会跳过它们，永远补不上。
                # 正文图（稳定源）才记为永久失败，避免每轮重复消耗。
                if not fast_fail:
                    manifest[u] = {"local": "", "ok": False}
                fail += 1
            done_n += 1

        while True:
            while not exhausted and len(inflight) < 24:
                try:
                    u = next(it)
                except StopIteration:
                    exhausted = True
                    break
                inflight[pool.submit(do_one, u)] = u
            if not inflight:
                break
            picked = None
            for f in list(inflight.keys()):
                if f.done():
                    picked = f
                    break
            if picked is None:
                time.sleep(0.03)
                continue
            harvest(picked)
            if done_n % 200 == 0:
                el = time.time() - t0
                C.log("[s04][%s] %d/%d 成功 %d 失败 %d (%.2f 张/秒)"
                      % (tag, done_n, total, ok, fail, done_n / max(el, 0.001)))
                C.write_json(mpath, manifest)
    C.write_json(mpath, manifest)
    return ok, fail


def rewrite_articles(articles, manifest):
    """把图片本地路径写回文章数据，并把 content_html 里的 src 改写为本地相对路径。"""
    rewritten = 0
    missing = 0
    avatar_rewritten = 0
    avatar_missing = 0
    parsed_dir = os.path.join(C.SRC, "parsed")
    for a in articles.values():
        localmap = {}
        for im in a.get("images", []):
            ent = manifest.get(im.get("url"), {})
            im["local"] = ent.get("local", "")
            if im["local"]:
                localmap[im["url"]] = im["local"]
        a["image_local_map"] = localmap

        h = a.get("content_html", "")
        if h:
            try:
                el = LH.fragment_fromstring(h, create_parent="div")
            except Exception:
                el = None
            if el is not None:
                for im in el.xpath(".//img"):
                    src = im.get("src") or im.get("data-src") or ""
                    real = im.get("data-fl-origin") or src
                    if real in localmap:
                        im.set("src", localmap[real])
                        rewritten += 1
                    else:
                        missing += 1
                for k in IMG_SRC_ATTRS:
                    if k != "src" and k in el.attrib:
                        del el.attrib[k]
                a["content_html"] = LH.tostring(el, encoding="unicode")
                a["content_html"] = re.sub(r"^<div[^>]*>|</div>$", "", a["content_html"]) \
                    if a["content_html"].startswith("<div") else a["content_html"]

        # 评论头像：指向本地文件
        for c in a.get("comments", []):
            av = c.get("avatar")
            if av:
                ent = manifest.get(av, {})
                c["avatar_local"] = ent.get("local", "")
                if c["avatar_local"]:
                    avatar_rewritten += 1
                else:
                    avatar_missing += 1

        # 增量模式下回写单篇文件；全量模式回写 articles.json
        key = C.url_key(a.get("url", ""))
        if key:
            d = os.path.join(parsed_dir, key[:2])
            op = os.path.join(d, key + ".json")
            if os.path.isdir(d):
                C.write_json(op, a)
    return rewritten, missing, avatar_rewritten, avatar_missing


def main():
    watch = False
    for a in sys.argv[1:]:
        if a == "--watch":
            watch = True

    manifest = C.read_json(os.path.join(C.SRC, "image_manifest.json"), {}) or {}
    mpath = os.path.join(C.SRC, "image_manifest.json")
    total_ok = 0
    total_fail = 0
    cycle = 0

    while True:
        cycle += 1
        articles = load_articles()
        if not articles:
            C.log("[s04] 等待解析产物…")
            time.sleep(30)
            if not watch:
                return 2
            continue

        body_urls, av_urls = collect_urls(articles)
        todo_body = [u for u in body_urls if not manifest.get(u, {}).get("local")]
        todo_av = [u for u in av_urls if not manifest.get(u, {}).get("local")]
        C.log("[s04] 第 %d 轮：文章 %d 篇 | 正文图 %d（待下载 %d）| 头像 %d（待下载 %d）"
              % (cycle, len(articles), len(body_urls), len(todo_body),
                 len(av_urls), len(todo_av)))

        ok_a = fail_a = ok_b = fail_b = 0
        # 正文图优先：sinaimg 等主源稳定，是内容主体，必须 100% 落地。
        if todo_body:
            ok_b, fail_b = download_batch(todo_body, manifest, 6, 0.15, 0.15, "body")
            total_ok += ok_b
            total_fail += fail_b
            C.log("[s04] 正文图完成：成功 %d，失败 %d" % (ok_b, fail_b))

        # 头像（cravatar.cn）后置：该源在抓取中途开始 30s 超时，
        # 实测为「源侧限流」而非本机问题；放在正文图之后，
        # 且给它更短的超时 + 失败快速跳过，避免拖住整体进度。
        if todo_av:
            ok_a, fail_a = download_batch(todo_av, manifest, 8, 0.05, 0.05, "avatar",
                                          timeout=12, fast_fail=True)
            total_ok += ok_a
            total_fail += fail_a
            C.log("[s04] 头像完成：成功 %d，失败 %d" % (ok_a, fail_a))

        ok = ok_a + ok_b
        fail = fail_a + fail_b

        # 回写
        articles = load_articles()
        rewritten, missing, av_rw, av_miss = rewrite_articles(articles, manifest)
        C.write_json(mpath, manifest)
        C.log("[s04] 引用改写：正文图本地化 %d 处 / 未命中 %d 处；头像本地化 %d 处 / 未命中 %d 处"
              % (rewritten, missing, av_rw, av_miss))
        C.state_append("images", "__summary__", "DONE",
                       total=len(body_urls) + len(av_urls), ok=ok, fail=fail,
                       rewritten=rewritten, missing=missing)

        if not watch:
            break
        C.log("[s04] watch 模式：20s 后检查新图片")
        time.sleep(20)

    C.log("[s04] 全部完成：累计成功 %d，失败 %d" % (total_ok, total_fail))
    return 0


if __name__ == "__main__":
    sys.exit(main())
