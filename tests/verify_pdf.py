# -*- coding: utf-8 -*-
"""校验生成的 PDF：文件头、页数、交叉引用表自洽性、页面尺寸。

用法：  python tests/verify_pdf.py [PDF路径] [期望页数]
"""
import io
import os
import re
import sys

PDF = sys.argv[1] if len(sys.argv) > 1 else "sample/sample.pdf"
EXPECTED_PAGES = int(sys.argv[2]) if len(sys.argv) > 2 else 4


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def verify(path, expected_pages):
    data = io.open(path, "rb").read()
    check(data.startswith(b"%PDF-"), f"{path} 不是 PDF 文件")

    # startxref 必须指向真正的 xref 表
    m = re.search(rb"startxref\s+(\d+)", data)
    check(m is not None, "找不到 startxref")
    start = int(m.group(1))
    check(data[start:start + 4] == b"xref", f"startxref={start} 没有指向 xref 表")

    # 交叉引用表每一项都要指向对应的对象
    tail = data[start:]
    head = re.match(rb"xref\r?\n0 (\d+)\r?\n", tail)
    check(head is not None, "xref 表头格式不对")
    count, body = int(head.group(1)), head.end()
    offsets = []
    for k in range(1, count):
        entry = tail[body + k * 20: body + (k + 1) * 20]
        check(len(entry) == 20, f"xref 第 {k} 项长度不对")
        off = int(entry[0:10])
        tag = f"{k} 0 obj".encode()
        check(data[off:off + len(tag)] == tag,
              f"xref 第 {k} 项偏移 {off} 未指向对象（实际是 {data[off:off+16]!r}）")
        offsets.append(off)

    # 页数
    pages = len(re.findall(rb"/Type /Page[^s]", data))
    check(pages == expected_pages, f"页数不对：期望 {expected_pages}，实际 {pages}")

    # 每张图都必须有配套的 MediaBox，且尺寸是正数
    boxes = re.findall(rb"/MediaBox \[0 0 ([\d.]+) ([\d.]+)\]", data)
    check(len(boxes) == pages, f"MediaBox 数量 {len(boxes)} 与页数 {pages} 不一致")
    for w, h in boxes:
        check(float(w) > 0 and float(h) > 0, f"页面尺寸非法：{w} x {h}")

    # 所有图片都用 DCTDecode（直接内嵌 JPEG，不二次编码）
    images = re.findall(rb"/Filter /DCTDecode", data)
    check(len(images) == pages, f"内嵌 JPEG 数量 {len(images)} 与页数 {pages} 不一致")

    print(f"OK  {path}")
    print(f"    页数 {pages}，对象 {count - 1} 个，体积 {len(data) / 1024:.0f} KB")
    print(f"    startxref={start}，xref 各项偏移校验通过，全部页面为 DCTDecode 内嵌 JPEG")


if __name__ == "__main__":
    if not os.path.exists(PDF):
        sys.exit(f"文件不存在：{PDF}")
    verify(PDF, EXPECTED_PAGES)
