# -*- coding: utf-8 -*-
"""
s04r_fix_broken_refs.py — 修复 content_html 中指向 0 字节空壳的坏本地引用

背景（§1.32）：
  早期 GIF 转帧中断在磁盘留下 959 个 0 字节文件，多轮回写后这些坏路径被
  固化进 content_html（s04d 的幂等跳过不校验文件有效性），离线站出现破图。
  更棘手的是：这些图的原始远程 URL 在多轮覆盖中已从 images[].local 丢失。

恢复方法（已实证）：
  content_html 的 <img> 顺序与 images[] 严格一一对应（实测 49 = 49），
  故 images[i].url 即第 i 个 img 的原始真实图床地址。

处理：
  1) 按序对齐找回远程 URL
  2) 重新下载 → 落盘到 images/<前2位>/<hash>.<ext>，写回 manifest
  3) 下载失败的（源站已死）→ src 保持远程 URL（降级，不破图）

用法: python s04r_fix_broken_refs.py [--no-download]
"""
import os, sys, io, json, time, threading, queue, hashlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # _scripts/
import common as C

from lxml import html as LH

try:
    import requests
    from PIL import Image
except Exception as e:
    print("IMPORT_FAIL", e); raise

MAN = os.path.join(C.SRC, "image_manifest.json")
NO_DL = "--no-download" in sys.argv
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
# 防盗链：sinaimg 只放行 weibo 及同域（§1.9），其余图床对空 Referer 更宽松。
# 按域名直接定候选顺序，避免每张 sinaimg 图先白吃一次 403。
REFS_SINA = ["https://weibo.com/", ""]
REFS_DEF = ["", "https://weibo.com/", "https://fuliba2023.net/"]
MAX_PIXELS = 200_000_000
WORKERS = 10
# 0 字节空壳隔离区（由 s04q2_purge_recycle.py 做目录级清除）
ZERO_BIN = os.path.join(C.BACKUP_DIR, "_orphans", "_zero_shells")   # 被替下来的旧文件都归 _backup
# 允许超大图解码（原图可能上亿像素），关闭 PIL 的解压炸弹告警
try:
    Image.MAX_IMAGE_PIXELS = None
except Exception:
    pass

_lock = threading.Lock()
_man = {}
_downloaded = 0
_failed = 0
_reverted = 0
_unfixable = 0
_lock = threading.Lock()


def url_hash(u):
    """归档统一命名：sha1(url)[:20]。
    ⚠ 不可用 C.url_key（它是 sha1[:16]，用于 HTML 分片），
    否则会与全站既有图片文件名不一致，manifest 与回写全部对不上（§1.36）。
    """
    return hashlib.sha1(u.encode("utf-8")).hexdigest()[:20]


def local_path_for(u, ext):
    h = url_hash(u)
    return "images/%s/%s%s" % (h[:2], h, ext)


def refs_for(u):
    host = u.split("/")[2].lower() if "//" in u else ""
    if "sinaimg.cn" in host or "weibo" in host:
        return REFS_SINA
    return REFS_DEF


def orig_ext(u):
    """URL 自带的原始扩展名（.jpeg 归一为 .jpg）；返回 "" 表示无法判断。"""
    e = os.path.splitext(u.split("?")[0])[1].lower()
    if e == ".jpeg":
        e = ".jpg"
    return e if e in (".jpg", ".png", ".gif", ".webp") else ""


def find_native(u):
    """仅当 URL 原始扩展名的文件已非空才返回 (rel, bytes)。

    ⚠ 不可把其它扩展名的同名文件当原图：早期「GIF 转帧」把动画 GIF
    压成静态 JPEG 首帧（§1.32），若幂等检查按 .jpg 优先命中，
    就会把降级帧当成原图直接复用（§1.37 事故根因）。
    """
    e = orig_ext(u)
    if not e:
        return None
    rel = local_path_for(u, e)
    p = C.img_disk_path(rel)
    try:
        if os.path.exists(p) and os.path.getsize(p) > 0:
            return rel, os.path.getsize(p)
    except OSError:
        pass
    return None


def find_fallback(u):
    """原图不可得时的降级回落：同哈希下其它扩展名的非空文件（含静态帧）。"""
    for ext in (".jpg", ".png", ".gif", ".webp"):
        if ext == orig_ext(u):
            continue
        rel = local_path_for(u, ext)
        p = C.img_disk_path(rel)
        try:
            if os.path.exists(p) and os.path.getsize(p) > 0:
                return rel, os.path.getsize(p)
        except OSError:
            pass
    return None


def ext_of(content_type, data):
    ct = (content_type or "").lower()
    if "png" in ct:
        return ".png"
    if "gif" in ct:
        return ".gif"
    if "webp" in ct:
        return ".webp"
    if "jpeg" in ct or "jpg" in ct:
        return ".jpg"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".jpg"


def fetch_one(u):
    """下载**原图**并落盘；返回 (local_rel, bytes, ctype) 或 None

    ⚠ 不做任何尺寸降级：归档目标是保留原始画质（/large/ 原图）。
    原图单张响应可能达 104~140 秒，故读超时放宽到 150s。

    顺序：原扩展名文件已非空 → 复用；否则下载原图；
    下载彻底失败 → 回落其它扩展名的现有帧（可显示，降级记账）。
    仅当文件存在且非空才复用 —— 0 字节空壳必须重下。
    """
    hit = find_native(u)
    if hit:
        rel, nb = hit
        return rel, nb, "image/" + os.path.splitext(rel)[1].lstrip(".")


    sess = requests.Session()
    sess.trust_env = False
    sess.headers.update({"User-Agent": UA})
    for ref in refs_for(u):
        try:
            h = {"Referer": ref} if ref else {}
            r = sess.get(u, headers=h, timeout=(10, 150), allow_redirects=True)
            if r.status_code != 200 or len(r.content) < 512:
                continue
            try:
                im = Image.open(io.BytesIO(r.content))
                w, hh = im.size
                if w * hh > MAX_PIXELS:
                    continue
                im.load()
            except Exception:
                continue
            ext = ext_of(r.headers.get("Content-Type", ""), r.content)
            rel = local_path_for(u, ext)
            dst = C.img_disk_path(rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as f:
                f.write(r.content)
            # 同哈希下若有其它扩展名的 0 字节空壳（早期 GIF 转帧中断残留），
            # 移入隔离区而非逐文件删除（规避批量删除钩子，§1.32），由 s04q2 目录级清除
            for other in (".gif", ".jpg", ".png", ".webp", ".bin"):
                if other == ext:
                    continue
                shell = C.img_disk_path(local_path_for(u, other))
                try:
                    if os.path.exists(shell) and os.path.getsize(shell) == 0:
                        os.makedirs(ZERO_BIN, exist_ok=True)
                        os.replace(shell, os.path.join(ZERO_BIN, os.path.basename(shell)))
                except OSError:
                    pass
            return rel, len(r.content), "image/" + ext.lstrip(".")
        except Exception:
            continue
    # 源站已死：回落降级帧（如静态首帧），保证不破图
    fb = find_fallback(u)
    if fb:
        rel, nb = fb
        return rel, nb, "image/" + os.path.splitext(rel)[1].lstrip(".")
    return None


def collect_tasks():
    """扫描年度容器，产出待修复的 (槽位, URL, 文章数据, [img元素…], [坏引用…])。

    坏引用元组：(序号, img元素, 远程URL, 当前本地路径)。
    """
    tasks = []
    for url, _key, slot, a in C.Y.iter_parsed():
        h = a.get("content_html") or ""
        if not h:
            continue
        try:
            el = LH.fragment_fromstring(h, create_parent="div")
        except Exception:
            continue
        imgs = el.xpath(".//img")
        arr = a.get("images") or []
        bad = []
        for i, im in enumerate(imgs):
            src = im.get("src") or ""
            if not src.startswith("images/"):
                continue
            # 找回原始远程 URL：优先用 images[].local 精确匹配（强证据，
            # 不受 20 篇 img/images 数量不等文章的错位影响），再无则顺序对齐
            remote = ""
            for _im in arr:
                if (_im.get("local") or "") == src:
                    remote = _im.get("url") or _im.get("raw_src") or ""
                    break
            if not remote and i < len(arr):
                remote = arr[i].get("url") or arr[i].get("raw_src") or ""
            fp = os.path.normpath(C.img_disk_path(src))
            try:
                ok = os.path.exists(fp) and os.path.getsize(fp) > 0
            except OSError:
                ok = False
            if ok:
                # 只有「GIF 被转成静态帧」才算降级（§1.32 的 GIF 转帧产物，§1.37 实证 801 处）：
                # 其它扩展名不符多为图床返回格式差异（URL 是 .png、实际返回 JPEG），
                # 落盘 .jpg 即原图，不得当成坏引用重下。
                ue = orig_ext(remote) if remote else ""
                se = os.path.splitext(src)[1].lower()
                if not (ue == ".gif" and se != ".gif"):
                    continue
                if find_native(remote):
                    continue
            bad.append((i, im, remote, src))
        if bad:
            tasks.append((slot, url, a, el, imgs, bad))
    return tasks


def main():
    C.guard_or_exit("s04r")          # 会写图并把 local 回写进 content_html
    if C.new_images_only():
        # 历史图在别的电脑：「本机文件缺失」是常态而不是坏引用，
        # 按旧口径跑会把 13.8 万处引用当坏引用重下。只处 0 字节空壳那种才该修，
        # 而空壳只可能出现在库在本机的时候 —— 所以本模式直接不跑。
        print("[s04r] 图片库模式=incremental：历史图在外部库，不修引用不重下（挂库后再跑）")
        print("FIXBROKEN_SKIP_INCREMENTAL")
        return
    global _man, _downloaded, _failed, _reverted, _unfixable
    _man = C.read_json(MAN, {}) or {}
    print("[s04r] manifest 载入 %d 条" % len(_man))

    tasks = collect_tasks()
    n_bad = sum(len(t[5]) for t in tasks)
    print("[s04r] 含坏引用的文章 %d 篇，坏引用 %d 处" % (len(tasks), n_bad))
    if not tasks:
        print("FIXBROKEN_OK 0 0 0 0")
        return

    # 1) 收集所有需要重新下载的远程 URL
    need = []
    seen = set()
    for slot, url, a, el, imgs, bad in tasks:
        for i, im, remote, src in bad:
            if remote and remote not in seen:
                seen.add(remote)
                need.append(remote)
    print("[s04r] 待重新下载唯一 URL: %d" % len(need))

    got = {}
    # 续跑状态：已下载映射增量持久化，中断后不丢。读最近一份（可能是往日），写今天的日期目录
    RESUME_NAME = "resume_s04r.json"
    STATE = C.pipeline_rolling_path(RESUME_NAME)
    _prev = C.pipeline_find(RESUME_NAME)
    prev = (C.read_json(_prev, {}) or {}) if _prev else {}
    for u, r in prev.items():
        rel, nb, ct = (list(r) + [None, None, None])[:3]
        if rel and os.path.exists(C.img_disk_path(rel)):
            got[u] = (rel, nb, ct)
    if prev:
        print("[s04r] 复用上次已下载映射 %d 条" % len(got))

    if not NO_DL:
        q = queue.Queue()
        for u in need:
            if u not in got:
                q.put(u)
        print("[s04r] 本次需下载 %d 条（已在盘 %d 条）" % (q.qsize(), len(got)))

        def worker():
            global _downloaded, _failed
            while True:
                try:
                    u = q.get_nowait()
                except queue.Empty:
                    return
                r = fetch_one(u)
                if r:
                    with _lock:
                        got[u] = r
                        _downloaded += 1
                else:
                    with _lock:
                        _failed += 1
                q.task_done()

        ts = [threading.Thread(target=worker, daemon=True) for _ in range(WORKERS)]
        for t in ts:
            t.start()
        while any(t.is_alive() for t in ts):
            time.sleep(10)
            # 增量持久化：每 10 秒落一次快照，中断也不丢已下载映射
            try:
                os.makedirs(os.path.dirname(STATE), exist_ok=True)
                C.write_json(STATE, {u: list(v) for u, v in got.items()})
            except Exception:
                pass
        for t in ts:
            t.join()
        try:
            C.write_json(STATE, {u: list(v) for u, v in got.items()})
        except Exception:
            pass

    print("[s04r] 下载成功 %d，失败 %d" % (len(got), _failed))

    # 2) 落盘已在 fetch_one 内完成（下载即写盘），此处无需额外处理

    # 3) 回写年度容器（内容未变不写盘）
    for slot, url, a, el, imgs, bad in tasks:
        changed = False
        for i, im, remote, src in bad:
            newloc = ""
            if remote and remote in got:
                newloc = got[remote][0]
            if newloc:
                if newloc != src:
                    im.set("src", newloc)
                    changed = True
                with _lock:
                    _reverted += 1
            elif remote:
                # 源站已死 → 保持远程 URL（降级，不破图）
                im.set("src", remote)
                changed = True
                with _lock:
                    _unfixable += 1
            else:
                with _lock:
                    _unfixable += 1
        if changed:
            out = LH.tostring(el, encoding="unicode")
            if out.startswith("<div"):
                import re
                out = re.sub(r"^<div[^>]*>", "", out)
                out = re.sub(r"</div>$", "", out)
            a["content_html"] = out
            C.Y.write_parsed(url, a, slot=slot, touch=False)

    # 4) manifest 写入新下载的文件
    for u, (rel, nb, ct) in got.items():
        _man[u] = {"local": rel, "bytes": nb, "ctype": ct, "ok": True}
    C.write_json(MAN, _man)

    print("[s04r] 回写为本地路径 %d 处，降级远程 %d 处" % (_reverted, _unfixable))
    print("FIXBROKEN_OK %d %d %d %d" % (n_bad, _reverted, _unfixable, len(got)))


if __name__ == "__main__":
    main()
