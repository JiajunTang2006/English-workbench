"""统一学校成绩同步合同。

第三方 MCP、HTTP API 或文件适配器都应先转换为这些结构，再进入入库服务。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SyncTerm(BaseModel):
    external_id: str = Field(min_length=1, max_length=150)
    name: str = Field(min_length=1, max_length=100)
    code: str | None = Field(default=None, max_length=50)


class SyncClass(BaseModel):
    external_id: str = Field(min_length=1, max_length=150)
    name: str = Field(min_length=1, max_length=50)
    grade: str | None = Field(default=None, max_length=20)
    school_year: str | None = Field(default=None, max_length=20)


class SyncQuestion(BaseModel):
    external_id: str = Field(min_length=1, max_length=150)
    question_no: str = Field(min_length=1, max_length=20)
    section_name: str | None = Field(default=None, max_length=100)
    question_type: str | None = Field(default=None, max_length=50)
    content_text: str | None = None
    max_score: float = Field(gt=0, le=1000)
    correct_answer: str | int | float | list | dict | None = None
    sub_question_no: str | None = Field(default=None, max_length=20)
    difficulty_level: str | None = Field(default=None, max_length=50)
    cognitive_level: str | None = Field(default=None, max_length=50)
    knowledge_nodes: list[str] = Field(default_factory=list, max_length=100)
    ability_nodes: list[str] = Field(default_factory=list, max_length=100)
    pitfall_tags: list[str] = Field(default_factory=list, max_length=100)
    teaching_blocks: list[str] = Field(default_factory=list, max_length=100)


class SyncItemScore(BaseModel):
    question_external_id: str = Field(min_length=1, max_length=150)
    score: float | None = Field(default=None, ge=0, le=1000)
    student_answer: str | None = None
    score_rate: float | None = Field(default=None, ge=0, le=1)
    correct: bool | None = None
    selected_option: str | None = Field(default=None, max_length=100)
    time_spent_ms: int | None = Field(default=None, ge=0)
    modify_count: int | None = Field(default=None, ge=0)
    hesitation_time_ms: int | None = Field(default=None, ge=0)
    teaching_blocks: list[str] = Field(default_factory=list, max_length=100)
    pitfall_tags: list[str] = Field(default_factory=list, max_length=100)


class SyncStudent(BaseModel):
    external_id: str = Field(min_length=1, max_length=150)
    student_no: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=100)
    gender: str | None = Field(default=None, max_length=10)
    status: Literal["active", "inactive", "absent", "excused"] = "active"
    total_score: float | None = Field(default=None, ge=0, le=1000)
    class_rank: int | None = Field(default=None, gt=0)
    grade_rank: int | None = Field(default=None, gt=0)
    global_rank: int | None = Field(default=None, gt=0)
    item_scores: list[SyncItemScore] = Field(default_factory=list, max_length=1000)


class SyncRosterStudent(BaseModel):
    """只同步学生名册时使用的规范结构。"""
    external_id: str = Field(min_length=1, max_length=150)
    student_no: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=100)
    class_external_id: str = Field(min_length=1, max_length=150)
    gender: str | None = Field(default=None, max_length=10)
    status: Literal["active", "inactive", "absent", "excused"] = "active"


class StudentRosterPayload(BaseModel):
    """外部名册转换后的入库合同，不包含成绩或题目。"""
    source_key: str = Field(min_length=1, max_length=100)
    source_name: str = Field(min_length=1, max_length=150)
    snapshot_id: str = Field(min_length=1, max_length=150)
    term: SyncTerm
    classes: list[SyncClass] = Field(min_length=1, max_length=1000)
    students: list[SyncRosterStudent] = Field(min_length=1, max_length=50000)

    @model_validator(mode="after")
    def validate_references(self):
        class_ids = {item.external_id for item in self.classes}
        if len(class_ids) != len(self.classes):
            raise ValueError("班级 external_id 不能重复")
        student_ids = [item.external_id for item in self.students]
        if len(student_ids) != len(set(student_ids)):
            raise ValueError("学生 external_id 不能重复")
        unknown = {item.class_external_id for item in self.students} - class_ids
        if unknown:
            raise ValueError(f"学生引用了不存在的班级：{sorted(unknown)}")
        return self


class SyncExam(BaseModel):
    external_id: str = Field(min_length=1, max_length=150)
    name: str = Field(min_length=1, max_length=150)
    exam_date: date | None = None
    full_score: float = Field(gt=0, le=1000)
    paper_revision: str | None = Field(default=None, max_length=150)
    exam_kind: Literal["entrance", "regular"] = "regular"
    # 外部成绩源提供的 A/B/C/D 分层底线（D 层无需单独分数线）。
    # 允许部分为空：当某一层没有学生时，不凭空猜测该层阈值。
    tier_a_cutoff: float | None = Field(default=None, ge=0)
    tier_b_cutoff: float | None = Field(default=None, ge=0)
    tier_c_cutoff: float | None = Field(default=None, ge=0)


class SchoolSyncPayload(BaseModel):
    source_key: str = Field(default="mock-school", min_length=1, max_length=100)
    source_name: str = Field(default="模拟学校数据源", min_length=1, max_length=150)
    snapshot_id: str = Field(default="mock-snapshot-v1", min_length=1, max_length=150)
    term: SyncTerm
    class_info: SyncClass
    exam: SyncExam
    # 允许只有考试元数据和学生名单的同步结果；题目/成绩稍后补齐时
    # 仍可用同一 external_id + paper revision 追加完整试卷结构。
    questions: list[SyncQuestion] = Field(min_length=0, max_length=2000)
    students: list[SyncStudent] = Field(min_length=1, max_length=5000)

    @model_validator(mode="after")
    def validate_references(self):
        question_ids = [q.external_id for q in self.questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("题目 external_id 不能重复")
        student_ids = [s.external_id for s in self.students]
        if len(student_ids) != len(set(student_ids)):
            raise ValueError("学生 external_id 不能重复")
        known = set(question_ids)
        for student in self.students:
            unknown = {item.question_external_id for item in student.item_scores} - known
            if unknown:
                raise ValueError(f"学生 {student.student_no} 引用了不存在的题目: {sorted(unknown)}")
        return self


class SyncPreviewResponse(BaseModel):
    source_key: str
    snapshot_id: str
    counts: dict[str, int]
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class SyncApplyResponse(SyncPreviewResponse):
    run_id: int
    status: str


class SchoolDataSourceWrite(BaseModel):
    """自定义 MCP 数据源的写入体。

    ``config`` 承载端点、鉴权、路径模板与字段映射；令牌单独通过 ``bearer_token``
    传入并只写 keyvault，任何读取接口都不会回显原文。
    """

    name: str = Field(min_length=1, max_length=150)
    config: dict = Field(default_factory=dict)
    bearer_token: str | None = Field(default=None, max_length=4096)
    enabled: bool = True


class SchoolDataSourceCreate(SchoolDataSourceWrite):
    source_key: str = Field(min_length=2, max_length=64)


class SchoolDataSourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    source_key: str
    name: str
    kind: str
    config: dict = Field(default_factory=dict, validation_alias="config_json", serialization_alias="config")
    enabled: bool
    last_sync_at: datetime | None = None


class MoniMcpConfig(BaseModel):
    """与用户提供的 mcpServers JSON 结构保持一致。"""
    mcpServers: dict[str, dict]
