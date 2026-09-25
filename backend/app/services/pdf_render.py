"""统一的 PDF 页面渲染服务。

扫描件 OCR 与试卷录入都需要把 PDF 页面渲染成图片。以前只有 ``pdftoppm``
一条路径，而参考 Windows 包并未携带 Poppler：干净 Windows 环境即使配好了
视觉模型，也会一直停在待 OCR。这里提供唯一入口：

- 优先使用随包分发的 ``pypdfium2``（自带 pdfium 动态库、跨平台 wheel）；
- 没有 ``pypdfium2`` 时回退到系统 ``pdftoppm``；
- 两者都不可用时返回明确的不可用原因，而不是静默失败。

渲染超时与页数上限由调用方负责（见 ``pdf_ocr``），本模块只做单页渲染。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


DEFAULT_DPI = 150
PDFTOPPM_TIMEOUT_SECONDS = 45


def available_renderer() -> str | None:
    """返回当前可用的渲染后端名称，均不可用返回 ``None``。"""
    try:
        import pypdfium2  # noqa: F401
    except Exception:
        pass
    else:
        return "pypdfium2"
    if shutil.which("pdftoppm") is not None:
        return "pdftoppm"
    return None


def _render_with_pdfium(path: Path, page_no: int, output: Path, *, dpi: int) -> bool:
    try:
        import pypdfium2 as pdfium
    except Exception:
        return False
    try:
        document = pdfium.PdfDocument(str(path))
        try:
            if page_no < 1 or page_no > len(document):
                return False
            page = document[page_no - 1]
            try:
                bitmap = page.render(scale=dpi / 72)
                image = bitmap.to_pil()
                try:
                    image.convert("RGB").save(output, format="JPEG", quality=88)
                finally:
                    close = getattr(image, "close", None)
                    if callable(close):
                        close()
            finally:
                page.close()
        finally:
            document.close()
    except Exception:
        # 损坏页/加密页等异常不应中断整份文档；交由调用方记为未解析页。
        return False
    return output.is_file() and output.stat().st_size > 0


def _render_with_pdftoppm(path: Path, page_no: int, prefix: Path, *, dpi: int) -> bool:
    converter = shutil.which("pdftoppm")
    if converter is None:
        return False
    try:
        result = subprocess.run(
            [converter, "-f", str(page_no), "-l", str(page_no), "-singlefile",
             "-jpeg", "-r", str(dpi), str(path), str(prefix)],
            capture_output=True, timeout=PDFTOPPM_TIMEOUT_SECONDS, check=False,
        )
    except Exception:
        return False
    return result.returncode == 0 and Path(f"{prefix}.jpg").is_file()


def render_pdf_page(
    path: Path, page_no: int, *, temp_dir: Path, dpi: int = DEFAULT_DPI,
) -> Path | None:
    """把 PDF 第 ``page_no`` 页渲染为 JPEG 并返回图片路径；失败返回 ``None``。

    图片写入 ``temp_dir``，由调用方用 ``TemporaryDirectory`` 管理生命周期。
    """
    path = Path(path)
    if not path.is_file() or page_no < 1:
        return None
    temp_dir = Path(temp_dir)
    output = temp_dir / f"page-{page_no}.jpg"
    if _render_with_pdfium(path, page_no, output, dpi=dpi):
        return output
    prefix = temp_dir / f"page-{page_no}"
    if _render_with_pdftoppm(path, page_no, prefix, dpi=dpi):
        return Path(f"{prefix}.jpg")
    return None
