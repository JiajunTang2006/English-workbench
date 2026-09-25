"""L4 文档 Provider 抽象（方案 §11.4）。

本地优先，远端可选。当前实现：
- LocalHTMLProvider：零依赖生成自包含 HTML（PDF 的打印源，也是兜底产物）；
- LocalDocxProvider：零依赖手写 OOXML 生成 .docx；
- WeasyPdfProvider：探测系统 weasyprint（macOS 自带 CLI 或 Python 模块）转 PDF，
  不可用时降级为「仅 HTML」并在返回中标记 format_hint。

远端 Provider（L4-C）按同一 Protocol 接入，本文件预留接口，不在本轮实现。
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable


@dataclass
class ExportHandle:
    """一次导出的本地句柄（落盘路径 + 实际产物格式）。"""

    artifact_path: Path
    mime_type: str
    produced_format: Literal["pdf", "docx", "html"]
    format_hint: str = ""  # 例如 "html_print" 表示本应 PDF 但回退 HTML


@dataclass
class ExportResult:
    handle: ExportHandle
    size_bytes: int
    sha256: str


@runtime_checkable
class DocumentProvider(Protocol):
    async def submit(
        self,
        *,
        spec: dict,
        title: str,
        answer: dict,
        evidence: list[dict],
        meta: dict[str, str],
        target_dir: Path,
        idempotency_key: str,
        privacy_level: str = "confidential",
    ) -> ExportResult: ...


def _weasyprint_available() -> bool:
    """探测系统是否可用 weasyprint（CLI 优先，其次 Python 模块）。"""
    if shutil.which("weasyprint"):
        return True
    try:
        import importlib.util
        return importlib.util.find_spec("weasyprint") is not None
    except Exception:
        return False


class LocalHTMLProvider:
    """零依赖生成自包含 HTML 报告（始终可用，作为 PDF 兜底）。"""

    produced_format = "html"
    mime_type = "text/html"

    async def submit(self, *, spec, title, answer, evidence, meta, target_dir,
                     idempotency_key, privacy_level="confidential") -> ExportResult:
        from .html_report import build_html_report

        html_text = build_html_report(
            title=title, answer=answer, evidence=evidence, meta=meta,
            privacy_level=privacy_level,
        )
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{idempotency_key}.html"
        path.write_text(html_text, encoding="utf-8")
        import hashlib
        digest = hashlib.sha256(html_text.encode("utf-8")).hexdigest()
        return ExportResult(
            handle=ExportHandle(
                artifact_path=path, mime_type=self.mime_type,
                produced_format="html", format_hint="html_print",
            ),
            size_bytes=path.stat().st_size,
            sha256=digest,
        )


class LocalDocxProvider:
    """零依赖手写 OOXML 生成 .docx。"""

    produced_format = "docx"
    mime_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    async def submit(self, *, spec, title, answer, evidence, meta, target_dir,
                     idempotency_key, privacy_level="confidential") -> ExportResult:
        from .docx_writer import build_docx_bytes

        data = build_docx_bytes(
            title=title, answer=answer, evidence=evidence, meta=meta,
            privacy_level=privacy_level,
        )
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{idempotency_key}.docx"
        path.write_bytes(data)
        import hashlib
        digest = hashlib.sha256(data).hexdigest()
        return ExportResult(
            handle=ExportHandle(
                artifact_path=path, mime_type=self.mime_type,
                produced_format="docx",
            ),
            size_bytes=len(data),
            sha256=digest,
        )


class WeasyPdfProvider:
    """探测系统 weasyprint 把 HTML 转 PDF；不可用则降级返回 HTML 产物。"""

    produced_format = "pdf"
    mime_type = "application/pdf"

    def __init__(self) -> None:
        self._available = _weasyprint_available()

    async def submit(self, *, spec, title, answer, evidence, meta, target_dir,
                     idempotency_key, privacy_level="confidential") -> ExportResult:
        from .html_report import build_html_report
        import hashlib

        html_text = build_html_report(
            title=title, answer=answer, evidence=evidence, meta=meta,
            privacy_level=privacy_level,
        )
        target_dir.mkdir(parents=True, exist_ok=True)
        html_path = target_dir / f"{idempotency_key}.html"
        html_path.write_text(html_text, encoding="utf-8")

        if not self._available:
            # 降级：仅 HTML（浏览器打印为 PDF 的用户路径）
            digest = hashlib.sha256(html_text.encode("utf-8")).hexdigest()
            return ExportResult(
                handle=ExportHandle(
                    artifact_path=html_path, mime_type="text/html",
                    produced_format="html", format_hint="html_print_no_weasyprint",
                ),
                size_bytes=html_path.stat().st_size,
                sha256=digest,
            )

        pdf_path = target_dir / f"{idempotency_key}.pdf"
        ok = self._render(html_text, pdf_path)
        if not ok:
            digest = hashlib.sha256(html_text.encode("utf-8")).hexdigest()
            return ExportResult(
                handle=ExportHandle(
                    artifact_path=html_path, mime_type="text/html",
                    produced_format="html", format_hint="html_print_render_failed",
                ),
                size_bytes=html_path.stat().st_size,
                sha256=digest,
            )
        data = pdf_path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        return ExportResult(
            handle=ExportHandle(
                artifact_path=pdf_path, mime_type=self.mime_type,
                produced_format="pdf",
            ),
            size_bytes=len(data),
            sha256=digest,
        )

    def _render(self, html_text: str, pdf_path: Path) -> bool:
        try:
            cli = shutil.which("weasyprint")
            if cli:
                subprocess.run(
                    [cli, "-,", str(pdf_path)], input=html_text.encode("utf-8"),
                    check=True, capture_output=True, timeout=120,
                )
                return pdf_path.exists() and pdf_path.stat().st_size > 0
            import weasyprint  # type: ignore
            weasyprint.HTML(string=html_text).write_pdf(str(pdf_path))
            return pdf_path.exists() and pdf_path.stat().st_size > 0
        except Exception:
            return False


def get_provider(kind: str) -> DocumentProvider:
    """按格式选择本地 Provider（方案 §11.6 fail-closed：远端需显式启用）。"""
    if kind == "pdf":
        return WeasyPdfProvider()
    if kind == "docx":
        return LocalDocxProvider()
    # 默认 HTML 兜底
    return LocalHTMLProvider()
