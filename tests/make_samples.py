# -*- coding: utf-8 -*-
"""生成一组测试用的合成"照片"，覆盖四种需要不同处理方向的情况。

用法：  python tests/make_samples.py [输出目录]     # 默认 sample/
"""
import os
import sys

from PIL import Image, ImageDraw


def make_page(text, size=(1600, 2200)):
    """画一页有文字的假文档（文字方向是正的）。"""
    im = Image.new("L", size, 255)
    draw = ImageDraw.Draw(im)
    draw.rectangle([0, 0, size[0] - 1, 90], fill=0)
    draw.text((40, 30), text.upper(), fill=255)
    for i in range(30):
        draw.text((80, 150 + i * 60), f"{text} - line {i:02d} - 0123456789", fill=0)
    return im


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "sample"
    os.makedirs(out, exist_ok=True)

    pages = {
        # 竖版，本来就是正的 -> 应保持原样
        "IMG_20260101_120000.jpg": make_page("portrait upright"),
        # 竖版但完全倒过来 -> 应旋转 180°
        "IMG_20260101_120001.jpg": make_page("portrait upside down").rotate(180, expand=True),
        # 横版，左边是顶端 -> 应顺时针 90°
        "IMG_20260101_120002.jpg": make_page("landscape needs cw", (2200, 1600)).rotate(-90, expand=True),
        # 横版，右边是顶端 -> 应逆时针 90°
        "IMG_20260101_120003.jpg": make_page("landscape needs ccw", (2200, 1600)).rotate(90, expand=True),
    }
    for name, im in pages.items():
        im.save(os.path.join(out, name), quality=85)
    print(f"已生成 {len(pages)} 张测试图片到 {out}/")


if __name__ == "__main__":
    main()
