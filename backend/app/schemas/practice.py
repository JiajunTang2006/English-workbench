"""Explicit teacher inputs and bounded AI drafts for evidence-based practice."""
from datetime import date
from typing import Literal
from pydantic import Field, model_validator
from .teaching import StrictInput

Level = Literal["basic", "consolidation", "transfer", "progressive"]


class QuestionDraft(StrictInput):
    objective: str = Field(min_length=1, max_length=300)
    kind: Literal["choice", "number", "text"]
    prompt: str = Field(min_length=1, max_length=3000)
    passage: str = Field(default="", max_length=20000)
    source: dict = Field(default_factory=dict)
    options: dict[str, str] = Field(default_factory=dict, max_length=8)
    answers: list[str] = Field(min_length=1, max_length=10)
    explanation: str = Field(min_length=1, max_length=3000)
    hints: list[str] = Field(default_factory=list, max_length=3)
    difficulty: Literal["basic", "consolidation", "transfer"] = "basic"

    @model_validator(mode="after")
    def content_contract(self):
        if any(not str(v).strip() or len(str(v)) > 1000 for v in [*self.answers, *self.hints, *self.options.values()]):
            raise ValueError("答案、提示与选项需要非空且不超过 1000 字")
        if self.kind == "choice" and (len(self.options) < 2 or any(a not in self.options for a in self.answers)):
            raise ValueError("选择题至少有两个选项，答案须为有效选项键")
        if self.kind != "choice" and self.options:
            raise ValueError("非选择题不能携带选项")
        return self


class PracticeDraft(StrictInput):
    title: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=300)
    student_ids: list[int] = Field(min_length=1, max_length=100)
    level: Level = "progressive"
    questions: list[QuestionDraft] = Field(min_length=1, max_length=20)
    parent_id: int | None = Field(default=None, gt=0)


class PracticeGenerate(StrictInput):
    objective: str = Field(min_length=1, max_length=300)
    student_ids: list[int] = Field(min_length=1, max_length=100)
    level: Level = "progressive"
    question_count: int = Field(default=5, ge=1, le=20)
    teacher_note: str = Field(default="", max_length=1500)
    scheduled_for: date | None = None
    parent_id: int | None = Field(default=None, gt=0)
    session_id: int = Field(gt=0)
    confirmed_budget_yuan: float | None = Field(default=None, gt=0, le=10)


class PracticeConfirm(StrictInput):
    checked_answers: bool
    checked_scope: bool


class AttemptCreate(StrictInput):
    question_id: int = Field(gt=0)
    student_id: int = Field(gt=0)
    submission_key: str = Field(min_length=1, max_length=100)
    answer: str = Field(min_length=1, max_length=4000)
    observed_on: date
    hint_level: int = Field(default=0, ge=0, le=3)
    answer_viewed: bool = False


class AttemptBatch(StrictInput):
    rows: list[AttemptCreate] = Field(min_length=1, max_length=500)


class AttemptCorrection(StrictInput):
    expected_revision: int = Field(ge=1)
    correct: bool
    reason: str = Field(min_length=1, max_length=1000)


class FeedbackGenerate(StrictInput):
    session_id: int = Field(gt=0)
    confirmed_budget_yuan: float | None = Field(default=None, gt=0, le=10)


class PracticeReuse(StrictInput):
    title: str = Field(default="原错题专项练习", min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=300)
    student_ids: list[int] = Field(min_length=1, max_length=100)
    question_ids: list[int] = Field(min_length=1, max_length=20)
    level: Literal["basic", "consolidation", "transfer"] = "basic"
    session_id: int = Field(gt=0)
    confirmed_budget_yuan: float | None = Field(default=None, gt=0, le=10)
