# -*- coding: utf-8 -*-
"""解析 <dir>/_rawdata/pages/*.html -> <dir>/_rawdata/details.json

每部影片提取:
  rating    豆瓣评分
  info      字典: director/writer/actors/genre/region/lang/date/duration/aka/imdb
  synopsis  简介正文
  shots     截图原图 URL 列表 (data-fancybox 灯箱图)

用法: python _scripts/pipeline/04_parse_details.py [--dir=<画廊目录>]   # 全量重解析, 幂等
"""
import json, os, re, sys, glob

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # -> _scripts/（common.py 所在）
from common import gallery_dir, pages_dir, meta_file, atomic_write_json

GDIR = gallery_dir()
PAGES = pages_dir(GDIR)
OUT = meta_file(GDIR, "details.json")

LABELS = ["导演", "编剧", "主演", "类型", "制片国家/地区", "语言", "上映日期", "片长", "又名", "IMDb"]
KEYMAP = {"导演": "director", "编剧": "writer", "主演": "actors", "类型": "genre",
          "制片国家/地区": "region", "语言": "lang", "上映日期": "date",
          "片长": "duration", "又名": "aka", "IMDb": "imdb"}


def clean(s):
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("&nbsp;", " ").replace("&#8230;", "…").replace("&amp;", "&")
    s = re.sub(r"\s+", " ", s).strip(" :：/ ")
    return s


# 下一个标签的边界。注意正文里的“制片国家 / 地区”常带空格，写死成“制片国家/地区”会切不开，
# 导致该标签后面的正文被整段并进上一字段（历史上污染过 10 条记录的 59 个字段）。
NEXT_LABEL = re.compile(
    r"(?:导演|编剧|主演|演员|类型|制片国家\s*/\s*地区|制片国家|语言|上映日期|片长|又名|IMDb|地区|年份|片名|译名|简介|官方网站)\s*[:：]"
    r"|豆瓣评分\s*[:：]?")      # “豆瓣评分 5.0”常常没有冒号


def clip_label(val):
    """字段值截到下一个标签为止，并清理豆瓣的“更多…/官方网站”尾巴与首尾分隔符"""
    m = NEXT_LABEL.search(val)
    if m:
        val = val[:m.start()]
    val = re.sub(r"\s*/?\s*更多\s*[…\.]*.*$", "", val)     # 含中间出现的“更多… 官方网站”
    return val.strip(" /,，、:：")


def parse_info_div(html):
    """格式 A: 豆瓣样式 <div id="info"> 块"""
    m = re.search(r'<div id="info">([\s\S]*?)</div>', html)
    if not m:
        return {}, ""
    block = m.group(1)
    info = {}
    parts = re.split(r'<span class="pl">', block)[1:]
    for p in parts:
        lm = re.match(r'([^<]+)</span>\s*:?\s*(.*?)(?=<span class="pl">|$)', p, re.S)
        if not lm:
            continue
        label = clean(lm.group(1))
        val = clip_label(clean(lm.group(2)))
        if label in KEYMAP:
            info[KEYMAP[label]] = val
    return info, block


def parse_text_info(txt):
    """格式 B: 纯文本 '导演 : xx 编剧 : xx ...'"""
    info = {}
    pat = r"(导演|编剧|主演|类型|制片国家\s*/\s*地区|语言|上映日期|片长|又名|IMDb)\s*[:：]\s*"
    parts = re.split(pat, txt)
    for i in range(1, len(parts) - 1, 2):
        label, val = parts[i], parts[i + 1].strip()
        val = clip_label(val)
        if label in KEYMAP and val:
            info[KEYMAP[label]] = val.strip(" /,，")
    return info


def extract_shots(html):
    """截图: 带 data-fancybox 的灯箱原图"""
    shots = []
    for m in re.finditer(r"<(a|img)\b[^>]+>", html):
        tag = m.group(0)
        if 'data-fancybox="gallery"' not in tag and 'data-fancybox="images"' not in tag:
            continue
        um = re.search(r'(?:href|data-src|src)="(https?://[^"]+/(?:wp-content/uploads/)[^"]+\.(?:jpg|jpeg|png|webp))"', tag)
        if um and um.group(1) not in shots:
            shots.append(um.group(1))
    return shots


def parse_page(html):
    d = {}
    rm = re.search(r'<strong class="ll rating_num">([\d.]+)</strong>', html)
    d["rating"] = rm.group(1) if rm else ""

    info, info_block = parse_info_div(html)
    art = re.search(r"<article[^>]*>([\s\S]*?)</article>", html)
    art_body = art.group(1) if art else html

    d["shots"] = extract_shots(art_body) or extract_shots(html)

    txt = re.sub(r"<[^>]+>", " ", art_body)
    txt = txt.replace("&nbsp;", " ").replace("&#8230;", "…").replace("&amp;", "&")
    txt = re.sub(r"\s+", " ", txt).strip()

    if not info:
        info = parse_text_info(txt)
        d["_fmt"] = "text"
        synopsis = txt
        last_pos = 0
        for lm in re.finditer(r"(导演|编剧|主演|类型|制片国家/地区|语言|上映日期|片长|又名|IMDb)\s*[:：]", txt):
            last_pos = lm.end()
        if last_pos:
            synopsis = re.sub(r"^.*?(?=导演\s*[:：]|编剧\s*[:：]|主演\s*[:：]|类型\s*[:：]|豆瓣评分|$)", "", txt, count=1)
            synopsis = re.sub(
                r"(导演|编剧|主演|类型|制片国家/地区|语言|上映日期|片长|又名|IMDb)\s*[:：]\s*[^\n]*?(?=(?:导演|编剧|主演|类型|制片国家/地区|语言|上映日期|片长|又名|IMDb)\s*[:：]|$)",
                "", synopsis)
            synopsis = synopsis.strip()
            if re.search(r"(导演|主演|类型)\s*[:：]", synopsis):
                synopsis = ""
        else:
            synopsis = txt
    else:
        d["_fmt"] = "info"
        body_wo = art_body
        body_wo = re.sub(r'<div id="info">[\s\S]*?</div>', "", body_wo)
        body_wo = re.sub(r'<div id="interest_sectl">[\s\S]*?</div>\s*</div>', "", body_wo)
        body_wo = re.sub(r'<div id="interest_sectl">[\s\S]*?</div>', "", body_wo)
        synopsis = re.sub(r"<[^>]+>", " ", body_wo)
        synopsis = synopsis.replace("&nbsp;", " ").replace("&#8230;", "…").replace("&amp;", "&")
        synopsis = re.sub(r"\s+", " ", synopsis).strip()

    d["synopsis"] = synopsis
    d["info"] = info
    return d


def main():
    files = glob.glob(os.path.join(PAGES, "*.html"))
    out = {}
    for p in files:
        pid = os.path.splitext(os.path.basename(p))[0]
        try:
            html = open(p, encoding="utf-8").read()
            out[pid] = parse_page(html)
        except Exception as e:
            out[pid] = {"error": str(e)}
    atomic_write_json(OUT, out, indent=1)      # 原子写: 中断不会留下截断的 details.json
    n_shots = sum(len(v.get("shots", [])) for v in out.values())
    n_info = sum(1 for v in out.values() if v.get("info"))
    n_syn = sum(1 for v in out.values() if v.get("synopsis"))
    print("parsed:", len(out), "| with info:", n_info, "| with synopsis:", n_syn, "| shots total:", n_shots)


if __name__ == "__main__":
    main()
