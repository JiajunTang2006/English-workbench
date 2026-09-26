from __future__ import annotations

import pytest
from PIL import Image
from sqlalchemy import select

from backend.app.config import Settings
from backend.app.factory import create_app
from backend.app.models import ExamPaperVersion, ExamQuestion, Term
from backend.app.services.exam_ingestion import ExamIngestionService
from backend.app.services.vision.provider import ProviderCapabilities, VisionResult


class FakeVisionProvider:
    capabilities = ProviderCapabilities(name="fake-vision", supports_image_input=True)

    async def analyze(self, *, images, task, response_schema):
        return VisionResult(
            provider="fake-vision", model_name="fake-v1", request_id="req-1",
            pages=[{"page_no": 1, "text": "试卷", "questions": [
                {"question_no": "1", "question_type": "选择题", "text": "Choose A", "max_score": 2, "options": {"A": "yes"}, "confidence": 0.96},
                {"question_no": "2", "question_type": "填空题", "text": "Fill", "max_score": 3, "confidence": 0.88},
            ]}],
        )


@pytest.fixture()
def app(tmp_path):
    return create_app(Settings(data_dir=tmp_path))


def test_image_ingestion_creates_draft_then_confirmation(app, tmp_path):
    session = app.state.session_factory()
    term = session.scalar(select(Term).where(Term.status == "active"))
    image_path = tmp_path / "paper.png"
    Image.new("RGB", (32, 32), "white").save(image_path)
    service = ExamIngestionService(FakeVisionProvider(), session, tmp_path)

    draft = service.ingest_from_images([image_path], "期中考试", term_id=term.id)
    assert draft["ok"] is True
    assert draft["status"] == "draft"
    assert len(draft["questions"]) == 2
    paper = session.get(ExamPaperVersion, draft["paper_version_id"])
    assert paper.status == "draft"
    assert session.scalar(select(ExamQuestion).where(ExamQuestion.paper_version_id == paper.id)).question_no == "1"

    confirmed = service.review_and_correct(draft["exam_id"], {"questions": [
        {"question_no": "1", "text": "Corrected", "max_score": 2},
        {"question_no": "2", "text": "Fill", "max_score": 3},
    ]})
    assert confirmed["ok"] is True
    assert confirmed["status"] == "confirmed"
    session.refresh(paper)
    assert paper.status == "confirmed"
    assert paper.questions[0].content_text == "Corrected"


def test_image_ingestion_rejects_missing_file(app, tmp_path):
    service = ExamIngestionService(FakeVisionProvider(), app.state.session_factory(), tmp_path)
    result = service.ingest_from_images([tmp_path / "missing.png"], "考试")
    assert result == {"ok": False, "error": "image_file_not_found"}
