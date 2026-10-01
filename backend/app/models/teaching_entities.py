"""Persistent teacher-owned tasks, immutable material revisions and observations."""
from datetime import date, datetime
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from .entities import utcnow


class TeachingTask(Base):
    __tablename__ = "teaching_tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    term_id: Mapped[int] = mapped_column(ForeignKey("terms.id", ondelete="CASCADE"), index=True)
    subject_key: Mapped[str] = mapped_column(String(40), nullable=False)
    class_id: Mapped[int | None] = mapped_column(ForeignKey("classes.id", ondelete="SET NULL"))
    exam_id: Mapped[int | None] = mapped_column(ForeignKey("exams.id", ondelete="SET NULL"))
    target_type: Mapped[str] = mapped_column(String(20), default="class", nullable=False)
    student_ids_json: Mapped[list[int]] = mapped_column(JSON, default=list, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    constraints_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    phase: Mapped[str] = mapped_column(String(30), default="diagnose", nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class TeachingArtifact(Base):
    __tablename__ = "teaching_artifacts"
    __table_args__ = (UniqueConstraint("task_id", "source_run_id", "source_section", name="uq_task_source_section"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("teaching_tasks.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    items_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    source_run_id: Mapped[int | None] = mapped_column(ForeignKey("analysis_runs.id", ondelete="SET NULL"))
    source_section: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class TeachingArtifactRevision(Base):
    __tablename__ = "teaching_artifact_revisions"
    __table_args__ = (UniqueConstraint("artifact_id", "revision", name="uq_artifact_revision"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    artifact_id: Mapped[int] = mapped_column(ForeignKey("teaching_artifacts.id", ondelete="CASCADE"), index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    content_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    origin: Mapped[str] = mapped_column(String(30), default="teacher", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TeachingFeedback(Base):
    __tablename__ = "teaching_feedback"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("teaching_tasks.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    observations_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    context_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PracticeSet(Base):
    """Teacher-owned immutable question set; confirming it does not award mastery."""
    __tablename__ = "practice_sets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("teaching_tasks.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    objective: Mapped[str] = mapped_column(String(300), nullable=False)
    student_ids_json: Mapped[list[int]] = mapped_column(JSON, default=list, nullable=False)
    level: Mapped[str] = mapped_column(String(30), default="progressive", nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="draft", nullable=False)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("practice_sets.id", ondelete="SET NULL"))
    source_run_id: Mapped[int | None] = mapped_column(ForeignKey("analysis_runs.id", ondelete="SET NULL"))
    checks_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    constraints_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PracticeQuestion(Base):
    """No in-place editing endpoint: historical attempts retain this exact version."""
    __tablename__ = "practice_questions"
    __table_args__ = (UniqueConstraint("practice_id", "position", name="uq_practice_question_position"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    practice_id: Mapped[int] = mapped_column(ForeignKey("practice_sets.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    objective: Mapped[str] = mapped_column(String(300), nullable=False)
    content_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class PracticeAttempt(Base):
    __tablename__ = "practice_attempts"
    __table_args__ = (UniqueConstraint("practice_id", "submission_key", name="uq_practice_submission"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    practice_id: Mapped[int] = mapped_column(ForeignKey("practice_sets.id", ondelete="CASCADE"), index=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("practice_questions.id", ondelete="RESTRICT"), index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    submission_key: Mapped[str] = mapped_column(String(100), nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    observed_on: Mapped[date] = mapped_column(Date, nullable=False)
    hint_level: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    answer_viewed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    new_question: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    correct: Mapped[bool | None] = mapped_column(Boolean)
    graded_by: Mapped[str] = mapped_column(String(30), default="pending", nullable=False)
    feedback_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    corrections_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
