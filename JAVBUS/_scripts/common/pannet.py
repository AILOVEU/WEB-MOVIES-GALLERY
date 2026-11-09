# -*- coding: utf-8 -*-
"""网盘链接识别（供 build.py 打标、index.html 列表与详情视图高亮）。

规则来源：先扫本项目 366 帖的真实主机分布定清单（pan.xunlei.com 18、pan.baidu.com 18、
pan.quark.cn 5、蓝奏 lanzouv/lanzouu 各 1），再补用户点名的阿里网盘和几家常见网盘，
最后留一条 generic 兜底：将来站里出现新网盘域名也能标上，而不是静默漏掉。

口径：
- 只认"网盘/云盘分享链接"这类可下载资源的地址；magnet://、ed2k:// 属磁力不算网盘，不标。
- 正文(1L) 命中 = pan；只在楼层命中 = pan_floor（列表页虚线 chip 区分，两者都算"含网盘"）。
- 服务商识别走两级：先按"域名+分享路径"特征精确命中，再按域名归到已知品牌；
  都不命中但域名像网盘(pan/drive/disk/cloud/lanz…)才记 other。
  这样 `www.yunshuzhai.com`（名字带 yun 的普通站）不会被误判。
"""
import re

# (key, 中文标签, 链接特征正则, 域名特征正则)
PROVIDERS = [
    ("baidu", "百度网盘",
     r"(?:https?://)?(?:pan|yun)\.baidu\.com/(?:s|share|surl)/", r"(?:pan|yun)\.baidu\.com"),
    ("ali", "阿里网盘",
     r"(?:https?://)?(?:www\.)?(?:alipan|aliyundrive)\.com/s/", r"(?:alipan|aliyundrive)\.com"),
    ("quark", "夸克网盘",
     r"(?:https?://)?pan\.quark\.cn/s/", r"pan\.quark\.cn"),
    ("xunlei", "迅雷网盘",
     r"(?:https?://)?pan\.xunlei\.com/s/|thunder://", r"pan\.xunlei\.com"),
    ("115", "115网盘",
     r"(?:https?://)?115\.com/(?:s/|\?code=)|115cd\.com/", r"(?:115\.com|115cd\.com)"),
    ("lanzn", "蓝奏云",
     r"(?:https?://)?[a-z0-9.-]*lanz[a-z0-9]*\.(?:com|net|xyz|top|ink|cyou|icu|gg|fun)/",
     r"[a-z0-9.-]*lanz[a-z0-9]*\.(?:com|net|xyz|top|ink|cyou|icu|gg|fun)"),
    ("weiyun", "腾讯微云",
     r"(?:https?://)?(?:www\.)?weiyun\.com/s/", r"weiyun\.com"),
    ("tianyi", "天翼云盘",
     r"(?:https?://)?cloud\.189\.cn/", r"cloud\.189\.cn|189share\.cn"),
    ("yidong", "移动云盘",
     r"(?:https?://)?(?:yun\.139\.com|139y\.com)/", r"yun\.139\.com|139y\.com"),
    ("p123", "123云盘",
     r"(?:https?://)?(?:www\.)?(?:123pan\.com|123684\.com)/[sp]/", r"123pan\.com|123684\.com"),
    ("uc", "UC网盘",
     r"(?:https?://)?(?:drive|pan)\.uc\.cn/", r"(?:drive|pan)\.uc\.cn"),
    ("gdrive", "谷歌盘",
     r"(?:https?://)?drive\.google\.com/(?:file|open|drive)/|(?:https?://)?(?:www\.)?dropbox\.com/s",
     r"drive\.google\.com|dropbox\.com"),
]

_TAG = re.compile(r"<[^>]+>")
# 从文本里抽 host（可无协议）+ 可选路径
HOST_PATH = re.compile(r"(?:https?://)?((?:[a-z0-9](?:[a-z0-9-]{0,60})?\.)+[a-z]{2,})(/[^\s\"'<>&]{3,})?",
                       re.I)
# 域名像网盘的兜底特征
GEN_HOST = re.compile(r"(pan|drive|disk|cloud|netdisk|weiyun|lanz|quark|xunlei|alipan|115|"
                      r"share|vault|yunpan)\.", re.I)
# 明确不是网盘的站点（论坛自身 / 图床 / CDN / 已知误判）
_NOT_PAN = re.compile(r"javbus\.com|javdb\.com|jdbstatic|javcdn|imgur|sis\.la|pixhost|sda1\.dev|"
                      r"corenexis|hdslb\.com|jsdelivr|yunshuzhai|imagetourls|googleapis|gstatic|"
                      r"cloudfront|cdn\.|baidu\.com/(?:f?hide|cmyk)", re.I)

_PROG = [(k, lb, re.compile(rx, re.I), re.compile(hx, re.I)) for (k, lb, rx, hx) in PROVIDERS]
_LABEL = {k: lb for (k, lb, _rx, _hx) in PROVIDERS}


def _plain(html):
    return _TAG.sub(" ", html or "")


def detect(html):
    """返回 [{"key","label"}]（按 PROVIDERS 顺序 + other 收尾）；同一篇同服务商只算一次。"""
    if not html:
        return []
    hay = html + " " + _plain(html)
    seen = set()
    for key, _lb, rx, _hx in _PROG:                 # 一级：域名+分享路径
        if rx.search(hay):
            seen.add(key)
    for m in HOST_PATH.finditer(hay):               # 二级：域名归品牌 / 兜底 other
        host, path = m.group(1), (m.group(2) or "")
        if _NOT_PAN.search(host):
            continue
        for key, _lb, _rx, hx in _PROG:
            if hx.search(host):
                seen.add(key)
                break
        else:
            if path and GEN_HOST.search(host + "."):
                seen.add("other")
    out = [{"key": k, "label": _LABEL[k]} for (k, _lb, _rx, _hx) in _PROG if k in seen]
    if "other" in seen:
        out.append({"key": "other", "label": "其他网盘"})
    return out


def scan_thread(parsed):
    """对一个帖子返回 (正文命中的网盘, 只在楼层命中的网盘)。"""
    art = detect((parsed.get("article") or {}).get("content_html"))
    akeys = set(x["key"] for x in art)
    floors = {}
    for c in parsed.get("comments") or []:
        for hit in detect(c.get("content_html")):
            if hit["key"] not in akeys and hit["key"] not in floors:
                floors[hit["key"]] = hit["label"]
    return art, [{"key": k, "label": v} for k, v in floors.items()]
