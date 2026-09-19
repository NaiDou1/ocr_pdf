#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ocr_pdf —— 把手机拍摄的文档照片批量整理成一个 PDF。

Turn a folder of phone photos of documents into one tidy PDF: straighten each
page automatically with the Windows OCR engine, compress it, and sort pages by
capture time. Windows-only tool; see README.md for the full documentation.

做三件事：
  1. 按拍摄时间排序（优先用文件名里的 IMG_YYYYMMDD_HHMMSS，其次用文件修改时间）；
  2. 自动把没拍正的照片转正 —— 默认调用 Windows 系统 OCR，对每张照片分别试
     原样、顺时针 90°、逆时针 90°、180° 四个方向，哪个方向识别出的文字最多最
     通顺就用哪个（手机没拿稳时横版照片有的要顺时针、有的要逆时针，靠固定方向
     是修不好的）；然后转成黑白/灰度并压缩；
  3. 直接内嵌压缩后的 JPEG 打包成 PDF（不经过二次编码，所以不会有额外损失）。

用法：
    ocr_pdf.py                          # 处理当前目录，输出 <文件夹名>.pdf
    ocr_pdf.py D:\\照片                   # 指定照片目录
    ocr_pdf.py D:\\照片 -o 作业.pdf        # 指定输出文件名
    ocr_pdf.py D:\\照片 -r --bw            # 递归处理子目录，输出纯黑白二值
    ocr_pdf.py --show-rotation-scores   # 打印每张图各方向的 OCR 分数，便于排查
    ocr_pdf.py --no-detect-rotation     # 不自动判方向
    ocr_pdf.py --rotate-ccw ccw         # 不自动判方向时，横版图统一逆时针转
    ocr_pdf.py --portrait-all           # 所有横版图转成竖排长条，全部页面统一竖版
    ocr_pdf.py --help
"""

from __future__ import annotations

import argparse
import io
import os
import re
import sys
from datetime import datetime

JPEG_SUFFIXES = {".jpg", ".jpeg", ".jfif", ".png", ".bmp", ".webp", ".tif", ".tiff"}
NAME_TIME_RE = re.compile(r"(\d{8})[_-]?(\d{6})")

# PDF 页面尺寸：把每页短边固定为 595pt（A4 宽度），长边按图片比例算。
SHORT_SIDE_PT = 595.0


# --------------------------------------------------------------------------
# 依赖检查
# --------------------------------------------------------------------------
def require_pillow(auto_install: bool = True):
    try:
        from PIL import Image, ImageOps  # noqa: F401
        return
    except ImportError:
        pass
    if auto_install:
        print("缺少 Pillow，正在自动安装 …")
        import subprocess

        rc = subprocess.call([sys.executable, "-m", "pip", "install", "--quiet", "pillow"])
        if rc == 0:
            try:
                from PIL import Image, ImageOps  # noqa: F401
                return
            except ImportError:
                pass
    sys.exit(
        "缺少 Pillow，无法处理图片。请先运行：\n"
        f'    "{sys.executable}" -m pip install pillow'
    )


_WINRT_PKGS = (
    "winrt-runtime",
    "winrt-Windows.Foundation",
    "winrt-Windows.Foundation.Collections",
    "winrt-Windows.Media.Ocr",
    "winrt-Windows.Graphics.Imaging",
    "winrt-Windows.Storage",
    "winrt-Windows.Storage.Streams",
    "winrt-Windows.Globalization",
)


def require_winrt(auto_install: bool = True) -> bool:
    """准备好 Windows 系统 OCR（用于自动判断照片该往哪边转）。失败返回 False。"""
    try:
        import winrt.windows.media.ocr  # noqa: F401
        return True
    except ImportError:
        pass
    if not auto_install:
        return False
    print("首次使用自动判方向，正在安装系统 OCR 组件 …")
    import subprocess

    subprocess.call([sys.executable, "-m", "pip", "install", "--quiet", *_WINRT_PKGS])
    try:
        import winrt.windows.media.ocr  # noqa: F401
        return True
    except ImportError:
        return False


# --------------------------------------------------------------------------
# 自动判方向：用系统 OCR 分别识别 4 个方向的文字量，取最通顺的那个
# --------------------------------------------------------------------------
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
_ALNUM_RE = re.compile(r"[A-Za-z0-9]")
_ocr_engine = None
_ocr_broken = False


def _get_ocr_engine(lang: str = "zh-Hans-CN"):
    """取 Windows OCR 引擎；必须在主线程上首次调用。"""
    global _ocr_engine, _ocr_broken
    if _ocr_engine is not None or _ocr_broken:
        return _ocr_engine
    try:
        import winrt.windows.media.ocr as wocr
        import winrt.windows.globalization as wglob

        _ocr_engine = wocr.OcrEngine.try_create_from_language(wglob.Language(lang))
        if _ocr_engine is None:
            _ocr_engine = wocr.OcrEngine.try_create_from_user_profile_languages()
        if _ocr_engine is None:
            print("  提示：系统没有可用的 OCR 语言包，改用固定方向旋转。")
    except Exception as exc:  # pragma: no cover - 环境相关
        print(f"  提示：系统 OCR 不可用（{exc}），改用固定方向旋转。")
        _ocr_broken = True
    return _ocr_engine


def ocr_text_from_image(im) -> str:
    """把一张 PIL 图交给系统 OCR，返回识别到的文字。"""
    import winrt.windows.graphics.imaging as wimg
    import winrt.windows.storage.streams as wstreams

    engine = _get_ocr_engine()
    if engine is None:
        return ""
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=70)
    stream = wstreams.InMemoryRandomAccessStream()
    writer = wstreams.DataWriter(stream)
    writer.write_bytes(buf.getvalue())
    writer.store_async().get()
    stream.seek(0)
    decoder = wimg.BitmapDecoder.create_async(stream).get()
    bitmap = decoder.get_software_bitmap_async().get()
    return engine.recognize_async(bitmap).get().text or ""


def text_score(text: str) -> int:
    """文字量打分：汉字权重 2，字母数字权重 1，再叠加长度。"""
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    alnum = len(_ALNUM_RE.findall(text))
    return cjk * 2 + alnum


def _ocr_variants(im, names):
    """对指定的几个方向分别做 OCR，返回 {方向: 分数}。"""
    from PIL import Image

    maker = {
        "none": lambda x: x,
        "cw": lambda x: x.transpose(Image.Transpose.ROTATE_270),   # 顺时针 90°
        "ccw": lambda x: x.transpose(Image.Transpose.ROTATE_90),   # 逆时针 90°
        "180": lambda x: x.transpose(Image.Transpose.ROTATE_180),
    }
    work = im if im.mode == "L" else im.convert("L")
    long_side = max(work.width, work.height)
    if long_side > 1800:  # OCR 不需要全分辨率，缩小能快一倍以上
        scale = 1800 / long_side
        work = work.resize((max(1, round(work.width * scale)), max(1, round(work.height * scale))),
                           Image.Resampling.LANCZOS)
    return {n: text_score(ocr_text_from_image(maker[n](work))) for n in names}


def detect_rotation(im, *, default: str = "cw", min_score: int = 60, min_gain: float = 1.4):
    """判断这张图应该是正着的、顺时针转、逆时针转还是转 180°。

    4 个方向都试一遍：拍摄时没拿稳的话，横版图可能该顺时针、也可能该逆时针，
    竖版图也可能是完全倒过来的。
    返回 (方向, 各方向得分)。
    """
    scores = _ocr_variants(im, ("none", "cw", "ccw", "180"))

    best = max(scores, key=scores.get)
    best_score = scores[best]

    # 识别不出多少字（空白页/糊了）：保持原样，不要瞎转
    if best_score < min_score:
        return "none", scores

    # 正着的时候文字量本来就最多，不需要转
    if best == "none":
        return "none", scores

    # 优势不明显时不要冒险，沿用设定的默认方向
    runner_up = max((v for k, v in scores.items() if k != best), default=0)
    if best_score < runner_up * min_gain:
        return default, scores

    return best, scores


# --------------------------------------------------------------------------
# 文件收集与排序
# --------------------------------------------------------------------------
def parse_name_time(name: str):
    """从 IMG_20260917_213438.jpg 之类的文件名里取出拍摄时间。"""
    m = NAME_TIME_RE.search(name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def collect_images(folder: str, recursive: bool):
    """收集图片，按拍摄时间升序返回 (排序键, 完整路径) 列表。"""
    items = []
    for root, dirs, files in os.walk(folder):
        dirs.sort()
        for fn in files:
            if os.path.splitext(fn)[1].lower() in JPEG_SUFFIXES:
                full = os.path.join(root, fn)
                when = parse_name_time(fn)
                key_dt = when if when else datetime.fromtimestamp(os.path.getmtime(full))
                # 时间相同时用文件名兜底，保证顺序稳定
                items.append(((key_dt, fn), full))
        if not recursive:
            break
    items.sort(key=lambda it: (it[0][0], it[0][1]))
    return [(k, p) for k, p in items]


# --------------------------------------------------------------------------
# 单张图片处理
# --------------------------------------------------------------------------
def _compress(im, fmt: str, quality: int, dpi: int) -> bytes:
    """把内存里的图编码成 JPEG 字节流。"""
    buf = io.BytesIO()
    save_kw = {"format": "JPEG", "dpi": (dpi, dpi)}
    if fmt == "JPEG":
        save_kw["quality"] = quality
        save_kw["optimize"] = True
        save_kw["progressive"] = False
        if im.mode == "L":
            save_kw["subsampling"] = 0  # 灰度没有色度分量，这里等价于最清晰
    im.save(buf, **save_kw)
    return buf.getvalue()


def process_image(
    path: str,
    *,
    rotate: str = "cw",
    fmt: str = "JPEG",
    quality: int = 72,
    max_long: int = 2200,
    bw: bool = False,
    auto_contrast: bool = True,
    portrait_all: bool = False,
    dpi: int = 150,
    rotate_choice: str | None = None,
) -> dict:
    """读取一张照片，转正 + 黑白化 + 压缩，返回 JPEG 字节和原始尺寸。

    rotate_choice：已经判定好的方向（none/cw/ccw/180）。给 None 时按老办法，
    横版图统一用 rotate 指定的方向。
    """
    from PIL import Image, ImageOps

    with Image.open(path) as src:
        src.load()
        orig_size = src.size
        # 按 EXIF 方向摆正（本机照片 orientation=1，这步基本是空操作，但更保险）
        im = ImageOps.exif_transpose(src) or src
        if im.mode not in ("L", "RGB"):
            im = im.convert("RGB")

        final_landscape = im.width > im.height

        if rotate_choice is None:
            # 老逻辑：横版图统一按 rotate 转；--portrait-all 连竖版图一起转
            if final_landscape and rotate in ("ccw", "cw"):
                rotate_choice = rotate
            elif portrait_all and rotate in ("ccw", "cw"):
                rotate_choice = rotate
            else:
                rotate_choice = "none"

        if rotate_choice != "none":
            if rotate_choice == "cw":
                im = im.transpose(Image.Transpose.ROTATE_270)   # 顺时针 90°
            elif rotate_choice == "ccw":
                im = im.transpose(Image.Transpose.ROTATE_90)    # 逆时针 90°
            elif rotate_choice == "180":
                im = im.transpose(Image.Transpose.ROTATE_180)   # 倒转 180°

        # 转黑白
        im = im.convert("L")

        # 缩放到目标长边（只缩小，不放大小图）
        long_side = max(im.width, im.height)
        if max_long and long_side > max_long:
            scale = max_long / long_side
            new_size = (max(1, round(im.width * scale)), max(1, round(im.height * scale)))
            im = im.resize(new_size, Image.Resampling.LANCZOS)

        # 提亮对比度，题目和公式更清楚
        if auto_contrast:
            im = ImageOps.autocontrast(im, cutoff=1)

        # 纯黑白二值：体积最小，字迹锐利，但数学公式可能稍糊
        if bw:
            im = im.point(lambda v: 255 if v >= 160 else 0, mode="1")

        data = _compress(im, fmt, quality, dpi)
        return {
            "bytes": data,
            "size": (im.width, im.height),
            "orig_size": orig_size,
            "rotated": rotate_choice != "none",
            "rotate_choice": rotate_choice,
            "landscape": final_landscape,
            "path": path,
        }


# --------------------------------------------------------------------------
# 组装 PDF（直接内嵌 JPEG，DCTDecode，不再重新编码）
# --------------------------------------------------------------------------
class _PdfBuilder:
    def __init__(self):
        self.objects: list[bytes] = []  # objects[i] 对应 1 号对象

    def _add(self, payload: bytes) -> int:
        self.objects.append(payload)
        return len(self.objects)

    def add_image(self, jpeg: bytes, w: int, h: int) -> int:
        head = (
            f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} "
            f"/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /DCTDecode "
            f"/Length {len(jpeg)} >>\nstream\n"
        ).encode("ascii")
        return self._add(head + jpeg + b"\nendstream")

    def add_page(self, img_ref: int, w: int, h: int, pt_w: float, pt_h: float) -> int:
        content = f"q\n{pt_w:.2f} 0 0 {pt_h:.2f} 0 0 cm\n/Im0 Do\nQ\n".encode("ascii")
        c_ref = self._add(
            f"<< /Length {len(content)} >>\nstream\n".encode("ascii") + content + b"endstream"
        )
        page = (
            f"<< /Type /Page /Parent 0 0 R /MediaBox [0 0 {pt_w:.2f} {pt_h:.2f}] "
            f"/Resources << /XObject << /Im0 {img_ref} 0 R >> /ProcSet [/PDF /ImageB] >> "
            f"/Contents {c_ref} 0 R >>"
        ).encode("ascii")
        return self._add(page)

    def finish(self, page_refs: list[int], title: str) -> bytes:
        pages_ref = len(self.objects) + 1
        kids = " ".join(f"{r} 0 R" for r in page_refs)
        pages_obj = f"<< /Type /Pages /Count {len(page_refs)} /Kids [{kids}] >>"
        n = self._add(pages_obj.encode("ascii"))

        info_ref = self._add(
            ("<< /Title (" + _pdf_escape(title) + ") /Producer (ocr_pdf.py) >>").encode("utf-8")
        )
        catalog = self._add(f"<< /Type /Catalog /Pages {pages_ref} 0 R >>".encode("ascii"))

        # 给每个 Page 补上 /Parent
        for ref in page_refs:
            idx = ref - 1
            self.objects[idx] = self.objects[idx].replace(b"/Parent 0 0 R",
                                                          f"/Parent {pages_ref} 0 R".encode("ascii"))

        out = io.BytesIO()
        out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = [0]
        for i, payload in enumerate(self.objects, start=1):
            offsets.append(out.tell())
            out.write(f"{i} 0 obj\n".encode("ascii"))
            out.write(payload)
            out.write(b"\nendobj\n")

        xref_pos = out.tell()
        count = len(self.objects) + 1
        out.write(f"xref\n0 {count}\n".encode("ascii"))
        out.write(b"0000000000 65535 f \n")
        for off in offsets[1:]:
            out.write(f"{off:010d} 00000 n \n".encode("ascii"))
        out.write(
            f"trailer\n<< /Size {count} /Root {catalog} 0 R /Info {info_ref} 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n".encode("ascii")
        )
        assert n == pages_ref
        return out.getvalue()


def _pdf_escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def build_pdf(records: list[dict], out_path: str, title: str) -> int:
    b = _PdfBuilder()
    page_refs = []
    for rec in records:
        w, h = rec["size"]
        ratio = max(w, h) / min(w, h)
        long_pt = SHORT_SIDE_PT * ratio
        pt_w, pt_h = (SHORT_SIDE_PT, long_pt) if h >= w else (long_pt, SHORT_SIDE_PT)
        img_ref = b.add_image(rec["bytes"], w, h)
        page_refs.append(b.add_page(img_ref, w, h, pt_w, pt_h))

    data = b.finish(page_refs, title)
    with open(out_path, "wb") as fh:
        fh.write(data)
    return len(data)


# --------------------------------------------------------------------------
# 命令行
# --------------------------------------------------------------------------
def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{int(n)}B"
        n /= 1024
    return f"{n:.1f}GB"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="把手机拍的黑白文档照片按拍摄顺序压缩打包成一个 PDF。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("folder", nargs="?", default=".", help="照片所在目录（默认：当前目录）")
    ap.add_argument("-o", "--output", default=None, help="输出的 PDF 路径（默认：<目录名>.pdf）")
    ap.add_argument("-r", "--recursive", action="store_true", help="递归处理子目录")
    ap.add_argument("--rotate-ccw", choices=["cw", "ccw", "none"], default="cw",
                    help="关掉自动判方向时，横版照片统一用的方向，默认 cw=顺时针")
    ap.add_argument("--no-detect-rotation", action="store_true",
                    help="不调用系统 OCR 自动判方向，直接用 --rotate-ccw 指定的固定方向")
    ap.add_argument("--ocr-lang", default="zh-Hans-CN",
                    help="OCR 识别用的语言，默认 zh-Hans-CN（中文简体）")
    ap.add_argument("--show-rotation-scores", action="store_true",
                    help="打印每张图各方向的 OCR 分数，方便排查判错的情况")
    ap.add_argument("-q", "--quality", type=int, default=72, help="JPEG 质量 1-95（默认 72）")
    ap.add_argument("--max-long", type=int, default=2200,
                    help="长边最大像素，超出则等比缩小（默认 2200；0=不缩放）")
    ap.add_argument("--bw", action="store_true", help="输出纯黑白二值图，体积最小")
    ap.add_argument("--no-auto-contrast", action="store_true", help="不做自动对比度增强")
    ap.add_argument("--portrait-all", action="store_true",
                    help="把竖版照片也转成横躺，最终每页都是竖排长条（页面方向统一）")
    ap.add_argument("--keep-temp", action="store_true", help="保留下载的中间 JPEG 到 _pdf_pages/")
    ap.add_argument("--quiet", action="store_true", help="不打印每页进度")
    args = ap.parse_args(argv)

    require_pillow()

    folder = os.path.abspath(args.folder)
    if not os.path.isdir(folder):
        sys.exit(f"目录不存在：{folder}")

    folder_name = os.path.basename(folder.rstrip("\\/")) or "output"
    out_path = args.output or (folder_name + ".pdf")
    if not os.path.isabs(out_path):
        out_path = os.path.join(folder, out_path)

    images = collect_images(folder, args.recursive)
    # 排除自己上一次生成的中间文件目录
    images = [it for it in images if "_pdf_pages" not in it[1]]
    if not images:
        sys.exit("没找到任何图片文件。")

    print(f"照片目录：{folder}")
    print(f"共找到 {len(images)} 张图片，开始处理 …\n")

    temp_dir = os.path.join(folder, "_pdf_pages")
    if args.keep_temp:
        os.makedirs(temp_dir, exist_ok=True)

    # ---- 准备自动判方向 ----
    use_ocr = not args.no_detect_rotation
    if use_ocr:
        if not require_winrt():
            print("  提示：没能装上系统 OCR 组件，改用固定方向旋转")
            print(f'        需要自动判方向的话，先运行："{sys.executable}" -m pip install '
                  + " ".join(_WINRT_PKGS) + "\n")
            use_ocr = False
        elif _get_ocr_engine(args.ocr_lang) is None:
            use_ocr = False
        else:
            print("自动判方向：已启用（用系统 OCR 识别每张图的文字方向）")

    records = []
    total_src = total_out = 0
    for i, (_, path) in enumerate(images, start=1):
        src_bytes = os.path.getsize(path)

        # 用系统 OCR 判断这张图该怎么转
        rotate_choice = None
        extra = ""
        if use_ocr:
            from PIL import Image as _Image

            with _Image.open(path) as _probe:
                _probe.load()
                choice, scores = detect_rotation(
                    _probe, default=args.rotate_ccw if args.rotate_ccw != "none" else "none")
            rotate_choice = choice
            if args.show_rotation_scores:
                extra = "  分数 " + " ".join(f"{k}={v}" for k, v in scores.items())

        rec = process_image(
            path,
            rotate=args.rotate_ccw,
            quality=args.quality,
            max_long=args.max_long,
            bw=args.bw,
            auto_contrast=not args.no_auto_contrast,
            portrait_all=args.portrait_all,
            rotate_choice=rotate_choice,
        )
        total_src += src_bytes
        total_out += len(rec["bytes"])
        rec["src_bytes"] = src_bytes
        records.append(rec)

        if not args.quiet:
            if not rec["rotated"]:
                tag = "保持  "
            else:
                tag = {"cw": "顺转90", "ccw": "逆转90", "180": "转180 "}[rec["rotate_choice"]]
            print(f"  [{i:>3}/{len(images)}] {os.path.basename(path):<28} {tag}  "
                  f"{rec['orig_size'][0]}x{rec['orig_size'][1]} → {rec['size'][0]}x{rec['size'][1]}  "
                  f"{human(src_bytes)} → {human(len(rec['bytes']))}{extra}")

        if args.keep_temp:
            with open(os.path.join(temp_dir, f"page_{i:03d}.jpg"), "wb") as fh:
                fh.write(rec["bytes"])

    title = os.path.splitext(os.path.basename(out_path))[0]
    pdf_bytes = build_pdf(records, out_path, title)

    print(f"\n完成！共 {len(records)} 页")
    print(f"  原图合计：{human(total_src)}")
    print(f"  压缩合计：{human(total_out)}（约 {total_out / total_src * 100:.0f}%）")
    print(f"  输出文件：{out_path}（{human(pdf_bytes)}）")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已中断。")
        sys.exit(130)
