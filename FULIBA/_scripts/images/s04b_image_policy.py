# -*- coding: utf-8 -*-
"""
s04b_image_policy.py — 图片落盘策略（磁盘保护）

背景：D 盘仅剩 42.8 GB，而实测已下载 23049 张图占 9.55 GB（均值 414 KB/张），
其中 886 张 >2MB 的图独占 5.47 GB（均值 6.2 MB/张）。按此比例，
剩余约 10 万张图会再消耗 40+ GB → **磁盘写爆**。

策略（正文图）：
- 单图字节 > RAW_KEEP_KB 时，用 Pillow 重采样：长边限 MAX_EDGE，输出 JPEG 质量 QUALITY；
- 重采样后仍 > HARD_KEEP_KB（如 1.5MB）的，**跳过**（不落盘），标记为 skip_oversize；
  这类图是极少数异常大图（如 25MB 的 GIF），对归档浏览无实质价值。
- 头像图（<5KB）原样保留，不做处理。

节省估算：6.2 MB → 约 0.2 MB/张，10 万张约省 600 GB，落在 20 GB 区间内。
"""
import io
import os

# 长边上限（像素）
MAX_EDGE = 1600
# JPEG 质量
QUALITY = 82
# 原始字节超过此值才处理（KB）
RAW_KEEP_KB = 150
# 重采样后仍超过此值（KB）则丢弃
HARD_KEEP_KB = 1500

# 像素数上限：超过此值的巨图直接放弃（不解码）。
# 实测 sinaimg 上存在 9800 万像素的图片，PIL 完整解码会吃掉数百 MB 内存
# 与数十秒 CPU（伴随 DecompressionBombWarning），是下载通道卡死的隐形杀手。
MAX_PIXELS = 40_000_000

MAX_EDGE_LIMIT = MAX_EDGE


def _pil():
    from PIL import Image
    return Image


def too_many_pixels(data):
    """轻量像素数预检：只读图片头，不做完整解码。"""
    try:
        Image = _pil()
        im = Image.open(io.BytesIO(data))
        w, h = im.size
        im.close()
        return (w * h) > MAX_PIXELS
    except Exception:
        return False


def compress_bytes(data, ctype=""):
    """尝试把图片字节压缩为受控尺寸的 JPEG。

    返回 (bytes, ext) 或 (None, None) —— None 表示不适合压缩
    （体积没收益 / 重采样后仍过大 / 无法解码）。

    GIF 特殊处理：抽样实测 GIF 占文件数的 10% 却占体积的 45%
    （均值 2.5 MB/张，多为超长动画），故**取首帧转 JPEG** 存档，
    既保住图像内容，又把体积压到 ~1%。
    """
    if len(data) <= RAW_KEEP_KB * 1024:
        return None, None
    # 巨图预检：不解码直接放弃，避免解码开销拖垮整个下载通道
    if too_many_pixels(data):
        return None, None
    try:
        Image = _pil()
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:
        return None, None

    fmt = (im.format or "").upper()
    if fmt == "SVG":
        return None, None
    if fmt == "GIF":
        # 动图取首帧
        try:
            im.seek(0)
            im = im.convert("RGB")
        except Exception:
            return None, None

    w, h = im.size
    longest = max(w, h)
    if longest > MAX_EDGE:
        ratio = MAX_EDGE / float(longest)
        nw, nh = max(1, int(w * ratio)), max(1, int(h * ratio))
        try:
            im = im.resize((nw, nh), Image.LANCZOS)
        except Exception:
            return None, None

    if im.mode not in ("RGB", "L"):
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")

    buf = io.BytesIO()
    try:
        im.save(buf, format="JPEG", quality=QUALITY, optimize=True)
    except Exception:
        return None, None
    out = buf.getvalue()
    if len(out) > HARD_KEEP_KB * 1024:
        return None, None
    if len(out) >= len(data):
        # 没收益
        return None, None
    return out, ".jpg"
