# -*- coding: utf-8 -*-
"""从 javbus.com 抓磁力链接（替代 javdb.com，后者对爬虫不友好）。

为什么换源（用户 2026-11-24 决定）：
  javdb.com 现在连 top250 都要登录，cookie 三天就失效，CF 质询频繁，抓一轮要反复过验证。
  javbus.com 只要过掉年龄验证（服务端下发 cookie）就能稳定抓，磁力条数还更多
  （实测 SSIS-001：javbus 43 条 vs javdb 1 条）。

页面结构（2026-11-24 实测）：
  磁力行**不是**服务端直出的，主页面只给表头 + 「讀取中...」，
  真正的数据由 JS 再发一次 AJAX：
    /ajax/uncledatoolsbyajax.php?gid=<gid>&lang=zh&img=<cover>&uc=<uc>&floor=<rand>
  gid / uc / img 三个变量内联在主页面 <script> 里（var gid = ...; var uc = 0; var img = '...'）。
  该接口返回的仍是同样结构的 <table id="magnet-table"> 行：
    <tr height="35px">
      <td width="70%" onclick="window.open('magnet:?xt=...')">
        <a href="magnet:?xt=urn:btih:XXX&dn=NAME">NAME</a>
      </td>
      <td>2.02GB</td>                           <- 大小
      <td>2025-10-28</td>                       <- 分享日期
      <td>...</td>                              <- 操作列
    </tr>

用法：
  python javbus_magnet.py --codes=SSIS-001,ABP-123     # 指定番号
  python javbus_magnet.py --dry-run                    # 只看要抓多少
  作为库被 magnet_spider.py 调用：fetch(code) -> list | None
"""
import html as htmllib
import io
import os
import random
import re
import subprocess
import sys
import time

BASE = "https://www.javbus.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36")
PROXY = os.environ.get("JAVBUS_PROXY", "http://127.0.0.1:7890")

# 年龄验证：服务端认这两个 cookie（existmag 控制是否显示磁力）
COOKIE = "existmag=all; age=verified; dv=1"

ROW_RE = re.compile(r"<tr[^>]*height=\"35px\"[^>]*>(.*?)</tr>", re.S | re.I)
MAG_RE = re.compile(r'href="(magnet:\?xt=urn:btih:[^"]+)"', re.I)
TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)
TAG_RE = re.compile(r"<[^>]+>")
GID_RE = re.compile(r"var\s+gid\s*=\s*(\d+)")
UC_RE = re.compile(r"var\s+uc\s*=\s*(\d+)")
IMG_RE = re.compile(r"var\s+img\s*=\s*'([^']*)'")


def log(*a):
    print("[javbus]", *a, flush=True)


def _plain(s):
    return htmllib.unescape(TAG_RE.sub("", s or "")).strip()


def _size_to_mb(s):
    """'2.02GB' -> 2068；'1.5MB' -> 1.5 -> 2（取整）。解析不了返回 0。"""
    m = re.search(r"([\d.]+)\s*([KMGT]i?B)?", (s or "").upper())
    if not m:
        return 0
    try:
        v = float(m.group(1))
    except ValueError:
        return 0
    unit = (m.group(2) or "MB").upper()
    mult = {"KB": 1.0 / 1024, "MB": 1, "GB": 1024, "TB": 1024 * 1024}.get(
        unit.replace("I", ""), 1)
    return int(round(v * mult))


def curl(url, timeout=30):
    r = subprocess.run(
        ["curl", "-s", "--ssl-no-revoke", "--max-time", str(timeout),
         "-x", PROXY, "-A", UA, "-H", "Cookie: " + COOKIE,
         "-H", "Referer: " + BASE + "/", url],
        capture_output=True, text=True, encoding="utf-8", errors="ignore")
    return r.stdout or ""


def parse(html):
    """解析磁力表格行。返回 list（可能为空）。页面异常返回 None。

    注意：AJAX 接口返回的是**裸 <tr> 片段**，不带 <table id="magnet-table"> 包裹，
    所以不能用 magnet-table 作为判定锚点，改用「有没有 height="35px" 的行」。
    """
    if not html:
        return None
    if "Age Verification" in html or "ageVerify" in html:
        return None
    rows = ROW_RE.findall(html)
    if not rows:
        # 没有数据行：可能确实无磁力，也可能页面异常
        if "magnet" in html.lower() or "磁力" in html:
            return []
        return None
    out = []
    for blk in rows:
        mm = MAG_RE.search(blk)
        if not mm:
            continue
        link = htmllib.unescape(mm.group(1))
        tds = TD_RE.findall(blk)
        name = _plain(tds[0]) if tds else ""
        size_s = _plain(tds[1]) if len(tds) > 1 else ""
        date_s = _plain(tds[2]) if len(tds) > 2 else ""
        # 名称里的「高清/字幕」等是 javbus 的标签，转成 tags 字段（与 javdb 口径对齐）
        tags = []
        for kw in ("高清", "字幕", "無修正", "流出", "破坏版", "破壞版"):
            if kw in name:
                tags.append("高清" if kw == "高清" else kw)
        # 去重保序
        seen, tl = set(), []
        for t in tags:
            if t not in seen:
                seen.add(t)
                tl.append(t)
        out.append({
            "link": link,
            "name": name,
            "sizeMB": _size_to_mb(size_s),
            "size": size_s or "",
            "files": 0,                       # javbus 列表页不给文件数
            "date": (date_s or "").replace("-", ""),
            "tags": tl,
        })
    return out


# 樣品圖像：<a class="sample-box" href="原图地址"><div class="photo-frame"><img src="/pics/sample/xxx_N.jpg">
SAMPLE_RE = re.compile(r'class="sample-box"[^>]*href="([^"]+)"', re.I)


def parse_samples(html):
    """从详情页提取「樣品圖像」的原图地址（不下载，只记 URL）。

    优先取 sample-box 的 href（DMM 原图，分辨率最高）；
    href 缺失时退回 img src（javbus 自己的缩略图），并补成绝对地址。
    返回 list[str]，保持页面顺序。
    """
    if not html:
        return []
    out, seen = [], set()
    for m in re.finditer(r'<a[^>]*class="sample-box"[^>]*>(.*?)</a>', html, re.S | re.I):
        blk = m.group(0)
        hm = re.search(r'href="([^"]+)"', blk, re.I)
        url = htmllib.unescape(hm.group(1)) if hm else ""
        if not url:
            im = re.search(r'<img[^>]*src="([^"]+)"', blk, re.I)
            url = htmllib.unescape(im.group(1)) if im else ""
        if not url:
            continue
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            url = BASE + url
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


# javbus 详情页上「视频预告」的痕迹（2026-11-25 实测：44 个样本全部没有）
VIDEO_MARKS = (
    re.compile(r"<video", re.I),
    re.compile(r"\.m3u8", re.I),
    re.compile(r"sample\.mp4|preview\.mp4|trailer", re.I),
    re.compile(r"樣品影片|样品影片|預告片|预告片"),
)


def has_video_preview(html):
    """javbus 详情页是否带视频预告入口。返回 (bool, 命中的标记列表)。"""
    if not html:
        return False, []
    hits = [p.pattern for p in VIDEO_MARKS if p.search(html)]
    return bool(hits), hits


def fetch_detail(code, timeout=30, retries=3):
    """抓一个番号的详情：返回 dict 或 None（抓取失败）。

    返回 {'magnets': [...], 'samples': [...], 'has_video': bool, 'video_marks': [...]}
      magnets: list（可能为空 = 确认无磁力）
      samples: list[str] 樣品圖像原图地址（不下载，只记 URL）
      has_video: 详情页是否带视频预告入口（javbus 实测基本为 False）
    """
    url = "%s/%s" % (BASE, code)
    for attempt in range(retries):
        html = curl(url, timeout=timeout)
        if not html:
            if attempt < retries - 1:
                time.sleep(3 + attempt * 4)
            continue
        # 年龄验证页：没拿到 cookie 放行
        if "Age Verification" in html or "ageVerify" in html:
            if attempt < retries - 1:
                time.sleep(3 + attempt * 4)
            continue
        # 番号不存在
        if "找不到" in html or "not found" in html.lower():
            return {"magnets": [], "samples": [], "has_video": False, "video_marks": []}
        samples = parse_samples(html)
        has_video, marks = has_video_preview(html)
        mg, mu, mi = GID_RE.search(html), UC_RE.search(html), IMG_RE.search(html)
        if not mg:
            # 没有 gid：页面正常但无磁力（如「無磁力連結」）
            if "磁力" in html:
                return {"magnets": [], "samples": samples,
                        "has_video": has_video, "video_marks": marks}
            return None
        gid = mg.group(1)
        uc = mu.group(1) if mu else "0"
        img = mi.group(1) if mi else ""
        ajax = ("%s/ajax/uncledatoolsbyajax.php?gid=%s&lang=zh&img=%s&uc=%s&floor=%d"
                % (BASE, gid, img, uc, random.randint(1, 1000)))
        body = curl(ajax, timeout=timeout)
        got = parse(body)
        if got is not None:
            return {"magnets": got, "samples": samples,
                    "has_video": has_video, "video_marks": marks}
        if attempt < retries - 1:
            time.sleep(3 + attempt * 4)
    return None


def fetch(code, timeout=30, retries=3):
    """只要磁力（兼容旧调用）。返回 list 或 None。"""
    r = fetch_detail(code, timeout=timeout, retries=retries)
    return None if r is None else r["magnets"]


def main():
    import argparse
    ap = argparse.ArgumentParser(description="从 javbus.com 抓磁力链接")
    ap.add_argument("--codes", default="", help="逗号分隔的番号")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    codes = [c.strip() for c in a.codes.split(",") if c.strip()]
    if not codes:
        log("需要 --codes=SSIS-001,ABP-123")
        return 1
    if a.dry_run:
        log("将抓 %d 个番号（dry-run，不发请求）" % len(codes))
        return 0

    ok = fail = 0
    for c in codes:
        got = fetch(c)
        if got is None:
            log("%s -> FAIL" % c)
            fail += 1
        else:
            log("%s -> %d 条" % (c, len(got)))
            ok += 1
        time.sleep(1.5)
    log("完成：成功 %d，失败 %d" % (ok, fail))
    return 0


if __name__ == "__main__":
    sys.exit(main())
