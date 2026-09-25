from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


AttendanceStatus = Literal["present", "absent", "excused"]


class ScoreDimensionCreate(BaseModel):
    code: str = Field(min_length=1, max_length=50, pattern=r"^[a-zA-Z0-9_\-]+$")
    name: str = Field(min_length=1, max_length=100)
    max_score: float = Field(gt=0, le=1000)
    position: int = Field(default=0, ge=0, le=1000)


class ScoreDimensionRead(ScoreDimensionCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int


class ExamCreate(BaseModel):
    source_key: str | None = Field(default=None, min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=150)
    exam_date: date | None = None
    full_score: float = Field(default=100, gt=0, le=1000)
    exam_type: Literal["english_total", "question_type"] = "english_total"
    exam_kind: Literal["entrance", "regular"] = "regular"
    tier_a_cutoff: float | None = Field(default=None, ge=0)
    tier_b_cutoff: float | None = Field(default=None, ge=0)
    tier_c_cutoff: float | None = Field(default=None, ge=0)
    dimensions: list[ScoreDimensionCreate] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def validate_dimensions(self):
        codes = [item.code for item in self.dimensions]
        names = [item.name for item in self.dimensions]
        if len(codes) != len(set(codes)) or len(names) != len(set(names)):
            raise ValueError("题型代码和名称不能重复")
        if self.exam_type == "question_type" and not self.dimensions:
            raise ValueError("按题型考试至少需要一个题型")
        if sum(item.max_score for item in self.dimensions) > self.full_score + 1e-9:
            raise ValueError("题型满分之和不能超过考试满分")
        lines = (self.tier_a_cutoff, self.tier_b_cutoff, self.tier_c_cutoff)
        if any(item is not None and item > self.full_score for item in lines):
            raise ValueError("分层线不能超过考试满分")
        if all(item is not None for item in lines) and not (self.tier_a_cutoff >= self.tier_b_cutoff >= self.tier_c_cutoff):
            raise ValueError("A层线应不低于B层线，B层线应不低于C层线")
        return self


class ExamPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    exam_date: date | None = None
    full_score: float | None = Field(default=None, gt=0, le=1000)
    exam_kind: Literal["entrance", "regular"] | None = None
    tier_a_cutoff: float | None = Field(default=None, ge=0)
    tier_b_cutoff: float | None = Field(default=None, ge=0)
    tier_c_cutoff: float | None = Field(default=None, ge=0)


class ExamRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    source_key: str | None
    name: str
    exam_date: date | None
    full_score: float
    exam_type: str
    exam_kind: str
    tier_a_cutoff: float | None
    tier_b_cutoff: float | None
    tier_c_cutoff: float | None
    status: str
    dimensions: list[ScoreDimensionRead] = Field(default_factory=list)


class ExamWorkspaceMutation(BaseModel):
    expected_revision: int = Field(ge=1)
    source_key: str = Field(min_length=1, max_length=100)


class ExamWorkspaceMutationRead(BaseModel):
    exam: ExamRead | None
    state: dict
    revision: int


class DimensionScoreUpsert(BaseModel):
    dimension_id: int = Field(gt=0)
    score: float = Field(ge=0, le=1000)


class ExamScoreUpsert(BaseModel):
    student_id: int = Field(gt=0)
    total_score: float | None = Field(default=None, ge=0, le=1000)
    grade_rank: int | None = Field(default=None, gt=0)
    attendance_status: AttendanceStatus = "present"
    note: str | None = Field(default=None, max_length=500)
    dimension_scores: list[DimensionScoreUpsert] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def validate_attendance(self):
        if self.attendance_status == "present" and self.total_score is None:
            raise ValueError("实考学生必须填写总分")
        if self.attendance_status != "present" and (self.total_score is not None or self.grade_rank is not None or self.dimension_scores):
            raise ValueError("缺考或免考学生不能填写成绩")
        ids = [item.dimension_id for item in self.dimension_scores]
        if len(ids) != len(set(ids)):
            raise ValueError("同一题型不能重复提交")
        return self


class ExamScoresUpsert(BaseModel):
    items: list[ExamScoreUpsert] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_students(self):
        ids = [item.student_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("同一学生不能重复提交")
        return self


class DimensionScoreRead(BaseModel):
    dimension_id: int
    dimension_name: str
    score: float
    max_score: float


class ExamScoreRead(BaseModel):
    id: int
    student_id: int
    student_no: str
    student_name: str
    class_id: int
    class_name: str
    total_score: float | None
    class_rank: int | None = None
    grade_rank: int | None
    global_rank: int | None = None
    attendance_status: str
    note: str | None
    source_sync_run_id: int | None = None
    teacher_override: bool = False
    override_note: str | None = None
    rank: int | None
    class_rank: int | None
    tier: Literal["A", "B", "C", "D"] | None = None
    dimension_scores: list[DimensionScoreRead] = Field(default_factory=list)


class ExamSummary(BaseModel):
    exam_id: int
    exam_name: str
    full_score: float
    present_count: int
    absent_count: int
    average: float | None
    highest: float | None
    lowest: float | None
    # 已到场但没有有效总分的记录，单独暴露给分析前的数据质量提示。
    missing_score_count: int = 0


class StudentItemResultRead(BaseModel):
    id: int | None = None
    question_id: int
    question_no: str
    section_name: str | None = None
    question_type: str | None = None
    max_score: float
    score: float | None = None
    sub_question_no: str | None = None
    difficulty_level: str | None = None
    cognitive_level: str | None = None
    knowledge_nodes: list[str] = Field(default_factory=list)
    ability_nodes: list[str] = Field(default_factory=list)
    pitfall_tags: list[str] = Field(default_factory=list)
    score_rate: float | None = None
    correct: bool | None = None
    selected_option: str | None = None
    time_spent_ms: int | None = None
    modify_count: int | None = None
    hesitation_time_ms: int | None = None
    teaching_blocks: list[str] = Field(default_factory=list)
    student_answer_text: str | None = None
    attendance_status: str = "present"
    source_sync_run_id: int | None = None
    source_record_id: str | None = None
    source_cell: str | None = None
    import_confidence: float | None = None
    teacher_override: bool = False
    override_note: str | None = None


class StudentItemResultPatch(BaseModel):
    score: float = Field(ge=0, le=1000)
    override_note: str | None = Field(default=None, max_length=500)


class ExamClassMetricUpsert(BaseModel):
    grade_rank: int = Field(gt=0)


class ExamClassMetricRead(BaseModel):
    exam_id: int
    class_id: int
    grade_rank: int | None


class StudentExamPoint(BaseModel):
    exam_id: int
    exam_name: str
    exam_date: date | None
    full_score: float
    total_score: float | None
    score_rate: float | None
    attendance_status: str
    rank: int | None
    class_rank: int | None
    grade_rank: int | None
    class_name: str | None = None
    tier: Literal["A", "B", "C", "D"] | None = None


class StudentProfileRead(BaseModel):
    student: dict
    exams: list[StudentExamPoint]
    latest_score: StudentExamPoint | None
    learning_profile: dict = Field(default_factory=dict)
    learning_profile_meta: dict = Field(default_factory=dict)
    question_type_tracking: dict = Field(default_factory=dict)


class StudentProfileUpdateRequest(BaseModel):
    """教师直接编辑正式学生画像。"""
    patch: dict[str, Any] = Field(default_factory=dict)
    expected_version: int | None = Field(default=None, ge=0)
