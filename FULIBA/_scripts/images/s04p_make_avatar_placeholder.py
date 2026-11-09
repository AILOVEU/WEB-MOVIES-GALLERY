# -*- coding: utf-8 -*-
"""
s04p_make_avatar_placeholder.py — 生成评论头像占位图
按用户要求：评论头像不再逐个本地化，统一使用一张默认占位图。
产物：D:\\FULIBA\\assets\\avatar-placeholder.png（96×96，站点根相对路径 assets/avatar-placeholder.png）
用法: python s04p_make_avatar_placeholder.py
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # _scripts/
import common as C

from PIL import Image, ImageDraw

SIZE = 96
BG = (238, 240, 243)
FG = (176, 182, 190)
RING = (214, 218, 223)

ASSETS = os.path.join(C.ROOT, "assets")
OUT = os.path.join(ASSETS, "avatar-placeholder.png")


def main():
    os.makedirs(ASSETS, exist_ok=True)
    im = Image.new("RGB", (SIZE, SIZE), BG)
    d = ImageDraw.Draw(im)

    # 背景圆角
    d.rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=10, fill=BG)
    # 头部
    hr = 15
    cx, cy = SIZE // 2, 36
    d.ellipse([cx - hr, cy - hr, cx + hr, cy + hr], fill=FG)
    # 肩部
    d.ellipse([cx - 25, 58, cx + 25, 118], fill=FG)
    # 描边（浅色，制造轻微层次）
    d.ellipse([cx - hr - 1, cy - hr - 1, cx + hr + 1, cy + hr + 1], outline=RING, width=1)

    im.save(OUT, "PNG", optimize=True)
    print("PLACEHOLDER_OK %s  %d bytes  %dx%d" % (OUT, os.path.getsize(OUT), SIZE, SIZE))


def make_img_placeholder():
    """正文图片占位图（未落地死链图统一替代，s04d 引用）：
    640×360 灰底 + 山形/太阳经典 image 图标 + 英文提示，不依赖字体库。"""
    W, H = 640, 360
    BG = (243, 244, 246)
    FG = (203, 208, 214)
    FG2 = (166, 172, 180)
    OUT2 = os.path.join(ASSETS, "img-placeholder.png")
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    # 图标区：太阳 + 双山（经典 image placeholder 图形，中心区域）
    cx, cy = W // 2, H // 2 - 20
    d.ellipse([cx + 60, cy - 78, cx + 104, cy - 34], fill=FG)          # 太阳
    d.polygon([(cx - 150, cy + 80), (cx - 40, cy - 60), (cx + 30, cy + 20),
               (cx + 70, cy - 20), (cx + 160, cy + 80)], fill=FG2)     # 双山
    d.rectangle([cx - 160, cy - 90, cx + 160, cy + 80], outline=FG, width=3)
    # 提示文字（PIL 默认位图字体，仅 ASCII）
    d.text((cx - 118, cy + 104), "IMAGE NOT AVAILABLE (OFFLINE)", fill=FG2)
    im.save(OUT2, "PNG", optimize=True)
    print("PLACEHOLDER_OK %s  %d bytes  %dx%d" % (OUT2, os.path.getsize(OUT2), W, H))


if __name__ == "__main__":
    main()
    make_img_placeholder()
