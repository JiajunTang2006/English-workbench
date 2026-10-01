"""
Agent 相关 SQLAlchemy 数据模型

本模块定义 Agent 所需的全部数据库实体，包括：
- 试卷版本与题目结构 (exam_paper_versions, exam_questions)
- 知识点词表 (knowledge_points, question_knowledge_points)
- 学生小题结果 (student_item_results)
- 错因评估 (error_cause_assessments)
- Agent 会话与消息 (agent_sessions, agent_messages, agent_message_attachments)
- 分析运行记录 (analysis_runs)
- 证据账本 (analysis_evidence)
- 学生评价 (student_evaluations)
- 评价审计日志 (evaluation_audit_logs)
- 学生画像与画像变更 (student_profiles / student_profile_revisions)
- 跨学期长期学生画像 (student_longitudinal_profiles)
- 后台任务 (background_jobs)
- LLM 用量记录 (llm_usage_records)
- 分析设置 (agent_analysis_settings)

设计原则：
- 所有业务时间使用带时区 UTC
- 不修改现有考试和总分事实表的职责
- 学生评价保留 AI 草稿和教师确认文本，不得覆盖
- 错因候选值由当前学科配置提供
- student_item_results 对考试、学生、题目唯一
- 题目结构按版本保存，不原地覆盖已确认版本
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Integer, JSON,
    String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from ..database import Base
from ..question_types import canonical_question_type
from .entities import utcnow


# ---------------------------------------------------------------------------
# 试卷结构相关
# ---------------------------------------------------------------------------

class ExamPaperVersion(Base):
    """试卷结构版本。每次教师确认后生成不可变版本，不静默覆盖旧结构。

    状态流转：draft -> confirmed -> superseded
    """

    __tablename__ = "exam_paper_versions"
    __table_args__ = (
        UniqueConstraint("exam_id", "version", name="uq_paper_versions_exam_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exam_id: Mapped[int] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    source_attachment_ids_json: Mapped[list[int]] = mapped_column(JSON, default=list)
    full_score: Mapped[float | None] = mapped_column(Float)
    extraction_provider: Mapped[str | None] = mapped_column(String(50))
    extraction_model: Mapped[str | None] = mapped_column(String(100))
    external_revision: Mapped[str | None] = mapped_column(String(150))
    structure_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    source_sync_run_id: Mapped[int | None] = mapped_column(ForeignKey("school_sync_runs.id", ondelete="SET NULL"), index=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    questions: Mapped[list["ExamQuestion"]] = relationship(
        back_populates="paper_version", cascade="all, delete-orphan"
    )


class ExamQuestion(Base):
    """试卷题目结构，按版本保存。"""

    __tablename__ = "exam_questions"

    @validates("question_type", "section_name")
    def normalize_reading_type(self, key, value):
        return canonical_question_type(value)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    paper_version_id: Mapped[int] = mapped_column(
        ForeignKey("exam_paper_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question_no: Mapped[str] = mapped_column(String(20), nullable=False)
    sub_question_no: Mapped[str | None] = mapped_column(String(20))
    external_id: Mapped[str | None] = mapped_column(String(150), index=True)
    section_name: Mapped[str | None] = mapped_column(String(100))
    question_type: Mapped[str | None] = mapped_column(String(50))
    content_text: Mapped[str | None] = mapped_column(Text)
    options_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    max_score: Mapped[float] = mapped_column(Float, nullable=False)
    correct_answer_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    excel_column_key: Mapped[str | None] = mapped_column(String(50))
    included_in_analysis: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    extraction_confidence: Mapped[float | None] = mapped_column(Float)
    source_page: Mapped[int | None] = mapped_column(Integer)
    difficulty_level: Mapped[str | None] = mapped_column(String(50))
    cognitive_level: Mapped[str | None] = mapped_column(String(50))
    knowledge_nodes_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    ability_nodes_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    pitfall_tags_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    teaching_blocks_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    paper_version: Mapped[ExamPaperVersion] = relationship(back_populates="questions")
    knowledge_point_links: Mapped[list["QuestionKnowledgePoint"]] = relationship(
        back_populates="question", cascade="all, delete-orphan"
    )


class KnowledgePoint(Base):
    """知识点词表（三级结构）。AI 只能选已存在 code 或提出 draft。

    层级示例：
    一级：语法 / 词汇 / 阅读 / 写作 / 听力 / 其他
    二级：动词时态 / 从句 / 篇章理解 / 书面表达组织等
    三级：一般过去时 / 宾语从句 / 细节定位 / 推断判断等
    """

    __tablename__ = "knowledge_points"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_points.id", ondelete="SET NULL"), index=True
    )
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    code: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    aliases_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    children: Mapped[list["KnowledgePoint"]] = relationship(back_populates="parent")
    parent: Mapped["KnowledgePoint | None"] = relationship(
        back_populates="children", remote_side="KnowledgePoint.id"
    )


class QuestionKnowledgePoint(Base):
    """题目与知识点的多对多映射。source: ai / teacher / import。"""

    __tablename__ = "question_knowledge_points"
    __table_args__ = (
        UniqueConstraint("question_id", "knowledge_point_id", name="uq_qkp_pair"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_id: Mapped[int] = mapped_column(
        ForeignKey("exam_questions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    knowledge_point_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_points.id", ondelete="CASCADE"), nullable=False, index=True
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source: Mapped[str] = mapped_column(String(20), default="ai", nullable=False)
    confirmed_by_teacher: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    question: Mapped[ExamQuestion] = relationship(back_populates="knowledge_point_links")
    knowledge_point: Mapped[KnowledgePoint] = relationship()


class StudentItemResult(Base):
    """学生小题结果，对考试、学生、题目唯一。"""

    __tablename__ = "student_item_results"
    __table_args__ = (
        UniqueConstraint("exam_id", "student_id", "question_id", name="uq_sir_exam_student_question"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exam_id: Mapped[int] = mapped_column(
        ForeignKey("exams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question_id: Mapped[int] = mapped_column(
        ForeignKey("exam_questions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    score: Mapped[float | None] = mapped_column(Float)
    score_rate: Mapped[float | None] = mapped_column(Float)
    correct: Mapped[bool | None] = mapped_column(Boolean)
    selected_option: Mapped[str | None] = mapped_column(String(100))
    time_spent_ms: Mapped[int | None] = mapped_column(Integer)
    modify_count: Mapped[int | None] = mapped_column(Integer)
    hesitation_time_ms: Mapped[int | None] = mapped_column(Integer)
    teaching_blocks_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    pitfall_tags_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    student_answer_text: Mapped[str | None] = mapped_column(Text)
    attendance_status: Mapped[str] = mapped_column(String(20), default="present", nullable=False)
    source_attachment_id: Mapped[int | None] = mapped_column(ForeignKey("attachments.id"))
    source_cell: Mapped[str | None] = mapped_column(String(50))
    import_confidence: Mapped[float | None] = mapped_column(Float)
    source_sync_run_id: Mapped[int | None] = mapped_column(ForeignKey("school_sync_runs.id", ondelete="SET NULL"), index=True)
    source_record_id: Mapped[str | None] = mapped_column(String(150))
    teacher_override: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    override_note: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ErrorCauseAssessment(Base):
    """错因评估。类别名称由当前学科的诊断配置提供。

    一道错题允许多个错因。教师确认后的版本用于后续纵向统计。
    """

    __tablename__ = "error_cause_assessments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exam_id: Mapped[int] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("exam_questions.id", ondelete="CASCADE"), nullable=False, index=True)
    knowledge_point_id: Mapped[int | None] = mapped_column(ForeignKey("knowledge_points.id"))
    cause: Mapped[str] = mapped_column(String(20), nullable=False)
    # 当前学科的错因类别名称
    confidence: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(20), default="ai", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="candidate", nullable=False)
    # candidate / confirmed / rejected
    analysis_run_id: Mapped[int | None] = mapped_column(ForeignKey("analysis_runs.id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ExamPaperMemory(Base):
    """试卷记忆：一场考试的 AI 理解摘要（语篇研读 What/Why/How 框架）。

    教师确认（confirmed）后作为分析依据注入分析包；版本化，确认新版时
    旧 confirmed 置为 superseded。source: ai / manual。
    """

    __tablename__ = "exam_paper_memories"
    __table_args__ = (
        UniqueConstraint("exam_id", "version", name="uq_paper_memory_exam_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exam_id: Mapped[int] = mapped_column(
        ForeignKey("exams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    paper_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("exam_paper_versions.id", ondelete="SET NULL")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    subject_key: Mapped[str] = mapped_column(String(40), default="english", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    # draft / confirmed / superseded
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(20), default="ai", nullable=False)
    generation_model: Mapped[str | None] = mapped_column(String(100))
    knowledge_gaps_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AgentSession(Base):
    """Agent 会话。固定当前学期，可选绑定班级、考试或学生。"""

    __tablename__ = "agent_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200), default="新会话", nullable=False)
    term_id: Mapped[int] = mapped_column(ForeignKey("terms.id", ondelete="CASCADE"), nullable=False, index=True)
    subject_key: Mapped[str] = mapped_column(String(40), default="english", nullable=False, index=True)
    class_id: Mapped[int | None] = mapped_column(ForeignKey("classes.id"))
    exam_id: Mapped[int | None] = mapped_column(ForeignKey("exams.id"))
    student_id: Mapped[int | None] = mapped_column(ForeignKey("students.id"))
    summary: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    # U1-03: Harness 会话映射
    harness_session_id: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    context_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    teaching_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("teaching_tasks.id", ondelete="SET NULL"), index=True
    )

    messages: Mapped[list["AgentMessage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="AgentMessage.created_at"
    )


class AgentMessage(Base):
    """Agent 消息。保留完整消息和结构化回答。"""

    __tablename__ = "agent_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("agent_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    analysis_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="SET NULL"), index=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    # user / assistant / system
    content_text: Mapped[str | None] = mapped_column(Text)
    structured_answer_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Teacher edits stay separate from the immutable AI report for comparison.
    teacher_material_edits_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    evidence_ids_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    model_name: Mapped[str | None] = mapped_column(String(100))
    provider_request_id: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    session: Mapped[AgentSession] = relationship(back_populates="messages")
    attachments: Mapped[list["AgentMessageAttachment"]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )


class AgentMessageAttachment(Base):
    """消息附件关联。可晋升为正式考试资料。"""

    __tablename__ = "agent_message_attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("agent_messages.id", ondelete="CASCADE"), nullable=False, index=True)
    attachment_id: Mapped[int | None] = mapped_column(ForeignKey("attachments.id"))
    chat_attachment_path: Mapped[str | None] = mapped_column(String(1000))
    purpose: Mapped[str] = mapped_column(String(30), default="chat_context", nullable=False)
    # exam_paper/answer_key/item_score_sheet/student_answer_sheet/student_exam_image/chat_context
    promoted_to_formal: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    message: Mapped[AgentMessage] = relationship(back_populates="attachments")


class AnalysisGroup(Base):
    """受控的多 Agent 分析任务组。"""

    __tablename__ = "analysis_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_type: Mapped[str] = mapped_column(String(40), nullable=False)
    # student_batch / exam_plus_students
    term_id: Mapped[int] = mapped_column(
        ForeignKey("terms.id", ondelete="CASCADE"), nullable=False, index=True
    )
    class_id: Mapped[int | None] = mapped_column(Integer, index=True)
    exam_id: Mapped[int | None] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(30), default="queued", nullable=False)
    # queued / running / completed / partially_completed / failed / cancelled
    requested_student_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_concurrency: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    estimated_cost_yuan: Mapped[float | None] = mapped_column(Float)
    actual_cost_yuan: Mapped[float | None] = mapped_column(Float)
    scope_snapshot_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    scope_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(100), default="teacher", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AnalysisGroupTask(Base):
    """任务组中的一个独立运行及其分片元数据。"""

    __tablename__ = "analysis_group_tasks"
    __table_args__ = (
        UniqueConstraint("group_id", "shard_index", name="uq_analysis_group_task_shard"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_groups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    analysis_run_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_role: Mapped[str] = mapped_column(String(40), default="student_worker", nullable=False)
    shard_index: Mapped[int] = mapped_column(Integer, nullable=False)
    depends_on_json: Mapped[list[int]] = mapped_column(JSON, default=list, nullable=False)
    student_ids_json: Mapped[list[int]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="queued", nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AnalysisRun(Base):
    """分析运行记录。

    状态机：created -> validating -> estimating -> waiting_confirmation
            -> queued -> running -> completed / failed / cancelled
    """

    __tablename__ = "analysis_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int | None] = mapped_column(ForeignKey("agent_sessions.id"), index=True)
    capability: Mapped[str] = mapped_column(String(50), nullable=False)
    # exam_analysis / student_diagnosis / review_plan / exam_ingestion
    term_id: Mapped[int] = mapped_column(ForeignKey("terms.id"), nullable=False, index=True)
    subject_key: Mapped[str] = mapped_column(String(40), default="english", nullable=False, index=True)
    class_id: Mapped[int | None] = mapped_column(Integer)
    exam_id: Mapped[int | None] = mapped_column(Integer)
    student_id: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="created", nullable=False)
    input_summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    rules_version: Mapped[str | None] = mapped_column(String(20))
    prompt_version: Mapped[str | None] = mapped_column(String(20))
    # P0-2: 绑定运行时的配置版本号，用于审计 provider 切换
    config_version: Mapped[int | None] = mapped_column(Integer)
    tool_calls_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    estimated_cost_yuan: Mapped[float | None] = mapped_column(Float)
    actual_cost_yuan: Mapped[float | None] = mapped_column(Float)
    estimated_tokens: Mapped[int | None] = mapped_column(Integer)
    actual_tokens: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # H0-3: 运行时元数据，区分 legacy / harness 路径
    runtime_kind: Mapped[str] = mapped_column(String(20), default="legacy", nullable=False)
    runtime_version: Mapped[str | None] = mapped_column(String(50))
    harness_session_id: Mapped[str | None] = mapped_column(String(100), index=True)
    # U1-03: Harness 消息 ID 和运行代次
    harness_message_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    runtime_generation: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AnalysisEvidence(Base):
    """证据账本。每条证据生成稳定 ID，可追溯到来源。

    证据序列化给模型前进行脱敏，展示给教师时在本地恢复姓名。
    """

    __tablename__ = "analysis_evidence"
    __table_args__ = (
        UniqueConstraint("run_id", "evidence_id", name="uq_evidence_run_eid"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    evidence_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    evidence_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # db_metric / file_page / question / student_trend / rule_signal
    local_fact_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source_entity: Mapped[str | None] = mapped_column(String(50))
    source_field: Mapped[str | None] = mapped_column(String(100))
    source_file: Mapped[str | None] = mapped_column(String(500))
    source_page: Mapped[int | None] = mapped_column(Integer)
    source_question_no: Mapped[str | None] = mapped_column(String(20))
    source_cell: Mapped[str | None] = mapped_column(String(50))
    calculation_formula: Mapped[str | None] = mapped_column(String(500))
    numerator: Mapped[float | None] = mapped_column(Float)
    denominator: Mapped[float | None] = mapped_column(Float)
    rule_id: Mapped[str | None] = mapped_column(String(50))
    rule_version: Mapped[str | None] = mapped_column(String(20))
    contains_personal_data: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    display_summary: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AnalysisRunEvent(Base):
    """运行事件持久化 (U2-02)。

    存储运行生命周期中的所有事件，支持断线重连后恢复。
    约束：UNIQUE(run_id, seq)，seq 在单次运行内严格递增。
    安全：payload_json 已脱敏，不得保存 API Key、完整 Prompt 或未脱敏工具输出。
    """

    __tablename__ = "analysis_run_events"
    __table_args__ = (
        UniqueConstraint("run_id", "seq", name="uq_run_event_run_seq"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source: Mapped[str] = mapped_column(String(20), default="gateway", nullable=False)
    # harness / gateway / worker
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StudentEvaluation(Base):
    """学生评价。AI 草稿 -> 教师编辑预览 -> 确认追加。

    保留 AI 原文和教师确认文本，以后不得用 AI 原文覆盖教师确认文本。
    """

    __tablename__ = "student_evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    term_id: Mapped[int] = mapped_column(ForeignKey("terms.id"), nullable=False, index=True)
    exam_id: Mapped[int | None] = mapped_column(ForeignKey("exams.id"))
    ai_original_text: Mapped[str | None] = mapped_column(Text)
    teacher_confirmed_text: Mapped[str | None] = mapped_column(Text)
    evidence_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    analysis_run_id: Mapped[int | None] = mapped_column(ForeignKey("analysis_runs.id"))
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    # draft / confirmed / archived
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class EvaluationAuditLog(Base):
    """评价审计日志。记录评价的创建、编辑、确认、归档操作。

    追加式写入，不允许修改或删除历史记录。
    """

    __tablename__ = "evaluation_audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    evaluation_id: Mapped[int] = mapped_column(
        ForeignKey("student_evaluations.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    # created / edited / confirmed / archived
    actor: Mapped[str] = mapped_column(String(100), default="teacher", nullable=False)
    detail_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StudentProfile(Base):
    """学生当前画像快照。

    画像不是成绩事实表；成绩、排名等事实仍须从考试数据实时读取。
    这里保存经教师确认的教学连续性信息，并按学生/学期隔离。
    """

    __tablename__ = "student_profiles"
    __table_args__ = (
        UniqueConstraint("student_id", "term_id", name="uq_student_profiles_student_term"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    term_id: Mapped[int] = mapped_column(ForeignKey("terms.id", ondelete="CASCADE"), nullable=False, index=True)
    profile_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_analysis_run_id: Mapped[int | None] = mapped_column(ForeignKey("analysis_runs.id", ondelete="SET NULL"), index=True)
    # 画像所引用的成长事实指纹（方案 §6.4）：与当前成长源修订号不一致时
    # 画像标记「依据已更新」。该字段只由服务端写入，不在模型可改白名单内。
    growth_reference_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    updated_by: Mapped[str] = mapped_column(String(100), default="teacher", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class StudentLongitudinalProfile(Base):
    """跨学期持续累积的学生长期画像。

    ``StudentProfile`` 保留每个学期的独立快照；这里保存教师确认后的
    压缩画像，作为学生进入新学期时的连续性基础。
    """

    __tablename__ = "student_longitudinal_profiles"
    __table_args__ = (UniqueConstraint("student_id", name="uq_student_longitudinal_profiles_student"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    profile_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_source_term_id: Mapped[int | None] = mapped_column(ForeignKey("terms.id", ondelete="SET NULL"), index=True)
    last_analysis_run_id: Mapped[int | None] = mapped_column(ForeignKey("analysis_runs.id", ondelete="SET NULL"), index=True)
    needs_compression: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_by: Mapped[str] = mapped_column(String(100), default="teacher", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class StudentProfileRevision(Base):
    """学生画像变更记录。

    保留 AI 自动合并和教师手工编辑的版本记录；历史 draft 仍兼容确认/拒绝流程。
    """

    __tablename__ = "student_profile_revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    term_id: Mapped[int] = mapped_column(ForeignKey("terms.id", ondelete="CASCADE"), nullable=False, index=True)
    profile_id: Mapped[int | None] = mapped_column(ForeignKey("student_profiles.id", ondelete="SET NULL"), index=True)
    analysis_run_id: Mapped[int | None] = mapped_column(ForeignKey("analysis_runs.id", ondelete="SET NULL"), index=True)
    base_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    proposed_patch_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    evidence_ids_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    # draft / confirmed / rejected
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_by: Mapped[str | None] = mapped_column(String(100))
    rejection_reason: Mapped[str | None] = mapped_column(String(500))


class BackgroundJob(Base):
    """后台任务。单进程 Worker 先行，不引入外部消息队列。

    状态：queued / running / waiting_confirmation / completed / failed / cancelled
    """

    __tablename__ = "background_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    # exam_analysis / student_diagnosis / pdf_extract / ocr / stats_refresh
    scope_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="queued", nullable=False)
    progress: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    checkpoint_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    estimated_cost_yuan: Mapped[float | None] = mapped_column(Float)
    actual_cost_yuan: Mapped[float | None] = mapped_column(Float)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class LlmUsageRecord(Base):
    """LLM 用量与成本记录。不保存 API Key。"""

    __tablename__ = "llm_usage_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    stage: Mapped[str] = mapped_column(String(50), nullable=False)
    # text_analysis / vision_analysis / tool_call / report_generation
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # provider 返回的缓存命中输入与推理 token，单独记录便于成本/上下文诊断。
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reasoning_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    provider_prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_yuan: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    provider_request_id: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentAnalysisSetting(Base):
    """分析规则配置。修改规则不改变历史报告。"""

    __tablename__ = "agent_analysis_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    rules_version: Mapped[str] = mapped_column(String(20), default="v1.0.0", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
