# -*- coding: utf-8 -*-
"""抓取 9dy.top 作者 admin 的全部文章元数据 (WordPress REST API)

用法:
  python _scripts/pipeline/01_fetch_meta.py                     # 全量抓取 (per_page=100 逐页, 最多 40 页)
  python _scripts/pipeline/01_fetch_meta.py --update            # 增量: 只抓新帖, 与已有 _raw_posts.json 合并
  python _scripts/pipeline/01_fetch_meta.py --update --max-pages=2   # 增量时最多翻 2 页就停
  python _scripts/pipeline/01_fetch_meta.py --fix-img          # 不翻页: 只对 img 为空的条目逐篇补图 URL（后接 new_movies --backfill）
  python _scripts/pipeline/01_fetch_meta.py --retitle          # 不联网: 用当前 parse_title 规则从 full_title 离线重算片名/年份
  python _scripts/pipeline/01_fetch_meta.py --dir=<画廊目录>

输出: <dir>/_rawdata/_raw_posts.json  {total, categories, items:[...]}
"""
import json, os, re, sys, io, time, html as ihtml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # -> _scripts/（common.py 所在）
from common import (gallery_dir, meta_file, backup_file, get, BASE_URL, sleep_polite,
                    arg_val, arg_int, has_flag, atomic_write_json)

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

GDIR = gallery_dir()
OUT = meta_file(GDIR, "_raw_posts.json")

TAG_RE = re.compile(r"<[^>]+>")
WS = re.compile(r"[\u200b\u200c\u200d\ufeff\xa0]")


def clean(s):
    if not s:
        return ""
    s = ihtml.unescape(TAG_RE.sub("", s))
    s = WS.sub(" ", s)
    return re.sub(r"[ \t]+", " ", s).strip()


def pick_field(text, labels):
    for lab in labels:
        m = re.search(lab + r"\s*[:：]\s*(.+?)(?=\n|$)", text)
        if m:
            v = m.group(1).strip(" /\u3000")
            v = re.sub(r"\s*/\s*", " / ", v).strip()
            if v and v not in ("-", "—", "未知"):
                return v
    return ""


BRACKET = re.compile(r"[\[(（【]\s*([^\[\]()（）【】]*?)\s*[\])）】]\s*$")


def parse_title(full):
    """解析 片名[年份/地区/类型][体积/格式/字幕] —— 循环剥离尾部方括号块"""
    out = {"name": full, "year": "", "region": "", "genre": "", "extra": ""}
    t = re.sub(r"\s+", " ", full).strip()
    blocks = []
    while True:
        m = BRACKET.search(t)
        if not m or not m.group(1).strip():
            break
        blocks.insert(0, m.group(1).strip())
        t = t[:m.start()].strip()
        if len(blocks) >= 3:
            break
    for blk in blocks:
        parts = [x.strip() for x in blk.split("/") if x.strip()]
        if re.match(r"^\d{4}$", parts[0] if parts else ""):
            out["year"] = parts[0]
            rest = parts[1:]
            if rest:
                out["region"] = rest[0]
                out["genre"] = " / ".join(rest[1:]) if len(rest) > 1 else ""
        elif parts and len(parts) > 1 and parts[0] in ("年份不详", "年份未知", "不详", "未知"):
            # 无年份但带地区/类型的块, 如 [年份不详/韩国/情色] —— 不能当 extra 丢掉
            rest = parts[1:]
            out["region"] = rest[0]
            out["genre"] = " / ".join(rest[1:]) if len(rest) > 1 else ""
        elif re.match(r"^[\d.]+\s*[GMK]B?$", parts[0] if parts else "", re.I):
            out["extra"] = blk
        elif not out["extra"]:
            out["extra"] = blk
    t2 = re.sub(r"[(（\[【]\s*(?:\d{4}|年份不详)\s*[)）\]】].*$", "", t).strip()
    if t2:
        t = t2
    if "[" in t or "（" in t:
        m3 = re.search(r"[(（\[【]\s*(\d{4}|年份不详)\s*[/／]", t)
        if m3:
            out["year"] = "" if m3.group(1) == "年份不详" else m3.group(1)
            rest = [x.strip(" ）)】]") for x in t[m3.end():].split("/") if x.strip(" ）)】]")]
            if rest:
                out["region"] = rest[0]
                out["genre"] = " / ".join(rest[1:]) if len(rest) > 1 else ""
            t = t[:m3.start()].strip()
    if not out["year"]:
        m2 = re.search(r"[(（\[【]\s*(\d{4})\s*[)）\]】]", full)
        if m2:
            out["year"] = m2.group(1)
    out["name"] = re.sub(r"\s+", " ", t).strip(" -·、,，。")
    if not out["name"]:
        out["name"] = full
    return out


def norm_size(s):
    if not s:
        return ""
    m = re.match(r"^([\d.]+)\s*([GMK]B?)", s, re.I)
    return f"{m.group(1)}{m.group(2).upper()}" if m else ""


def norm_format(s):
    if not s:
        return ""
    m = re.search(r"(MKV|MP4|TS|WMV|AVI|ISO|BD|BluRay|HD|FLV|RMVB)", s, re.I)
    return m.group(1).upper() if m else ""


def norm_subs(s):
    if not s:
        return ""
    for k in ["中字", "国语", "粤语", "英语", "日语", "韩语", "双语", "简繁", "简中", "繁中", "外挂字幕"]:
        if k in s:
            return k
    return ""


def item_from_post(p):
    """REST post dict -> 画廊元数据 item"""
    emb = p.get("_embedded", {})
    fm = None
    wpm = emb.get("wp:featuredmedia")
    if wpm and isinstance(wpm, list) and wpm:
        fm = wpm[0].get("source_url") or ""
        if not fm:
            sizes = (wpm[0].get("media_details") or {}).get("sizes") or {}
            fm = next((v.get("source_url") for v in sizes.values() if v.get("source_url")), "")
    full_title = clean(p["title"]["rendered"])
    tp = parse_title(full_title)
    body = clean(p["content"]["rendered"])
    ex = clean(p.get("excerpt", {}).get("rendered", ""))
    item = {
        "id": p["id"],
        "title": tp["name"] or full_title,
        "full_title": full_title,
        "year": tp["year"] or pick_field(body, ["上映日期"])[:4],
        "region": tp["region"] or pick_field(body, ["制片国家/地区", "制片国家", "国家/地区"]),
        "genre": tp["genre"] or pick_field(body, ["类型"]),
        "size": norm_size(pick_field(body, ["文件大小", "大小"]) or tp.get("extra", "")),
        "format": norm_format(pick_field(body, ["格式"]) or tp.get("extra", "")),
        "subs": norm_subs(body),
        "director": pick_field(body, ["导演"]),
        "actors": pick_field(body, ["主演", "演员"]),
        "duration": pick_field(body, ["片长"]),
        "imdb": pick_field(body, ["IMDb", "imdb"]),
        "douban": "",
        "lang": pick_field(body, ["语言"]),
        "date": (p.get("date") or "")[:10],
        "link": p["link"],
        "img": fm,
        "imgid": p.get("featured_media") or 0,
        "excerpt": ex,
        "cats": [c["name"] for c in
                 (t for grp in (emb.get("wp:term") or []) if isinstance(grp, list)
                  for t in grp if isinstance(t, dict) and t.get("taxonomy") == "category")
                 if c.get("id")],
    }
    m = re.search(r"豆瓣评分\s*([\d.]+)", body)
    if m:
        item["douban"] = m.group(1)
    return item


def fetch_pages(max_pages=40, stop_on_known=None):
    """逐页抓 REST 帖子; stop_on_known=已知id集合时遇到整页已知即停 (增量)"""
    posts, page, seen = [], 1, set()
    complete = True          # 分页是否跑完（中途网络失败会置 False）
    while True:
        url = (f"{BASE_URL}/wp-json/wp/v2/posts?author=1&per_page=100&page={page}"
               f"&_embed=1&orderby=date&order=desc")
        txt, err = get(url, tries=5, timeout=60)
        if err or not txt.strip() or txt.strip() == "[]":
            if err:
                print("STOP at page", page, "err:", err)
            complete = False
            break
        batch = json.loads(txt)
        if not batch:
            break
        if not isinstance(batch, list):          # REST 出错时返回对象, 避免下面 p["id"] 报 TypeError
            print("STOP at page", page, "unexpected response:", str(batch)[:80])
            complete = False
            break
        known_cnt = 0
        for p in batch:
            if p["id"] in seen:
                continue
            seen.add(p["id"])
            posts.append(p)
            if stop_on_known is not None and p["id"] in stop_on_known:
                known_cnt += 1
        print(f"page {page} -> +{len(batch)} (total {len(posts)}, known {known_cnt})", flush=True)
        # 增量: 整页都是已知 id 说明后面没有新帖
        if stop_on_known is not None and known_cnt == len(batch):
            break
        page += 1
        if page > max_pages:
            break
        time.sleep(0.25)
    return posts, complete


def load_payload():
    if not os.path.exists(OUT):
        raise SystemExit("缺少 %s — 先跑一次全量/增量抓取" % OUT)
    with open(OUT, encoding="utf-8") as f:
        return json.load(f)


def save_payload(payload):
    atomic_write_json(OUT, payload)


def retitle_offline():
    """--retitle: 不联网, 用当前 parse_title 规则从 full_title 重算 title/year（补充空 region/genre）"""
    payload = load_payload()
    fixed = 0
    for it in payload["items"]:
        tp = parse_title(it.get("full_title") or it.get("title") or "")
        name = tp["name"] or it.get("title")
        if name != it.get("title"):
            it["title"] = name
            fixed += 1
        if tp["year"] and tp["year"] != it.get("year"):
            it["year"] = tp["year"]
            fixed += 1
        if tp["region"] and not it.get("region"):
            it["region"] = tp["region"]
        if tp["genre"] and not it.get("genre"):
            it["genre"] = tp["genre"]
    save_payload(payload)
    bad = [i["title"] for i in payload["items"]
           if "[" in i["title"] or "（" in i["title"]]
    print("retitle 改写字段次数:", fixed, "| 条目:", len(payload["items"]))
    print("仍含括号的片名:", len(bad), bad[:6])
    print("下一步: 片名变更不会自动同步到 data.js（存量记录），需要时手工校对或用 _scripts/pipeline/07_build_datajs.py")


def img_from_post(pid):
    """单篇 REST 取图: 正文首张图 > featured media 源地址 -> (url, from)"""
    txt, err = get("%s/wp-json/wp/v2/posts/%d?_embed=1" % (BASE_URL, pid),
                   tries=5, timeout=60)
    if err:
        return "", "err: " + str(err)[:60]
    try:
        p = json.loads(txt)
    except Exception as e:
        return "", "bad json: %s" % e
    c = p.get("content", {}).get("rendered", "")
    srcs = re.findall(r'<img[^>]+src="([^"]+)"', c) or re.findall(r'data-src="([^"]+)"', c)
    srcs = [s for s in srcs if re.search(r"\.(jpe?g|png|webp)$", s, re.I)]
    if srcs:
        return srcs[0], "content"
    wpm = (p.get("_embedded") or {}).get("wp:featuredmedia")
    if wpm and isinstance(wpm, list) and wpm:
        url = wpm[0].get("source_url") or ""
        if not url:
            sizes = (wpm[0].get("media_details") or {}).get("sizes") or {}
            url = next((v.get("source_url") for v in sizes.values() if v.get("source_url")), "")
        if url:
            return url, "featured"
    return "", "源站无图"


def fix_img_offline():
    """--fix-img: 不重新翻页, 只对 _raw_posts.json 中 img 为空的条目逐篇补图 URL"""
    payload = load_payload()
    miss = [i for i in payload["items"] if not i.get("img")]
    print("待补图条目:", len(miss), flush=True)
    if not miss:
        return
    fixed = 0
    for n, it in enumerate(miss, 1):
        url, src = img_from_post(it["id"])
        if url:
            it["img"] = url
            it["img_from"] = src
            fixed += 1
            print("  FIX", it["id"], src, url[:60], flush=True)
        else:
            print("  --", it["id"], it.get("title"), src, flush=True)
        save_payload(payload)      # 边跑边存, 中途中断不丢进度
        sleep_polite(0.4)
    still = [i["id"] for i in payload["items"] if not i.get("img")]
    print("fixed:", fixed, "| 仍无图:", len(still), still[:10])
    print("下一步: python _scripts/pipeline/02_new_movies.py --backfill 下载海报")


def main():
    if has_flag("--retitle"):
        return retitle_offline()
    if has_flag("--fix-img"):
        return fix_img_offline()
    update = "--update" in sys.argv[1:]
    max_pages = arg_int("--max-pages=", 40, "--max-pages")
    known_ids = set()
    payload = None
    if update and os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            payload = json.load(f)
        known_ids = {it["id"] for it in payload["items"]}
        print("existing items:", len(known_ids))
    elif update:
        print("--update 但文件不存在, 转为全量抓取")
        update = False

    stop = known_ids if update else None
    posts, complete = fetch_pages(max_pages=max_pages, stop_on_known=stop)
    print("fetched posts:", len(posts), "| 分页跑完:" , complete)

    if update:
        new_posts = [p for p in posts if p["id"] not in known_ids]
        print("NEW posts:", len(new_posts))
        if not new_posts:
            print("没有新影片, _raw_posts.json 未改动")
            return
        items = [item_from_post(p) for p in new_posts] + payload["items"]
        # 分类树合并
        cats = {c["id"]: c for c in payload.get("categories", [])}
        for p in new_posts:
            for grp in p.get("_embedded", {}).get("wp:term", []) or []:
                if not isinstance(grp, list):
                    continue
                for t in grp:
                    if isinstance(t, dict) and t.get("taxonomy") == "category":
                        cats[t["id"]] = {"id": t["id"], "name": t["name"], "slug": t["slug"]}
        payload["items"] = items
        payload["total"] = len(items)
        payload["categories"] = sorted(cats.values(), key=lambda x: x["id"])
    else:
        items = [item_from_post(p) for p in posts]
        cats = {}
        for p in posts:
            for grp in p.get("_embedded", {}).get("wp:term", []) or []:
                if not isinstance(grp, list):
                    continue
                for t in grp:
                    if isinstance(t, dict) and t.get("taxonomy") == "category":
                        cats[t["id"]] = {"id": t["id"], "name": t["name"], "slug": t["slug"]}
        payload = {"total": len(items),
                   "categories": sorted(cats.values(), key=lambda x: x["id"]),
                   "items": items}

    # 补图: 无 featured_media 的帖子从正文取第一张图 (仅全量模式大批出现, 增量也可能有)
    missing = [i for i in payload["items"] if not i.get("img")]
    if missing and len(missing) <= 80:
        print("fetching images from content for", len(missing), "posts ...")
        for n, it in enumerate(missing, 1):
            txt, err = get(f"{BASE_URL}/wp-json/wp/v2/posts/{it['id']}", tries=3)
            if not err:
                try:
                    c = json.loads(txt).get("content", {}).get("rendered", "")
                    srcs = re.findall(r'<img[^>]+src="([^"]+)"', c) or re.findall(r'data-src="([^"]+)"', c)
                    srcs = [s for s in srcs if re.search(r"\.(jpe?g|png|webp)$", s, re.I)]
                    if srcs:
                        it["img"] = srcs[0]
                        it["img_from"] = "content"
                except Exception:
                    pass
            if n % 10 == 0:
                print(f"  {n}/{len(missing)}", flush=True)
            sleep_polite(0.3)

    # 防缩水守卫: 新结果明显少于现有条数（90%）说明分页中断/源站异常，
    # 直接全量覆盖会把主源数据越抓越少 → 拒写（需 --force，且覆盖前先备份）
    if os.path.exists(OUT):
        try:
            with open(OUT, encoding="utf-8") as f:
                prev = (json.load(f) or {}).get("items", [])
        except Exception:
            prev = []
        if prev and len(payload["items"]) < len(prev) * 0.9:
            msg = ("新抓 %d 条 < 现有 %d 条的 90%%（分页是否跑完: %s）"
                   "— 拒绝覆盖 %s；确认要覆盖请加 --force" %
                   (len(payload["items"]), len(prev), complete, OUT))
            if not has_flag("--force"):
                raise SystemExit(msg)
            import shutil
            bak = backup_file(GDIR, "_raw_posts.json.force-bak")   # 当日备份目录内
            shutil.copy2(OUT, bak)
            print("WARN: %s\n  旧数据已备份 -> %s" % (msg, bak), flush=True)

    atomic_write_json(OUT, payload)
    noimg = sum(1 for i in payload["items"] if not i.get("img"))
    print("saved ->", OUT)
    print("items:", payload["total"], "| no image:", noimg,
          "| with year:", sum(1 for i in payload["items"] if i.get("year")))


if __name__ == "__main__":
    main()
