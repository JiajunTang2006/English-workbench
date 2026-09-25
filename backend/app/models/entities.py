from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Term(Base):
    __tablename__ = "terms"
    __table_args__ = (UniqueConstraint("code", name="uq_terms_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    starts_on: Mapped[date | None] = mapped_column(Date)
    ends_on: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    classes: Mapped[list[Class]] = relationship(back_populates="term")
    enrollments: Mapped[list[Enrollment]] = relationship(back_populates="term")
    exams: Mapped[list[Exam]] = relationship(back_populates="term")
    workspace_state: Mapped[WorkspaceState | None] = relationship(back_populates="term", uselist=False)


class Class(Base):
    __tablename__ = "classes"
    __table_args__ = (UniqueConstraint("term_id", "name", name="uq_classes_term_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    term_id: Mapped[int] = mapped_column(ForeignKey("terms.id", ondelete="RESTRICT"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    grade: Mapped[str | None] = mapped_column(String(20))
    school_year: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    term: Mapped[Term] = relationship(back_populates="classes")
    students: Mapped[list[Student]] = relationship(back_populates="classroom")
    enrollments: Mapped[list[Enrollment]] = relationship(back_populates="classroom")
    exam_metrics: Mapped[list[ExamClassMetric]] = relationship(back_populates="classroom")


class Student(Base):
    __tablename__ = "students"
    __table_args__ = (UniqueConstraint("student_no", name="uq_students_student_no"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_no: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    gender: Mapped[str | None] = mapped_column(String(10))
    # 兼容旧版接口的当前班级镜像；正式学期归属以 enrollments 为准。
    class_id: Mapped[int | None] = mapped_column(ForeignKey("classes.id"))
    entrance_english: Mapped[float | None] = mapped_column()
    target_score: Mapped[float | None] = mapped_column()
    weak_tags: Mapped[str | None] = mapped_column(Text)
    parent_phone: Mapped[str | None] = mapped_column(String(30))
    seat: Mapped[str | None] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    classroom: Mapped[Class] = relationship(back_populates="students")
    enrollments: Mapped[list[Enrollment]] = relationship(back_populates="student")
    exam_scores: Mapped[list[ExamScore]] = relationship(back_populates="student")


class Enrollment(Base):
    __tablename__ = "enrollments"
    __table_args__ = (
        UniqueConstraint("term_id", "student_id", name="uq_enrollments_term_student"),
        UniqueConstraint("term_id", "class_id", "student_id", name="uq_enrollments_term_class_student"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    term_id: Mapped[int] = mapped_column(ForeignKey("terms.id", ondelete="CASCADE"), nullable=False, index=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("classes.id", ondelete="RESTRICT"), nullable=False, index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    joined_on: Mapped[date | None] = mapped_column(Date)
    left_on: Mapped[date | None] = mapped_column(Date)
    entrance_english: Mapped[float | None] = mapped_column(Float)
    target_score: Mapped[float | None] = mapped_column(Float)
    weak_tags: Mapped[str | None] = mapped_column(Text)
    parent_phone: Mapped[str | None] = mapped_column(String(30))
    seat: Mapped[str | None] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    term: Mapped[Term] = relationship(back_populates="enrollments")
    classroom: Mapped[Class] = relationship(back_populates="enrollments")
    student: Mapped[Student] = relationship(back_populates="enrollments")


class Exam(Base):
    __tablename__ = "exams"
    __table_args__ = (UniqueConstraint("term_id", "source_key", name="uq_exams_term_source_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    term_id: Mapped[int] = mapped_column(ForeignKey("terms.id", ondelete="RESTRICT"), nullable=False, index=True)
    source_key: Mapped[str | None] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    exam_date: Mapped[date | None] = mapped_column(Date)
    full_score: Mapped[float] = mapped_column(Float, default=100, nullable=False)
    exam_type: Mapped[str] = mapped_column(String(30), default="english_total", nullable=False)
    exam_kind: Mapped[str] = mapped_column(String(30), default="regular", nullable=False)
    tier_a_cutoff: Mapped[float | None] = mapped_column(Float)
    tier_b_cutoff: Mapped[float | None] = mapped_column(Float)
    tier_c_cutoff: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    term: Mapped[Term] = relationship(back_populates="exams")
    dimensions: Mapped[list[ScoreDimension]] = relationship(back_populates="exam", cascade="all, delete-orphan", order_by="ScoreDimension.position")
    scores: Mapped[list[ExamScore]] = relationship(back_populates="exam", cascade="all, delete-orphan")
    class_metrics: Mapped[list[ExamClassMetric]] = relationship(back_populates="exam", cascade="all, delete-orphan")


class ScoreDimension(Base):
    __tablename__ = "score_dimensions"
    __table_args__ = (
        UniqueConstraint("exam_id", "code", name="uq_score_dimensions_exam_code"),
        UniqueConstraint("exam_id", "name", name="uq_score_dimensions_exam_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exam_id: Mapped[int] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    max_score: Mapped[float] = mapped_column(Float, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    exam: Mapped[Exam] = relationship(back_populates="dimensions")
    dimension_scores: Mapped[list[ExamDimensionScore]] = relationship(back_populates="dimension", cascade="all, delete-orphan")


class ExamScore(Base):
    __tablename__ = "exam_scores"
    __table_args__ = (UniqueConstraint("exam_id", "student_id", name="uq_exam_scores_exam_student"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exam_id: Mapped[int] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), nullable=False, index=True)
    total_score: Mapped[float | None] = mapped_column(Float)
    class_rank: Mapped[int | None] = mapped_column(Integer)
    grade_rank: Mapped[int | None] = mapped_column(Integer)
    global_rank: Mapped[int | None] = mapped_column(Integer)
    attendance_status: Mapped[str] = mapped_column(String(20), default="present", nullable=False)
    class_id_at_exam: Mapped[int | None] = mapped_column(ForeignKey("classes.id"), index=True)
    note: Mapped[str | None] = mapped_column(String(500))
    source_sync_run_id: Mapped[int | None] = mapped_column(ForeignKey("school_sync_runs.id", ondelete="SET NULL"), index=True)
    teacher_override: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    override_note: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    exam: Mapped[Exam] = relationship(back_populates="scores")
    student: Mapped[Student] = relationship(back_populates="exam_scores")
    dimension_scores: Mapped[list[ExamDimensionScore]] = relationship(back_populates="exam_score", cascade="all, delete-orphan")


class ExamClassMetric(Base):
    __tablename__ = "exam_class_metrics"
    __table_args__ = (UniqueConstraint("exam_id", "class_id", name="uq_exam_class_metrics_pair"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exam_id: Mapped[int] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"), nullable=False, index=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("classes.id", ondelete="CASCADE"), nullable=False, index=True)
    grade_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    exam: Mapped[Exam] = relationship(back_populates="class_metrics")
    classroom: Mapped[Class] = relationship(back_populates="exam_metrics")


class ExamDimensionScore(Base):
    __tablename__ = "exam_dimension_scores"
    __table_args__ = (UniqueConstraint("exam_score_id", "dimension_id", name="uq_exam_dimension_scores_pair"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exam_score_id: Mapped[int] = mapped_column(ForeignKey("exam_scores.id", ondelete="CASCADE"), nullable=False, index=True)
    dimension_id: Mapped[int] = mapped_column(ForeignKey("score_dimensions.id", ondelete="CASCADE"), nullable=False, index=True)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    exam_score: Mapped[ExamScore] = relationship(back_populates="dimension_scores")
    dimension: Mapped[ScoreDimension] = relationship(back_populates="dimension_scores")


class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value_json: Mapped[Any] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ImportJob(Base):
    __tablename__ = "import_jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    report_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SchoolDataSource(Base):
    """外部学校数据源配置。连接方式先保留为 mock/api/mcp。"""

    __tablename__ = "school_data_sources"
    __table_args__ = (UniqueConstraint("source_key", name="uq_school_data_sources_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_key: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), default="mock", nullable=False)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ExternalEntityMapping(Base):
    """外部系统实体与 TeachMate 实体的稳定映射，避免用姓名匹配。"""

    __tablename__ = "external_entity_mappings"
    __table_args__ = (
        UniqueConstraint("source_id", "entity_type", "external_id", name="uq_external_entity_mapping"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("school_data_sources.id", ondelete="CASCADE"), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)
    external_id: Mapped[str] = mapped_column(String(150), nullable=False)
    local_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class SchoolSyncRun(Base):
    """一次学校数据同步的可审计记录。"""

    __tablename__ = "school_sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("school_data_sources.id", ondelete="SET NULL"), index=True)
    snapshot_id: Mapped[str | None] = mapped_column(String(150))
    status: Mapped[str] = mapped_column(String(30), default="preview", nullable=False, index=True)
    scope_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BackupRecord(Base):
    __tablename__ = "backup_records"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(30), default="operation", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Attachment(Base):
    """元数据 for local files kept outside the workspace JSON snapshot."""

    __tablename__ = "attachments"
    __table_args__ = (UniqueConstraint("term_id", "storage_name", name="uq_attachments_term_storage_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    term_id: Mapped[int] = mapped_column(ForeignKey("terms.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    storage_name: Mapped[str] = mapped_column(String(120), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    term: Mapped[Term] = relationship()


class AttachmentDerivative(Base):
    """附件派生物：页级图像、缩略图、OCR 文本、视觉观测等。

    大规模页级结果独立存储（方案 §10.3），不再无限塞入
    attachments.metadata_json。旧字段保留兼容读取。
    """

    __tablename__ = "attachment_derivatives"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    attachment_id: Mapped[int] = mapped_column(
        ForeignKey("attachments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(30), nullable=False, index=True)  # page_image/thumbnail/ocr_text/vision_observation
    page_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    storage_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str | None] = mapped_column(String(40), nullable=True)  # local_pdf/text_extract/vision
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AttachmentAnalysisResult(Base):
    """附件视觉/ OCR 分析结果，含状态机与审计字段（方案 §10.3/§10.4）。"""

    __tablename__ = "attachment_analysis_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    attachment_id: Mapped[int] = mapped_column(
        ForeignKey("attachments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False, default="local")
    model_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    result_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # ocr/layout/handwriting/vision
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued", index=True)
    # queued/running/pending_review/confirmed/rejected/failed
    content_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    teacher_correction_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    cost_yuan: Mapped[float | None] = mapped_column(Float, nullable=True)
    provider_request_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PendingFileOperation(Base):
    __tablename__ = "pending_file_operations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    operation: Mapped[str] = mapped_column(String(20), nullable=False)
    source_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    target_path: Mapped[str | None] = mapped_column(String(1000))
    expected_size: Mapped[int | None] = mapped_column(Integer)
    expected_sha256: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ChangeLog(Base):
    __tablename__ = "change_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    detail_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkspaceState(Base):
    """旧界面完整状态的迁移期存储。

    在十二个业务模块全部拆成独立领域表之前，以这个快照作为旧界面的
    唯一数据源，避免 localStorage 与 SQLite 双写。
    """

    __tablename__ = "workspace_states"

    term_id: Mapped[int] = mapped_column(ForeignKey("terms.id", ondelete="CASCADE"), primary_key=True)
    state_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    term: Mapped[Term] = relationship(back_populates="workspace_state")


# ---------------------------------------------------------------------------
# L4：Word/PDF 文档导出（方案 §11.3）
# 统一 DocumentSpec v1 → 本地/远端 Provider → 产物登记。
# 设计约束（保守、零新增硬依赖）：
# - 导出任务独立成表，不污染 attachments / agent_runs；
# - 产物（PDF/DOCX/HTML）登记 MIME、大小、SHA-256、来源与隐私标记；
# - Provider 失败不影响报告内容（内容快照已落库 input_snapshot_json）。
# ---------------------------------------------------------------------------


class DocumentExportJob(Base):
    """一次文档导出后台任务（方案 §11.3）。"""

    __tablename__ = "document_export_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    session_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    format: Mapped[str] = mapped_column(String(10), nullable=False)  # pdf / docx
    provider: Mapped[str] = mapped_column(String(20), nullable=False, default="local")
    template_id: Mapped[str] = mapped_column(String(30), nullable=False, default="report")
    template_version: Mapped[str] = mapped_column(String(20), nullable=False, default="1.0")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued", index=True)
    # queued/running/completed/failed/cancelled
    input_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    remote_job_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    output_artifact_id: Mapped[int | None] = mapped_column(
        ForeignKey("generated_artifacts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GeneratedArtifact(Base):
    """导出产物文件登记（方案 §11.3）。不存文件本体，只存元数据与落盘名。"""

    __tablename__ = "generated_artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="document")  # document/print_html
    storage_name: Mapped[str] = mapped_column(String(160), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    contains_personal_data: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
