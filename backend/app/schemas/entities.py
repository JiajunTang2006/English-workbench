from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ClassCreate(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    grade: str | None = Field(default=None, max_length=20)
    school_year: str | None = Field(default=None, max_length=20)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("班级名称不能为空")
        return value


class ClassPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=50)
    grade: str | None = Field(default=None, max_length=20)
    school_year: str | None = Field(default=None, max_length=20)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("班级名称不能为空")
        return value


class ClassRead(ClassCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    status: str


class StudentCreate(BaseModel):
    student_no: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=100)
    class_id: int = Field(gt=0)
    gender: str | None = Field(default=None, max_length=10)
    entrance_english: float | None = Field(default=None, ge=0)
    target_score: float | None = Field(default=None, ge=0)
    weak_tags: str | None = Field(default=None, max_length=500)
    parent_phone: str | None = Field(default=None, max_length=30)
    seat: str | None = Field(default=None, max_length=30)

    @field_validator("student_no", "name", mode="before")
    @classmethod
    def strip_required(cls, value: str) -> str:
        value = str(value).strip()
        if not value:
            raise ValueError("字段不能为空")
        return value


class StudentPatch(BaseModel):
    student_no: str | None = Field(default=None, min_length=1, max_length=50)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    class_id: int | None = Field(default=None, gt=0)
    gender: str | None = Field(default=None, max_length=10)
    entrance_english: float | None = Field(default=None, ge=0)
    target_score: float | None = Field(default=None, ge=0)
    weak_tags: str | None = Field(default=None, max_length=500)
    parent_phone: str | None = Field(default=None, max_length=30)
    seat: str | None = Field(default=None, max_length=30)

    @field_validator("student_no", "name")
    @classmethod
    def normalize_required(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("字段不能为空")
        return value


class StudentRead(StudentCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    status: str


class SettingsPatch(BaseModel):
    teacher_name: str | None = Field(default=None, max_length=100)
    subject: str | None = Field(default=None, max_length=50)
    user_address: str | None = Field(default=None, max_length=50)
    tone: str | None = Field(default=None, max_length=20)
    custom_prompt: str | None = Field(default=None, max_length=1200)
    excellent_line: float | None = Field(default=None, ge=0, le=100)
    passing_line: float | None = Field(default=None, ge=0, le=100)
    critical_line: float | None = Field(default=None, ge=0)

    @field_validator("teacher_name", "subject", "user_address", "custom_prompt")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value

    @field_validator("tone")
    @classmethod
    def validate_tone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().lower()
        if value not in {"rigorous", "friendly", "custom"}:
            raise ValueError("回答风格必须是 rigorous、friendly 或 custom")
        return value


class SettingsRead(BaseModel):
    teacher_name: str = ""
    subject: str = "英语"
    user_address: str = "老师"
    tone: str = "rigorous"
    custom_prompt: str = ""
    excellent_line: float = 90
    passing_line: float = 60
    critical_line: float = 55

    @model_validator(mode="after")
    def validate_thresholds(self):
        if self.excellent_line <= self.passing_line:
            raise ValueError("优秀线必须高于及格线")
        if self.tone not in {"rigorous", "friendly", "custom"}:
            raise ValueError("回答风格必须是 rigorous、friendly 或 custom")
        return self
