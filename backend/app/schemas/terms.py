from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TermCreate(BaseModel):
    code: str = Field(min_length=1, max_length=50, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=100)
    starts_on: date | None = None
    ends_on: date | None = None
    clone_from_term_id: int | None = Field(default=None, gt=0)
    clone_classes: bool = True
    clone_enrollments: bool = True

    @model_validator(mode="after")
    def validate_dates(self):
        if self.starts_on and self.ends_on and self.starts_on > self.ends_on:
            raise ValueError("学期开始日期不能晚于结束日期")
        if self.clone_enrollments and not self.clone_classes:
            raise ValueError("复制学生名单时必须同时复制班级")
        return self


class TermPatch(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=50, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str | None = Field(default=None, min_length=1, max_length=100)
    starts_on: date | None = None
    ends_on: date | None = None


class TermDelete(BaseModel):
    """永久删除学期时必须再次提交学期代码，避免误操作。"""

    confirmation_code: str = Field(min_length=1, max_length=50)


class TermRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str
    starts_on: date | None
    ends_on: date | None
    status: str


class TermCacheClear(BaseModel):
    """清理学期 AI 缓存的显式确认。"""

    confirm: bool = False


class TermCacheSummary(BaseModel):
    """学期 AI 缓存数量与保护范围。"""

    term_id: int
    exam_paper_memories: int = 0
    sessions: int = 0
    messages: int = 0
    message_attachments: int = 0
    analysis_runs: int = 0
    analysis_evidence: int = 0
    analysis_events: int = 0
    llm_usage_records: int = 0
    student_profile_drafts: int = 0
    evaluation_drafts: int = 0
    error_cause_candidates: int = 0
    active_runs: int = 0
    static_knowledge_files_preserved: bool = True
    formal_attachments_preserved: bool = True
    teacher_confirmed_profiles_and_evaluations_preserved: bool = True


class CurrentTermWrite(BaseModel):
    term_id: int = Field(gt=0)
