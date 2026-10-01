"""Bounded teacher inputs; no model can silently confirm learning outcomes."""
from datetime import date
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Phase = Literal["diagnose", "design", "implement", "review", "completed", "archived"]
Kind = Literal["lesson_flow", "student_handout", "teacher_key", "followup_assessment"]


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TeachingConstraints(StrictInput):
    lesson_minutes: int = Field(default=40, ge=5, le=240)
    no_homework: bool = True
    grade: str = Field(default="", max_length=100)
    curriculum: str = Field(default="", max_length=300)
    taught_content: str = Field(default="", max_length=1500)
    activity_preference: str = Field(default="", max_length=500)
    equipment: str = Field(default="", max_length=300)


class TaskCreate(StrictInput):
    term_id: int = Field(gt=0)
    class_id: int | None = Field(default=None, gt=0)
    exam_id: int | None = Field(default=None, gt=0)
    title: str = Field(min_length=1, max_length=200)
    goal: str = Field(min_length=1, max_length=2000)
    constraints: TeachingConstraints = Field(default_factory=TeachingConstraints)
    target_type: Literal["class", "individual", "group"] = "class"
    student_ids: list[int] = Field(default_factory=list, max_length=100)


class ReportTaskCreate(StrictInput):
    title: str = Field(min_length=1, max_length=200)
    goal: str = Field(min_length=1, max_length=2000)
    constraints: TeachingConstraints = Field(default_factory=TeachingConstraints)


class TaskUpdate(StrictInput):
    expected_revision: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    goal: str | None = Field(default=None, min_length=1, max_length=2000)
    phase: Phase | None = None
    constraints: TeachingConstraints | None = None
    target_type: Literal["class", "individual", "group"] | None = None
    student_ids: list[int] | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def reject_nulls(self):
        if any(getattr(self, key) is None for key in self.model_fields_set - {"expected_revision"}):
            raise ValueError("更新字段不能为空")
        return self


class ArtifactCreate(StrictInput):
    kind: Kind
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=20000)
    items: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def bounded_items(self):
        if any(len(item) > 2000 for item in self.items):
            raise ValueError("每个条目最多 2000 字")
        return self


class ArtifactUpdate(ArtifactCreate):
    expected_revision: int = Field(ge=1)


class ArtifactRestore(StrictInput):
    expected_revision: int = Field(ge=1)
    revision: int = Field(ge=1)


class AssessmentRow(StrictInput):
    student_id: int = Field(gt=0)
    objective: str = Field(min_length=1, max_length=300)
    correct: bool
    independent: bool = True
    new_question: bool = True
    observed_on: date
    note: str = Field(default="", max_length=500)


class FeedbackCreate(StrictInput):
    kind: Literal["implementation", "observation", "correction", "assessment"]
    note: str = Field(min_length=1, max_length=3000)
    observations: list[AssessmentRow] = Field(default_factory=list, max_length=500)
    finding_title: str = Field(default="", max_length=300)
    correction_reason: Literal["", "not_taught", "ambiguous_question", "time_limit", "other"] = ""
    source_run_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def assessment_contract(self):
        if self.kind == "assessment" and not self.observations:
            raise ValueError("复测记录至少需要一条作答")
        if self.kind != "assessment" and self.observations:
            raise ValueError("只有复测记录可提交作答")
        keys = [(r.student_id, r.objective, r.observed_on) for r in self.observations]
        if len(keys) != len(set(keys)):
            raise ValueError("同批复测中同一学生、目标、日期不能重复")
        return self
