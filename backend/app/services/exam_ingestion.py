"""试卷图片/PDF 录入服务。

视觉 Provider 只负责识别，服务层负责校验、版本化和教师确认。草稿不会
影响考试统计；只有 ``review_and_correct`` 确认后才成为可供分析使用的
``ExamPaperVersion``。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from .vision.provider import ImageInput, VisionResult
from .vision.registry import get_provider
from ..models import Exam, ExamPaperVersion, ExamQuestion, Term

logger = logging.getLogger(__name__)


class ExamIngestionService:
    """试卷录入服务。

    视觉 Provider 负责识别，服务层负责将题目结构化为草稿版本；
    教师通过 ``review_and_correct`` 确认后，版本才会进入正式分析链路。
    """

    def __init__(self, vision_provider=None, db_session=None, data_dir: str | Path | None = None) -> None:
        self._vision_provider = vision_provider or get_provider()
        self._db = db_session
        self._data_dir = Path(data_dir) if data_dir else None

    def ingest_from_images(
        self,
        image_paths: list[str | Path],
        exam_title: str,
        subject: str = "英语",
        term_id: int | None = None,
        exam_id: int | None = None,
        source_attachment_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        """识别图片并创建可审核的试卷草稿。"""
        paths = [Path(path) for path in image_paths]
        if not paths or any(not path.is_file() for path in paths):
            return {"ok": False, "error": "image_file_not_found"}
        if self._vision_provider is None:
            return {"ok": False, "error": "vision_provider_not_configured"}
        try:
            if hasattr(self._vision_provider, "analyze"):
                result = self._run(self._vision_provider.analyze(
                    images=[ImageInput(path=str(path), page_no=index + 1) for index, path in enumerate(paths)],
                    task="exam_understanding", response_schema=self._schema(),
                ))
            elif hasattr(self._vision_provider, "analyze_images"):
                legacy = self._run(self._vision_provider.analyze_images(
                    [str(path) for path in paths],
                    "请识别试卷题目，只输出 JSON：{\"questions\":[{\"question_no\":\"1\",\"content_text\":\"\",\"max_score\":1}]}。",
                    max_tokens=4096,
                ))
                result = self._legacy_result(legacy)
            else:
                result = None
            if result is None:
                return {"ok": False, "error": "vision_provider_contract_invalid"}
            if result.error:
                return {"ok": False, "error": result.error, "request_id": result.request_id}
            questions = self._extract_questions(result)
            if not questions:
                return {"ok": False, "error": "no_questions_detected", "pages": result.pages, "request_id": result.request_id}
            return self._persist_draft(exam_title, subject, questions, term_id=term_id, exam_id=exam_id, source_attachment_ids=source_attachment_ids, provider=result.provider, model=result.model_name)
        except Exception as exc:
            logger.warning("试卷图片录入失败: %s", type(exc).__name__)
            return {"ok": False, "error": f"exam_ingestion_failed:{type(exc).__name__}"}

    def ingest_from_pdf(
        self,
        pdf_path: str | Path,
        exam_title: str,
        subject: str = "英语",
        term_id: int | None = None,
        exam_id: int | None = None,
        source_attachment_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        """将 PDF 页面渲染成受控 JPEG 后复用图片录入流程。"""
        path = Path(pdf_path)
        if not path.is_file() or path.suffix.lower() != ".pdf":
            return {"ok": False, "error": "pdf_file_not_found_or_invalid"}
        converter = shutil.which("pdftoppm")
        if converter is None:
            return {"ok": False, "error": "pdf_renderer_not_available"}
        with tempfile.TemporaryDirectory(prefix="exam-ingest-") as tmp:
            prefix = str(Path(tmp) / "page")
            try:
                proc = subprocess.run([converter, "-jpeg", "-r", "150", "-f", "1", "-l", "20", str(path), prefix], capture_output=True, timeout=90)
            except subprocess.TimeoutExpired:
                return {"ok": False, "error": "pdf_render_timeout"}
            if proc.returncode != 0:
                return {"ok": False, "error": "pdf_render_failed"}
            pages = sorted(Path(tmp).glob("page-*.jpg"))
            if not pages:
                return {"ok": False, "error": "pdf_has_no_rendered_pages"}
            return self.ingest_from_images(pages, exam_title, subject, term_id=term_id, exam_id=exam_id, source_attachment_ids=source_attachment_ids)

    def review_and_correct(
        self,
        exam_id: int,
        corrections: dict[str, Any],
    ) -> dict[str, Any]:
        """更新草稿题目并确认最新试卷版本。"""
        if self._db is None:
            return {"ok": False, "error": "database_not_configured"}
        paper = self._db.scalar(select(ExamPaperVersion).where(ExamPaperVersion.exam_id == exam_id, ExamPaperVersion.status == "draft").order_by(ExamPaperVersion.version.desc()).limit(1))
        if paper is None:
            return {"ok": False, "error": "draft_not_found"}
        incoming = corrections.get("questions") if isinstance(corrections, dict) else None
        if incoming is not None:
            if not isinstance(incoming, list) or not incoming:
                return {"ok": False, "error": "questions_must_be_non_empty_list"}
            for question in list(paper.questions):
                self._db.delete(question)
            self._db.flush()
            for item in incoming:
                self._add_question(paper, item)
        paper.status = "confirmed"
        paper.confirmed_at = _now()
        for older in self._db.scalars(select(ExamPaperVersion).where(ExamPaperVersion.exam_id == exam_id, ExamPaperVersion.id != paper.id, ExamPaperVersion.status == "confirmed")):
            older.status = "superseded"
        self._db.commit()
        return self._paper_payload(paper)

    @staticmethod
    def _schema() -> dict[str, Any]:
        question_schema = {
            "type": "object",
            "required": ["question_no", "max_score"],
            "properties": {
                "question_no": {"type": ["string", "integer"]},
                "sub_question_no": {"type": ["string", "integer"]},
                "section_name": {"type": "string"},
                "question_type": {
                    "type": "string",
                    "description": "choice/fill_blank/short_answer/essay/reading",
                },
                "content_text": {"type": "string"},
                "options": {"type": "object"},
                "correct_answer": {},
                "max_score": {"type": "number"},
                "knowledge_points": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "按题目内容标注的知识点名称，一道题可多个",
                },
                "confidence": {"type": "number"},
                "page_no": {"type": "integer"},
            },
        }
        return {"type": "object", "required": ["questions"],
                "properties": {"questions": {"type": "array", "items": question_schema}}}

    @staticmethod
    def _run(awaitable):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(awaitable)
        raise RuntimeError("exam_ingestion_sync_called_inside_event_loop")

    @staticmethod
    def _extract_questions(result: VisionResult) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for page in result.pages or []:
            if isinstance(page, dict) and isinstance(page.get("questions"), list):
                found.extend(item for item in page["questions"] if isinstance(item, dict))
        return found

    @staticmethod
    def _legacy_result(response) -> VisionResult:
        raw = getattr(response, "content", "") or ""
        try:
            value = json.loads(raw.strip().strip("`"))
        except (json.JSONDecodeError, TypeError):
            value = {"questions": []}
        return VisionResult(provider="legacy_vision", model_name=None,
                            request_id=getattr(getattr(response, "usage", None), "provider_request_id", None),
                            pages=[{"page_no": 1, "text": raw, "questions": value.get("questions", []) if isinstance(value, dict) else []}])

    def _persist_draft(self, title, subject, questions, *, term_id, exam_id, source_attachment_ids, provider, model):
        if self._db is None:
            return {"ok": False, "error": "database_not_configured"}
        if exam_id is not None:
            exam = self._db.get(Exam, exam_id)
            if exam is not None and term_id is not None and exam.term_id != term_id:
                return {"ok": False, "error": "exam_term_mismatch"}
        else:
            statement = select(Exam).where(Exam.name == title)
            if term_id is not None:
                statement = statement.where(Exam.term_id == term_id)
            exam = self._db.scalar(statement.order_by(Exam.id.desc()).limit(1))
        if exam is None:
            if term_id is None:
                term = self._db.scalar(select(Term).where(Term.status == "active").order_by(Term.id.desc()).limit(1))
            else:
                term = self._db.get(Term, term_id)
            if term is None:
                return {"ok": False, "error": "term_not_found"}
            exam = Exam(term_id=term.id, name=title, source_key=f"ingest:{hashlib.sha1(title.encode()).hexdigest()[:12]}", full_score=sum(float(q.get("max_score", 1)) for q in questions), exam_type="english_total")
            self._db.add(exam)
            self._db.flush()
        max_version = self._db.scalar(select(func.max(ExamPaperVersion.version)).where(ExamPaperVersion.exam_id == exam.id)) or 0
        paper = ExamPaperVersion(exam_id=exam.id, version=max_version + 1, status="draft", source_attachment_ids_json=source_attachment_ids or [], full_score=exam.full_score, extraction_provider=provider, extraction_model=model, structure_hash=hashlib.sha256(json.dumps(questions, ensure_ascii=False, sort_keys=True).encode()).hexdigest())
        self._db.add(paper)
        self._db.flush()
        for item in questions:
            self._add_question(paper, item)
        self._db.commit()
        return self._paper_payload(paper)

    @staticmethod
    def _add_question(paper, item: dict[str, Any]):
        number = str(item.get("question_no") or item.get("number") or "").strip()
        if not number:
            raise ValueError("question_no_required")
        max_score = float(item.get("max_score") or item.get("score") or 1)
        if max_score <= 0:
            raise ValueError("max_score_must_be_positive")
        knowledge_nodes = []
        for node in (item.get("knowledge_points") or item.get("knowledge_nodes") or []):
            text = str(node).strip()
            if text and text not in knowledge_nodes:
                knowledge_nodes.append(text[:100])
        paper.questions.append(ExamQuestion(
            question_no=number, sub_question_no=str(item.get("sub_question_no")) if item.get("sub_question_no") is not None else None,
            external_id=item.get("external_id") or f"ingest:{paper.id}:{number}", section_name=item.get("section_name"), question_type=item.get("question_type"),
            content_text=item.get("content_text") or item.get("text"), options_json=item.get("options") or {}, max_score=max_score,
            correct_answer_json={"value": item["correct_answer"]} if item.get("correct_answer") is not None else {}, extraction_confidence=item.get("confidence"), source_page=item.get("page_no"),
            knowledge_nodes_json=knowledge_nodes,
        ))

    @staticmethod
    def _paper_payload(paper):
        return {"ok": True, "exam_id": paper.exam_id, "paper_version_id": paper.id, "version": paper.version, "status": paper.status, "questions": [{"id": q.id, "question_no": q.question_no, "sub_question_no": q.sub_question_no, "section_name": q.section_name, "question_type": q.question_type, "content_text": q.content_text, "options": q.options_json or {}, "max_score": q.max_score, "knowledge_points": q.knowledge_nodes_json or [], "source_page": q.source_page, "confidence": q.extraction_confidence} for q in paper.questions]}


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)
