"""文档解析服务

处理上传的文档文件（PDF/Excel/图片/文本），提取文本内容。
用于从教师上传的试卷文件、成绩单等中提取原始文本。

U4-02 实现：支持 PDF、Excel、Word DOCX、图片（标记 OCR）、纯文本/CSV/Markdown。
旧版 Word .doc 仍需转换为 .docx 或 PDF 后上传。

U4 审查修复：使用进程级超时替代线程级超时，确保 CPU 密集型解析
（如恶意构造的超大 PDF）可以被真正终止，不会泄漏资源。
"""

from __future__ import annotations

import csv
import logging
import multiprocessing
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 进程级超时辅助函数（必须在模块顶层定义，multiprocessing 才能 pickle）
# ---------------------------------------------------------------------------

def _parse_in_subprocess(fmt: str, str_path: str) -> dict[str, Any]:
    """在子进程中执行解析。必须在模块顶层（可被 pickle）。"""
    parser = DocumentParser()
    handler = getattr(parser, f"_parse_{fmt}", None)
    if handler is None:
        return {"format": fmt, "content": "", "metadata": {}, "pages": [],
                "needs_ocr": False, "error": f"解析器未实现: {fmt}"}
    result = handler(Path(str_path))
    if "error" not in result:
        result["error"] = None
    if "metadata" not in result:
        result["metadata"] = {}
    if "pages" not in result:
        result["pages"] = []
    if "needs_ocr" not in result:
        result["needs_ocr"] = False
    return result


def _parse_worker(fmt: str, str_path: str, conn) -> None:
    """子进程工作函数（模块顶层，可被 pickle）。"""
    try:
        result = _parse_in_subprocess(fmt, str_path)
        conn.send(result)
    except Exception as exc:
        conn.send({
            "format": fmt,
            "content": "",
            "metadata": {},
            "pages": [],
            "needs_ocr": False,
            "error": str(exc),
        })
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# DocumentParser
# ---------------------------------------------------------------------------

class DocumentParser:
    """文档解析器。

    支持格式：
    - .pdf：PDF 文档（pdfplumber 提取文本 + 表格）
    - .xlsx / .xls：Excel 表格（openpyxl 读取）
    - .txt / .csv / .md：纯文本
    - .png / .jpg / .jpeg：图片（标记 needs_ocr，实际 OCR 由外部处理）
    - .docx：Word Open XML 文档（段落、表格、标题文本）
    - .doc：旧版二进制 Word 不支持，需转换为 DOCX 或 PDF

    超时机制：使用 multiprocessing.Process 实现进程级超时，
    超时后子进程被 terminate，确保不泄漏 CPU/内存。
    """

    SUPPORTED_FORMATS = {
        ".txt": "text",
        ".csv": "csv",
        ".md": "markdown",
        ".docx": "word",
        ".doc": "word_legacy",
        ".xlsx": "excel",
        ".xls": "excel_legacy",
        ".pdf": "pdf",
        ".png": "image",
        ".jpg": "image",
        ".jpeg": "image",
    }

    # U4-03 资源限制常量
    MAX_PDF_PAGES = 200
    MAX_EXCEL_ROWS = 10000
    MAX_EXCEL_COLS = 200
    MAX_WORD_PARAGRAPHS = 20000
    MAX_WORD_TABLE_ROWS = 10000
    MAX_WORD_CHARS = 2_000_000
    MAX_TEXT_BYTES = 10 * 1024 * 1024  # 10MB
    PARSE_TIMEOUT_SECONDS = 30.0

    def parse(self, file_path: str | Path, *, timeout_seconds: float | None = None) -> dict[str, Any]:
        """解析文档，提取文本内容。

        :param file_path: 文件路径
        :return: {
            "format": str,
            "content": str,        # 提取的文本内容
            "metadata": dict,      # 格式特定元数据
            "pages": list[dict],   # 分页内容（如有）
            "needs_ocr": bool,     # 图片类标记需要 OCR
            "error": str | None,   # 解析错误信息
        }
        """
        path = Path(file_path)
        if not path.is_file():
            return {"format": "unknown", "content": "", "metadata": {}, "pages": [],
                    "needs_ocr": False, "error": f"文件不存在: {path}"}

        ext = path.suffix.lower()
        if ext not in self.SUPPORTED_FORMATS:
            return {"format": "unknown", "content": "", "metadata": {}, "pages": [],
                    "needs_ocr": False, "error": f"不支持的格式: {ext}"}

        fmt = self.SUPPORTED_FORMATS[ext]
        timeout = self.PARSE_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds

        try:
            result = self._parse_with_process_timeout(fmt, str(path), timeout)
            return result
        except multiprocessing.TimeoutError:
            logger.warning("文档解析超时 (%.1fs): %s", timeout, path)
            return {
                "format": fmt,
                "content": "",
                "metadata": {"timeout_seconds": timeout},
                "pages": [],
                "needs_ocr": False,
                "error": "文档解析超时",
            }
        except Exception as exc:
            logger.exception("解析文档失败: %s", path)
            return {
                "format": fmt,
                "content": "",
                "metadata": {},
                "pages": [],
                "needs_ocr": False,
                "error": str(exc),
            }

    def _parse_with_process_timeout(
        self, fmt: str, str_path: str, timeout: float
    ) -> dict[str, Any]:
        """使用 multiprocessing 实现进程级超时。

        子进程超时后会被 terminate()，确保 CPU/内存不泄漏。
        结果通过 Pipe 传递。
        """
        context = multiprocessing.get_context("spawn")
        parent_conn, child_conn = context.Pipe(duplex=False)
        proc = context.Process(
            target=_parse_worker,
            args=(fmt, str_path, child_conn),
            daemon=True,
        )
        proc.start()
        child_conn.close()  # 父进程不需要写端

        try:
            if parent_conn.poll(timeout):
                result = parent_conn.recv()
                parent_conn.close()
                proc.join(timeout=2)
                if isinstance(result, dict):
                    return result
                return {"format": fmt, "content": "", "metadata": {},
                        "pages": [], "needs_ocr": False,
                        "error": f"解析返回意外类型: {type(result)}"}
            else:
                # 超时：终止子进程
                logger.warning("解析超时，终止子进程 pid=%s", proc.pid)
                proc.terminate()
                proc.join(timeout=3)
                if proc.is_alive():
                    proc.kill()
                    proc.join(timeout=1)
                parent_conn.close()
                raise multiprocessing.TimeoutError()
        except multiprocessing.TimeoutError:
            raise
        except Exception:
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=3)
            try:
                parent_conn.close()
            except Exception:
                pass
            raise

    # --- 文本类 ---

    def _parse_text(self, path: Path) -> dict[str, Any]:
        """解析纯文本文件，自动检测编码。"""
        content = self._read_text_auto(path)
        return {
            "format": "text",
            "content": content,
            "metadata": {"encoding": "utf-8", "char_count": len(content)},
        }

    def _parse_csv(self, path: Path) -> dict[str, Any]:
        """解析 CSV 文件，返回文本 + 行列元数据。"""
        content = self._read_text_auto(path)
        rows = list(csv.reader(content.splitlines()))
        return {
            "format": "csv",
            "content": content,
            "metadata": {
                "row_count": len(rows),
                "col_count": max(len(r) for r in rows) if rows else 0,
            },
        }

    def _parse_markdown(self, path: Path) -> dict[str, Any]:
        """解析 Markdown 文件，保留原始格式。"""
        content = self._read_text_auto(path)
        return {
            "format": "markdown",
            "content": content,
            "metadata": {"char_count": len(content)},
        }

    # --- PDF ---

    def _parse_pdf(self, path: Path) -> dict[str, Any]:
        """解析 PDF 文件，逐页提取文本和表格。"""
        try:
            import pdfplumber
        except ImportError:
            return {
                "format": "pdf",
                "content": "",
                "metadata": {},
                "error": "pdfplumber 未安装，无法解析 PDF",
            }

        pages: list[dict[str, Any]] = []
        all_text: list[str] = []
        ocr_pages: list[int] = []

        with pdfplumber.open(str(path)) as pdf:
            total_pages = len(pdf.pages)
            page_limit = min(total_pages, self.MAX_PDF_PAGES)

            for i in range(page_limit):
                page = pdf.pages[i]
                text = page.extract_text() or ""
                tables = page.extract_tables()
                # A scanned page can be a valid PDF with no text layer. Do not
                # report it as successfully parsed just because open() worked.
                if len(text.strip()) < 20 and page.images:
                    ocr_pages.append(i + 1)
                all_text.append(text)
                pages.append({
                    "page": i + 1,
                    "text": text,
                    "table_count": len(tables),
                    "tables": tables,
                    "needs_ocr": i + 1 in ocr_pages,
                })

            if total_pages > self.MAX_PDF_PAGES:
                logger.warning(
                    "PDF 有 %d 页，只解析前 %d 页", total_pages, self.MAX_PDF_PAGES,
                )

        return {
            "format": "pdf",
            "content": "\n\n--- Page Break ---\n\n".join(all_text),
            "metadata": {
                "total_pages": total_pages,
                "parsed_pages": page_limit,
                "truncated": total_pages > self.MAX_PDF_PAGES,
                "ocr_pages": ocr_pages,
            },
            "pages": pages,
            "needs_ocr": bool(ocr_pages),
        }

    # --- Excel ---

    def _parse_excel(self, path: Path) -> dict[str, Any]:
        """解析 Excel (.xlsx) 文件，提取所有工作表的文本。"""
        try:
            from openpyxl import load_workbook
        except ImportError:
            return {
                "format": "excel",
                "content": "",
                "metadata": {},
                "error": "openpyxl 未安装，无法解析 Excel",
            }

        wb = load_workbook(str(path), read_only=True, data_only=True)
        sheets: list[dict[str, Any]] = []
        all_text: list[str] = []

        for ws in wb.worksheets:
            rows_text: list[str] = []
            row_count = 0
            for row in ws.iter_rows(max_row=self.MAX_EXCEL_ROWS, max_col=self.MAX_EXCEL_COLS, values_only=True):
                cells = [str(c) if c is not None else "" for c in row]
                rows_text.append("\t".join(cells))
                row_count += 1
            sheet_text = "\n".join(rows_text)
            all_text.append(f"=== Sheet: {ws.title} ===\n{sheet_text}")
            sheets.append({
                "name": ws.title,
                "row_count": row_count,
                "col_count": ws.max_column or 0,
            })

        wb.close()

        return {
            "format": "excel",
            "content": "\n\n".join(all_text),
            "metadata": {
                "sheet_count": len(sheets),
                "sheets": sheets,
            },
        }

    def _parse_excel_legacy(self, path: Path) -> dict[str, Any]:
        """解析旧版 Excel (.xls) 文件——暂不支持。"""
        return {
            "format": "excel_legacy",
            "content": "",
            "metadata": {"path": str(path)},
            "error": ".xls 格式暂不支持，请转换为 .xlsx 后上传",
        }

    # --- Word ---

    def _parse_word(self, path: Path) -> dict[str, Any]:
        """解析 Word Open XML (.docx) 的段落和表格，不执行宏或外部关系。"""
        namespaces = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        try:
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
                required = {"[Content_Types].xml", "word/document.xml"}
                if not required.issubset(names):
                    raise ValueError("DOCX 包缺少必要的 Word 文档结构")
                document_xml = archive.read("word/document.xml")
        except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile, KeyError) as exc:
            return {
                "format": "word",
                "content": "",
                "metadata": {},
                "error": f"Word DOCX 文件损坏或不可读取: {exc}",
            }

        try:
            root = ElementTree.fromstring(document_xml)
        except ElementTree.ParseError as exc:
            return {
                "format": "word",
                "content": "",
                "metadata": {},
                "error": f"Word DOCX 正文 XML 损坏: {exc}",
            }

        body = root.find("w:body", namespaces)
        if body is None:
            return {
                "format": "word",
                "content": "",
                "metadata": {},
                "error": "Word DOCX 缺少正文内容",
            }

        def paragraph_text(paragraph) -> str:
            pieces: list[str] = []
            for node in paragraph.iter():
                local = node.tag.rsplit("}", 1)[-1]
                if local == "t" and node.text:
                    pieces.append(node.text)
                elif local == "tab":
                    pieces.append("\t")
                elif local in {"br", "cr"}:
                    pieces.append("\n")
            return "".join(pieces).strip()

        blocks: list[str] = []
        paragraph_count = 0
        table_count = 0
        table_row_count = 0
        truncated = False

        for child in body:
            local = child.tag.rsplit("}", 1)[-1]
            if local == "p":
                if paragraph_count >= self.MAX_WORD_PARAGRAPHS:
                    truncated = True
                    break
                paragraph_count += 1
                text = paragraph_text(child)
                if text:
                    blocks.append(text)
            elif local == "tbl":
                table_count += 1
                table_lines: list[str] = []
                for row in child.findall("w:tr", namespaces):
                    if table_row_count >= self.MAX_WORD_TABLE_ROWS:
                        truncated = True
                        break
                    table_row_count += 1
                    cells: list[str] = []
                    for cell in row.findall("w:tc", namespaces):
                        cell_parts = [paragraph_text(p) for p in cell.findall(".//w:p", namespaces)]
                        cells.append(" ".join(part for part in cell_parts if part))
                    table_lines.append("\t".join(cells))
                if table_lines:
                    blocks.append("[表格]\n" + "\n".join(table_lines))
                if truncated:
                    break

            if sum(len(block) for block in blocks) > self.MAX_WORD_CHARS:
                truncated = True
                break

        content = "\n\n".join(blocks)
        if len(content) > self.MAX_WORD_CHARS:
            content = content[: self.MAX_WORD_CHARS]
            truncated = True

        return {
            "format": "word",
            "content": content,
            "metadata": {
                "paragraph_count": paragraph_count,
                "table_count": table_count,
                "table_row_count": table_row_count,
                "char_count": len(content),
                "truncated": truncated,
            },
        }

    def _parse_word_legacy(self, path: Path) -> dict[str, Any]:
        """旧版二进制 Word (.doc) 需要转换为 DOCX 或 PDF 后解析。"""
        return {
            "format": "word_legacy",
            "content": "",
            "metadata": {"path": str(path)},
            "error": ".doc 格式暂不支持正文提取，请转换为 .docx 或 PDF 后上传",
        }

    # --- 图片 ---

    def _parse_image(self, path: Path) -> dict[str, Any]:
        """解析图片文件——返回元数据，实际 OCR 由外部处理。"""
        try:
            from PIL import Image
            img = Image.open(str(path))
            width, height = img.size
            fmt = img.format or "UNKNOWN"
            img.close()
        except Exception:
            width, height, fmt = 0, 0, "UNKNOWN"

        return {
            "format": "image",
            "content": "",
            "metadata": {
                "path": str(path),
                "width": width,
                "height": height,
                "image_format": fmt,
            },
            "needs_ocr": True,
        }

    # --- 辅助方法 ---

    def _read_text_auto(self, path: Path) -> str:
        """自动检测编码读取文本文件。"""
        raw = path.read_bytes()
        if len(raw) > self.MAX_TEXT_BYTES:
            raw = raw[: self.MAX_TEXT_BYTES]
            logger.warning("文本文件过大，截断到 %d 字节", self.MAX_TEXT_BYTES)

        for encoding in ("utf-8-sig", "utf-8", "gbk", "gb2312", "latin-1"):
            try:
                return raw.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                continue
        return raw.decode("utf-8", errors="replace")
