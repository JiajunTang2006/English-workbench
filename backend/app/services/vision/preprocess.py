"""本地图像预处理（方案 §10.7 L3-A）。

只用标准库 + Pillow（已装）。负责：受控缩放、缩略图、尺寸/像素记录、
PDF 页面渲染能力检测（依赖缺失时 fail-closed 明确提示，不静默崩）。
大规模 PDF 渲染依赖（pdf2image/fitz）不在本环境，故 PDF 渲染显式降级。
"""

from __future__ import annotations

import os

from PIL import Image

# 受控上限（保守取值，避免内存放大）
MAX_DIMENSION = 2000
THUMBNAIL_SIZE = (320, 320)
SUPPORTED_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}

PDF_RENDER_DEPS_AVAILABLE = False  # pdf2image/fitz 本环境缺失


def is_supported_image(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in SUPPORTED_IMAGE_EXT


def normalize_image(path: str, max_dimension: int = MAX_DIMENSION) -> dict:
    """受控缩放并读取尺寸/像素，返回元信息（不写原图）。

    返回：{width, height, mode, pixels, resized_path|None}
    若已在上限内则不生成新文件（resized_path=None）。
    """
    with Image.open(path) as im:
        im = im.convert("RGB")
        width, height = im.size
        pixels = width * height
        resized_path = None
        if max(width, height) > max_dimension:
            ratio = max_dimension / float(max(width, height))
            new_size = (int(width * ratio), int(height * ratio))
            im = im.resize(new_size, Image.LANCZOS)
            resized_path = f"{path}.resized.jpg"
            im.save(resized_path, "JPEG", quality=85)
            width, height = new_size
            pixels = width * height
        return {
            "width": width,
            "height": height,
            "mode": im.mode,
            "pixels": pixels,
            "resized_path": resized_path,
        }


def make_thumbnail(path: str, size=THUMBNAIL_SIZE) -> str:
    """生成缩略图，返回缩略图路径。"""
    thumb_path = f"{path}.thumb.jpg"
    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail(size, Image.LANCZOS)
        im.save(thumb_path, "JPEG", quality=80)
    return thumb_path


def pdf_render_supported() -> bool:
    return PDF_RENDER_DEPS_AVAILABLE


def assert_pdf_render_or_fail() -> None:
    """PDF 页面渲染依赖缺失时显式失败（fail-closed）。"""
    if not PDF_RENDER_DEPS_AVAILABLE:
        raise RuntimeError(
            "PDF 页面渲染依赖（pdf2image/fitz）未安装；"
            "请先安装渲染依赖或改用纯文本 PDF 本地提取路径。"
        )
