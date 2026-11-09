# -*- coding: utf-8 -*-
"""瀑布流画廊新增影片: 检测 _raw_posts.json 中 data.js 尚未收录的新影片,
下载海报, 并把基础记录追加进 data.js。

前置: 先跑 `python _scripts/pipeline/01_fetch_meta.py --update` 刷新 _raw_posts.json。
后续: 新 id 会自动被 crawl_pages / download_shots / magnets / build_datajs
      等脚本识别 (它们都基于 _raw_posts.json 的 id 列表 + 文件存在性断点续传),
      跑完一遍流水线后新影片即拥有详情/截图/磁力。

用法:
  python _scripts/pipeline/02_new_movies.py --dry-run      # 只报告有多少新影片, 不写任何文件
  python _scripts/pipeline/02_new_movies.py                # 下载新海报 + 追加记录到 data.js
  python _scripts/pipeline/02_new_movies.py --limit=10     # 只处理最新 10 部新片
  python _scripts/pipeline/02_new_movies.py --backfill     # 不新增记录: 补齐已收录但本地海报文件缺失的影片
  python _scripts/pipeline/02_new_movies.py --dir=<画廊目录>

新片判定会跳过排除清单里的 id（源站教程/说明等非影片帖）：
工具链级 `_scripts/skip_ids.txt`（跟脚本走）+ 当前画廊追加的 `_pipeline/skip_ids.txt`。
"""
import json, os, re, sys, io, time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # -> _scripts/（common.py 所在）
from common import (gallery_dir, datajs_path, meta_file, posters_dir, get,
                    load_datajs, save_datajs, sleep_polite, POSTER_PREFIX,
                    has_flag, arg_int, read_skip_ids)

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

GDIR = gallery_dir()
RAW = meta_file(GDIR, "_raw_posts.json")
DATAJS = datajs_path(GDIR)
IMGDIR = posters_dir(GDIR)


def make_cover(raw):
    """裁剪+压缩成 2:3 竖版封面 (400x600, JPEG q78); PIL 不可用或失败返回 None"""
    try:
        from PIL import Image, ImageOps
        import io as _io
        im = Image.open(_io.BytesIO(raw))
        im.load()
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", im.size, (18, 18, 20))
            im2 = im.convert("RGBA")
            bg.paste(im2, mask=im2.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
        tw, th = 400, 600
        w, h = im.size
        ratio = tw / th
        if w / h > ratio:
            nw = int(h * ratio)
            left = (w - nw) // 2
            im = im.crop((left, 0, left + nw, h))
        else:
            nh = int(w / ratio)
            top = int((h - nh) * 0.42)
            im = im.crop((0, top, w, top + nh))
        im = im.resize((tw, th), Image.LANCZOS)
        buf = _io.BytesIO()
        im.save(buf, "JPEG", quality=78, optimize=True, progressive=True)
        return buf.getvalue()
    except Exception:
        return None


def record_from_item(it, have_poster):
    """_raw_posts item -> data.js 基础记录 (字段映射与 build_html 一致)"""
    return {
        "i": it["id"],
        "t": it["title"],
        "y": it["year"],
        "r": it["region"],
        "g": it["genre"],
        "s": it["size"],
        "v": it["format"],
        "b": it["subs"],
        "db": it["douban"],
        "a": it["actors"],
        "dr": it["director"],
        "du": it["duration"],
        "lb": it["lang"],
        "dt": it["date"],
        "u": it["link"],
        "p": f"{POSTER_PREFIX}/{it['id']}.jpg" if have_poster else "",
    }


def download_poster(fid, url):
    out = os.path.join(IMGDIR, f"{fid}.jpg")
    if os.path.exists(out) and os.path.getsize(out) > 1024:
        return True, "exists"
    if not url:
        return False, "no-url"
    import urllib.request
    from common import CTX, UA
    last = None
    for i in range(3):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Referer": "https://www.9dy.top/"})
            with urllib.request.urlopen(req, timeout=45, context=CTX) as r:
                rawb = r.read()
            data = make_cover(rawb) or rawb
            with open(out, "wb") as f:
                f.write(data)
            return True, "ok"
        except Exception as e:
            last = e
            time.sleep(1 + i)
    return False, str(last)


def backfill_posters(items):
    """--backfill: 已收录但本地缺海报文件的影片, 从元数据 img URL 重下

    下载成功后会回写 data.js 的 p 字段（只填空白, 不覆盖已有值）——
    否则补了图但页面依旧显示 🎬 占位。文件已在但 p 为空的记录也会被修正。
    """
    have = {f[:-4] for f in os.listdir(IMGDIR)
            if f.endswith(".jpg") and f[:-4].isdigit()}
    records = load_datajs(DATAJS)
    by_id = {r["i"]: r for r in records}
    todo, no_url, fixed_p = [], [], 0
    for r in records:
        pid = str(r["i"])
        it = items.get(r["i"])
        url = (it or {}).get("img") or ""
        if pid in have:
            if not r.get("p"):
                r["p"] = "%s/%s.jpg" % (POSTER_PREFIX, pid)
                fixed_p += 1
            continue
        (todo if url else no_url).append((pid, url))
    print("已收录: %d | 本地海报: %d | 待补: %d | 元数据无图 URL: %d | 已补写 p 字段: %d"
          % (len(records), len(have), len(todo), len(no_url), fixed_p))
    if no_url:
        print("  无 URL 的 id（先跑 _scripts/pipeline/01_fetch_meta.py --fix-img 补 URL）:",
              [p for p, _ in no_url][:20])
    if not todo:
        if fixed_p:
            save_datajs(DATAJS, records)
            print("已回写 %d 条 p 字段 -> data.js" % fixed_p)
        else:
            print("无需补图")
        return
    if has_flag("--dry-run"):
        print("(dry-run, 未下载)")
        return
    ok = fail = 0
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(download_poster, int(pid), url): pid for pid, url in todo}
        for fu in as_completed(futs):
            pid = futs[fu]
            good, why = fu.result()
            if good:
                ok += 1
                r = by_id.get(int(pid))
                if r is not None and not r.get("p"):
                    r["p"] = "%s/%s.jpg" % (POSTER_PREFIX, pid)
                    fixed_p += 1
            else:
                fail += 1
                print("  poster FAIL", pid, why, flush=True)
            if (ok + fail) % 200 == 0:
                print("  进度 %d/%d ok=%d" % (ok + fail, len(todo), ok), flush=True)
    if fixed_p:
        save_datajs(DATAJS, records)
    print("backfill DONE ok=%d fail=%d | 回写 p 字段 %d 条" % (ok, fail, fixed_p))


def main():
    dry = "--dry-run" in sys.argv[1:]
    with open(RAW, encoding="utf-8") as f:
        payload = json.load(f)
    items = {it["id"]: it for it in payload["items"]}

    if has_flag("--backfill"):
        return backfill_posters(items)

    records = load_datajs(DATAJS)
    known = {c["i"] for c in records}
    skip = read_skip_ids(GDIR)
    excluded = sorted(i for i in items if i in skip)
    new_ids = [i for i in items if i not in known and i not in skip]
    lim = arg_int("--limit=", None, "--limit")
    if lim is not None:
        new_ids = new_ids[:lim]
    print("data.js 已收录: %d | 元数据总数: %d | 新影片: %d | 排除清单命中: %d 条"
          % (len(records), len(items), len(new_ids), len(excluded)))
    if excluded:
        print("  按排除清单 skip 的 id（非影片帖, 不会被加回）:", excluded[:12])
    if not new_ids:
        print("没有新影片, 无需操作")
        return
    for i in new_ids:
        it = items[i]
        print("  NEW", i, it["title"], it["date"])
    if dry:
        print("(dry-run, 未做任何修改)")
        return

    # 1. 并发下载新海报
    ok = fail = 0
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(download_poster, i, items[i].get("img") or ""): i for i in new_ids}
        for fu in as_completed(futs):
            i = futs[fu]
            good, why = fu.result()
            if good:
                ok += 1
            else:
                fail += 1
                print("  poster FAIL", i, why, flush=True)
    print(f"posters: ok={ok} fail={fail}")

    # 2. 追加记录到 data.js (按收录日期新片在前, 与 data.js 现有排序一致)
    have = {int(f[:-4]) for f in os.listdir(IMGDIR) if f.endswith(".jpg") and f[:-4].isdigit()}
    new_recs = [record_from_item(items[i], i in have) for i in new_ids]
    all_recs = new_recs + records   # 新片排最前 (date desc)
    size = save_datajs(DATAJS, all_recs)
    print(f"data.js 追加 {len(new_recs)} 条 -> {size} bytes (新片排在最前)")

    # 3. 提示后续流水线
    print("""
后续步骤 (新 id 自动被各脚本识别):
  python _scripts/pipeline/03_crawl_pages.py        # 抓新片详情页 (旧 id 文件已存在自动跳过)
  python _scripts/pipeline/04_parse_details.py      # 重新解析全部页面 -> details.json
  python _scripts/pipeline/05_download_shots.py     # 下载新片截图 (旧文件自动跳过)
  python _scripts/pipeline/06_magnets.py            # 扫描新片磁力 (旧 id 记录已存在自动跳过)
  python _scripts/pipeline/06_magnets.py --click2   # (可选) 对 0 金币影片二次点击
  python _scripts/pipeline/07_build_datajs.py       # 合并进 data.js
""")


if __name__ == "__main__":
    main()
