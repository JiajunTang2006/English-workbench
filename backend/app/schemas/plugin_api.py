"""TeachMate Codex 插件 API（TeachMatePluginAPI v1）稳定 DTO。

设计约束（见 docs/TEACHMATE_LONG_TERM_DEVELOPMENT_PLAN.md §9）：
- 只暴露确定性教学事实与已确认资料，绝不暴露 ORM 对象、绝对路径、API Key、
  Token 摘要、原始哈希或任何未确认附件正文。
- 所有 schema 为插件契约的稳定形状；字段变更需 bump api_version。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

PLUGIN_API_VERSION = "1.0"


# --- 状态与能力 ---
class PluginStatus(BaseModel):
    api_version: str = PLUGIN_API_VERSION
    service: str = "english-workbench"
    app_version: str
    schema_revision: str
    health: str = "ok"
    plugin_enabled: bool
    capabilities: list[str] = Field(default_factory=list)


class PluginTokenIssued(BaseModel):
    token: str
    scope: str
    issued_at: datetime
    expires_at: datetime | None = None


class PluginTokenMeta(BaseModel):
    id: str
    scope: str
    issued_at: datetime
    last_used_at: datetime | None = None
    revoked: bool = False


class PluginConnectConfig(BaseModel):
    """TeachMate 页面一键生成的 WorkBuddy 本地 MCP 配置。"""

    token: str
    scope: str
    config: dict[str, Any]
    issued_at: datetime
    expires_at: datetime | None = None


# --- 教学作用域 ---
class TermLite(BaseModel):
    id: int
    name: str
    status: str


class ClassLite(BaseModel):
    id: int
    name: str
    status: str


class ExamLite(BaseModel):
    id: int
    name: str
    exam_date: str | None = None
    full_score: float
    exam_type: str
    status: str


class TeachingScopes(BaseModel):
    term_id: int
    terms: list[TermLite] = Field(default_factory=list)
    classes: list[ClassLite] = Field(default_factory=list)
    exams: list[ExamLite] = Field(default_factory=list)


# --- 考试快照 ---
class ExamSnapshotSummary(BaseModel):
    exam_id: int
    exam_name: str
    full_score: float
    present_count: int
    absent_count: int
    average: float | None = None
    highest: float | None = None
    lowest: float | None = None


class ExamSnapshotClassMetric(BaseModel):
    class_id: int
    class_name: str
    grade_rank: int | None = None


class ExamSnapshot(BaseModel):
    term_id: int
    class_id: int | None = None
    class_name: str | None = None
    exam: ExamSnapshotSummary
    class_metrics: list[ExamSnapshotClassMetric] = Field(default_factory=list)
    data_quality: dict = Field(default_factory=dict)


# --- 分析报告（供 WorkBuddy 等外部 Agent 读取） ---
class AnalysisReportScope(BaseModel):
    """报告实际使用的数据范围。ID 与名称同时返回，避免调用方靠标题猜范围。"""

    term_id: int
    term_name: str
    class_id: int | None = None
    class_name: str | None = None
    exam_id: int
    exam_name: str
    full_score: float


class AnalysisReportRun(BaseModel):
    """报告对应的分析运行元数据，不暴露 prompt、工具输出或成本明细。"""

    run_id: int
    capability: str
    status: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    runtime_kind: str | None = None
    runtime_version: str | None = None
    rules_version: str | None = None
    prompt_version: str | None = None


class AnalysisReportView(BaseModel):
    """WorkBuddy 生成 PDF/PPT 时使用的稳定、只读报告数据。"""

    api_version: str = PLUGIN_API_VERSION
    schema_version: str = "1.1.0"
    scope: AnalysisReportScope
    run: AnalysisReportRun
    report: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    data_quality: dict[str, Any] = Field(default_factory=dict)
    scope_snapshot: dict[str, Any] = Field(default_factory=dict)
    snapshot_source: str = "frozen"
    identifiable: bool = False


class AnalysisReportListItem(BaseModel):
    """报告目录项；只返回定位和状态信息，不在目录中展开学生相关正文。"""

    scope: AnalysisReportScope
    run: AnalysisReportRun
    finding_count: int = 0
    recommendation_count: int = 0
    data_quality: dict[str, Any] = Field(default_factory=dict)
    snapshot_captured_at: str | None = None
    snapshot_source: str = "frozen"


class AnalysisReportCatalog(BaseModel):
    api_version: str = PLUGIN_API_VERSION
    schema_version: str = "1.0.0"
    term_id: int
    total: int
    reports: list[AnalysisReportListItem] = Field(default_factory=list)


# --- 匿名学生档案 ---
class StudentExamPointLite(BaseModel):
    exam_id: int
    exam_name: str
    exam_date: str | None = None
    full_score: float
    total_score: float | None = None
    score_rate: float | None = None
    attendance_status: str
    tier: str | None = None


class StudentProfileView(BaseModel):
    student_anon_id: str
    term_id: int
    class_id: int
    class_name: str | None = None
    target_score: float | None = None
    weak_tags: list[str] = Field(default_factory=list)
    identifiable: bool = False
    exams: list[StudentExamPointLite] = Field(default_factory=list)
    latest_score: StudentExamPointLite | None = None
    learning_profile: dict[str, Any] = Field(default_factory=dict)
    learning_profile_version: int = 0


class StudentSearchItem(BaseModel):
    """学生检索结果；默认不返回姓名等可识别信息。"""

    student_id: int
    student_anon_id: str
    class_id: int
    class_name: str | None = None
    display_name: str | None = None


class StudentSearchResponse(BaseModel):
    api_version: str = PLUGIN_API_VERSION
    term_id: int
    total: int = 0
    students: list[StudentSearchItem] = Field(default_factory=list)
    identifiable: bool = False


class PracticeErrorCause(BaseModel):
    cause: str
    status: str
    confidence: float | None = None
    knowledge_point: str | None = None


class PracticeWrongItem(BaseModel):
    item_id: int
    exam_id: int
    exam_name: str
    question_id: int
    question_no: str
    question_type: str | None = None
    content_text: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    correct_answer: Any = None
    student_answer: str | None = None
    score: float | None = None
    max_score: float | None = None
    score_rate: float | None = None
    knowledge_points: list[str] = Field(default_factory=list)
    pitfall_tags: list[str] = Field(default_factory=list)
    error_causes: list[PracticeErrorCause] = Field(default_factory=list)


class PracticeKnowledgePoint(BaseModel):
    name: str
    wrong_count: int = 0
    average_score_rate: float | None = None


class PracticeErrorPattern(BaseModel):
    cause: str
    count: int = 0
    confirmed_count: int = 0


class StudentPracticeContext(BaseModel):
    api_version: str = PLUGIN_API_VERSION
    term_id: int
    class_id: int
    class_name: str | None = None
    student_anon_id: str
    profile_version: int = 0
    confirmed_profile: dict[str, Any] = Field(default_factory=dict)
    longitudinal_profile: dict[str, Any] = Field(default_factory=dict)
    weak_knowledge_points: list[PracticeKnowledgePoint] = Field(default_factory=list)
    error_patterns: list[PracticeErrorPattern] = Field(default_factory=list)
    wrong_items: list[PracticeWrongItem] = Field(default_factory=list)
    total_wrong_items: int = 0
    identifiable: bool = False


# --- 复习计划事实 ---
class DimensionAverage(BaseModel):
    dimension_id: int
    dimension_name: str
    average_score_rate: float | None = None


class ReviewPlanFacts(BaseModel):
    term_id: int
    exam_id: int
    class_id: int | None = None
    dimension_averages: list[DimensionAverage] = Field(default_factory=list)
    common_error_dimensions: list[str] = Field(default_factory=list)
    weak_knowledge_points: list[str] = Field(default_factory=list)
    coverage: dict = Field(default_factory=dict)


# --- 确认资料 ---
class FormalMaterialMeta(BaseModel):
    material_id: int
    title: str
    original_name: str | None = None
    purpose: str | None = None
    confirmed_at: str | None = None
    char_count: int = 0


class FormalMaterialPage(BaseModel):
    material_id: int
    title: str
    page: int
    page_size: int
    total_pages: int
    total_chars: int
    text: str
    truncated: bool = False


# --- 证据 ---
class EvidenceSource(BaseModel):
    entity: str | None = None
    field: str | None = None
    file: str | None = None
    page: int | None = None
    question_no: str | None = None
    cell: str | None = None


class EvidenceCalculation(BaseModel):
    formula: str | None = None
    numerator: float | None = None
    denominator: float | None = None


class EvidenceView(BaseModel):
    evidence_id: str
    evidence_type: str
    display_summary: str | None = None
    local_fact: dict = Field(default_factory=dict)
    source: EvidenceSource = Field(default_factory=EvidenceSource)
    calculation: EvidenceCalculation = Field(default_factory=EvidenceCalculation)
    contains_personal_data: bool = False
