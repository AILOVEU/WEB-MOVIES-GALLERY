# -*- coding: utf-8 -*-
"""合并 details.json + magnets.json 进 data.js (幂等, 可反复执行)

字段语义 (空值不写入, 但会清除上次写入的失效值):
  wr  编剧        aka 又名        syn 简介 (过滤纯评分数字残留)
  sh  本地剧照路径数组 (assets/screenshots/{id}_{n}.ext)
  mg  磁力链接数组 (有则填; 已扫描但无磁力则 ""; 未扫描则字段缺失)
  mgv 磁力获取方式 (click2 = 二次点击获取)

用法: python _scripts/pipeline/07_build_datajs.py [--dir=<画廊目录>]
     python _scripts/pipeline/07_build_datajs.py --fill-basic   # 额外用 _raw_posts.json 补齐 data.js 中为空的基数字段
                                           （只填空白、绝不覆盖已有值；先跑 _scripts/pipeline/01_fetch_meta.py --retitle）
"""
import json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # -> _scripts/（common.py 所在）
from common import (gallery_dir, datajs_path, meta_file,
                    load_datajs, save_datajs, SHOT_PREFIX, has_flag)

GDIR = gallery_dir()
DATAJS = datajs_path(GDIR)
DETAILS = meta_file(GDIR, "details.json")
MAGNETS = meta_file(GDIR, "magnets.json")

data = load_datajs(DATAJS)
print("data.js movies:", len(data))


def require_map(path, producer, name):
    """源数据必须存在且非空, 否则中止。

    details/magnets 缺失时继续跑会让下面每条记录走 "无值" 分支,
    静默清空 data.js 的 wr/aka/syn/sh/mg（历史上同类事故造成过不可逆丢字段）。
    """
    if not os.path.exists(path):
        raise SystemExit(
            "缺少 %s —— 拒绝在源数据缺失时重写 data.js（会清空 wr/aka/syn/sh/mg）\n"
            "  先生成它: python %s" % (path, producer))
    with open(path, encoding="utf-8") as f:
        obj = json.load(f)
    if not isinstance(obj, dict) or not obj:
        raise SystemExit("%s 内容为空/格式异常, 中止（避免误清 data.js 字段）" % path)
    print("%s: %d 条" % (name, len(obj)))
    return obj


details = require_map(DETAILS, "_scripts/pipeline/04_parse_details.py", "details")
magnets = require_map(MAGNETS, "_scripts/pipeline/06_magnets.py", "magnets")

# --fill-basic: 用元数据补齐 data.js 中为空的基数字段（绝不覆盖已有值，幂等）
FILL_KEYS = [("t", "title"), ("y", "year"), ("r", "region"), ("g", "genre"),
             ("s", "size"), ("v", "format"), ("b", "subs"), ("db", "douban"),
             ("a", "actors"), ("dr", "director"), ("du", "duration"), ("lb", "lang")]
if has_flag("--fill-basic"):
    RAWP = meta_file(GDIR, "_raw_posts.json")
    if not os.path.exists(RAWP):
        raise SystemExit("--fill-basic 需要 %s（先跑 _scripts/pipeline/01_fetch_meta.py）" % RAWP)
    items = {it["id"]: it for it in json.load(open(RAWP, encoding="utf-8"))["items"]}
    n_field = n_rec = 0
    for c in data:
        it = items.get(c["i"])
        if not it:
            continue
        filled = 0
        for k, src in FILL_KEYS:
            if not str(c.get(k) or "").strip() and str(it.get(src) or "").strip():
                c[k] = it[src]
                filled += 1
        n_field += filled
        n_rec += 1 if filled else 0
    print("fill-basic: 补齐 %d 个字段, 涉及 %d 部影片" % (n_field, n_rec))

# --clean-basic: 只截断、不覆写的脏值清洗（幂等）。
# 旧解析器在“制片国家 / 地区”（带空格）等变体上切不开标签，把后续正文并进了字段；
# 新版 04_parse_details.py 已加边界（clip_label），本开关用于把历史存量也清干净。
# 原则：只做【前缀截断】与【去尾部“更多…”】，绝不引入源里没有的内容；
# “以源覆写”是错的 —— details.json 自身也还有 150+ 条把简介/年份/演员并进了字段。
RESIDUE = re.compile(
    r"(?:导演|编剧|主演|演员|类型|制片国家\s*/\s*地区|制片国家|语言|上映日期|片长|又名|IMDb|豆瓣评分|地区|年份|片名|译名|简介)\s*[:：]"
    r"|豆瓣评分\s*[\d.]*")      # “豆瓣评分 5.0”常常没有冒号
TAIL = re.compile(r"\s*/?\s*更多\s*[…\.]*\s*$")
CLEAN_KEYS = ("r", "g", "a", "dr", "wr", "du", "lb", "b", "v", "aka")

def clean_basic(data):
    """只截断、不覆写的存量脏值清洗；**必须在合并 details 之后调用**，
    否则随后被合并回来的字段又会把刚清好的值盖回去（曾因此不幂等）。"""
    n_field = n_rec = 0
    for c in data:
        hit = 0
        for k in CLEAN_KEYS:
            val = c.get(k)
            if not isinstance(val, str) or not val:
                continue
            new = val
            m = RESIDUE.search(new)
            if m:
                new = new[:m.start()]
            new = TAIL.sub("", new).strip(" /,，、:：")
            if new != val:
                if new:
                    c[k] = new
                else:
                    c.pop(k, None)
                hit += 1
        n_field += hit
        n_rec += 1 if hit else 0
    print("clean-basic: 截断/去尾 %d 个字段, 涉及 %d 部影片" % (n_field, n_rec))

n_wr = n_syn = n_sh = n_mg = 0
n_det_skip = n_mag_open = n_err = 0
for c in data:
    pid = str(c["i"])
    det = details.get(pid)
    if det is None or det.get("error"):
        # 源里没有这条页面记录或解析失败 → 保留原字段, 不能当成"确认无值"
        n_det_skip += 1
    else:
        info = det.get("info", {})
        if info.get("writer"):
            c["wr"] = info["writer"]; n_wr += 1
        else:
            c.pop("wr", None)
        if info.get("aka"):
            c["aka"] = info["aka"]
        else:
            c.pop("aka", None)
        syn = det.get("synopsis") or ""
        syn = re.sub(r"^\d+(\.\d+)?\s+", "", syn.strip())  # 去掉开头残留的豆瓣评分
        if syn and not syn.startswith("豆瓣评分") and not re.fullmatch(r"\d+(\.\d+)?", syn):
            c["syn"] = syn; n_syn += 1
        else:
            c.pop("syn", None)  # 清除上次合并可能写入的失效值
        shots = det.get("shots") or []
        if shots:
            local = []
            for n, url in enumerate(shots, 1):
                ext = os.path.splitext(url.split("?")[0])[1] or ".jpg"
                local.append("%s/%s_%d%s" % (SHOT_PREFIX, pid, n, ext))
            c["sh"] = local; n_sh += 1

    mg = magnets.get(pid)
    if mg is None:
        n_mag_open += 1              # 未扫描 → mg 字段保持缺失
    elif mg.get("magnets"):
        c["mg"] = mg["magnets"]          # 数组: 全部磁力链接
        if mg.get("via"):
            c["mgv"] = mg["via"]          # click2 = 二次点击获取
        else:
            c.pop("mgv", None)
        n_mg += 1
    elif mg.get("error") or mg.get("state") == "error":
        # 网络/超时故障 ≠ 确认无磁力 → 回到"未扫描", 交给 _scripts/pipeline/06_magnets.py --refetch-no-magnet 重扫
        n_err += 1
        c.pop("mg", None); c.pop("mgv", None)
    else:
        c["mg"] = ""                 # 已扫描确认无磁力（VIP 购买 / 空响应 / other）
        c.pop("mgv", None)

print("with writer:%d synopsis:%d shots:%d magnet:%d" % (n_wr, n_syn, n_sh, n_mg))
if has_flag("--clean-basic"):
    clean_basic(data)      # 放在合并之后：details 覆盖进来的字段也要过一遍（否则不幂等）
if n_det_skip or n_mag_open or n_err:
    print("保留原值: 无页面记录/解析失败 %d 条 | 磁力未扫描 %d 条 | 磁力扫描出错改回未扫描 %d 条"
          % (n_det_skip, n_mag_open, n_err), flush=True)
size = save_datajs(DATAJS, data)
print("data.js rewritten:", size, "bytes")
