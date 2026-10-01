"""结构化输出 Pydantic 模型

定义 Agent 最终输出的强制结构。
所有 finding 必须有 evidence_ids，所有 recommendation 必须有 supports。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class Finding(BaseModel):
    """单条分析发现。"""
    title: str = Field(..., min_length=1, description="发现标题")
    description: str = Field("", description="详细描述")
    evidence_ids: list[str] = Field(..., min_length=1, description="支撑证据 ID 列表")
    severity: str = Field("info", description="严重程度: info/warning/critical")

    @field_validator("evidence_ids")
    @classmethod
    def evidence_ids_not_empty(cls, v: list[str]) -> list[str]:
        if not v or len(v) == 0:
            raise ValueError("evidence_ids 不能为空，每条发现必须有证据支撑")
        return v


class Recommendation(BaseModel):
    """单条教学建议。"""
    action: str = Field(..., min_length=1, description="建议行动")
    rationale: str = Field("", description="建议理由")
    supports: list[str] = Field(..., min_length=1, description="支撑证据 ID 列表")
    priority: str = Field("medium", description="优先级: low/medium/high")

    @field_validator("supports")
    @classmethod
    def supports_not_empty(cls, v: list[str]) -> list[str]:
        if not v or len(v) == 0:
            raise ValueError("supports 不能为空，每条建议必须有证据支撑")
        return v


class StructuredSection(BaseModel):
    """报告中的可交付内容分节，例如课堂流程、学生练习和教师答案。"""
    kind: Literal["lesson_flow", "student_handout", "teacher_key", "followup_assessment"] = Field(
        ..., description="材料用途，用于安全区分学生版与教师版内容"
    )
    title: str = Field(..., min_length=1, description="分节标题")
    body: str = Field("", description="分节说明")
    items: list[str] = Field(default_factory=list, description="分节条目")


class StructuredAnswer(BaseModel):
    """Agent 最终输出的强制结构。

    U3-03: 报告至少包含 answer_type、summary、findings[]（每项必须有 evidence_ids）、
    recommendations[]（每项必须有 supports）、limitations[]、scope_snapshot、schema_version。
    student_diagnosis 额外使用 profile_summary 保存可成长的教师可读画像段落。
    """
    answer_type: str = Field(..., min_length=1, description="答案类型，对应 capability 名称")
    summary: str = Field(..., min_length=1, description="分析摘要")
    findings: list[Finding] = Field(default_factory=list, description="分析发现列表")
    recommendations: list[Recommendation] = Field(default_factory=list, description="教学建议列表")
    profile_summary: str | None = Field(
        default=None,
        description="学生画像摘要：结合既有画像和本次证据重写的教师可读自然语言段落",
    )
    timeline: str | None = Field(default=None, description="分阶段复习安排")
    sections: list[StructuredSection] = Field(default_factory=list, description="可复用的教学材料分节")
    limitations: list[str] = Field(default_factory=list, description="分析局限性")
    scope_snapshot: dict[str, Any] = Field(
        default_factory=dict,
        description="分析范围快照（由服务器注入，模型应原样回传）",
    )
    schema_version: str = Field(
        default="1.0.0",
        description="报告 Schema 版本",
    )

    @field_validator("summary")
    @classmethod
    def summary_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("summary 不能为空")
        return v
