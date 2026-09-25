"""Add the complete TeachMate Agent analysis schema."""

from alembic import op
import sqlalchemy as sa


revision = "20260815_0010"
down_revision = "20260810_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "exam_paper_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("exam_id", sa.Integer(), sa.ForeignKey("exams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("source_attachment_ids_json", sa.JSON(), nullable=False),
        sa.Column("full_score", sa.Float()),
        sa.Column("extraction_provider", sa.String(50)),
        sa.Column("extraction_model", sa.String(100)),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("exam_id", "version", name="uq_paper_versions_exam_version"),
    )
    op.create_index("ix_exam_paper_versions_exam_id", "exam_paper_versions", ["exam_id"])

    op.create_table(
        "exam_questions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("paper_version_id", sa.Integer(), sa.ForeignKey("exam_paper_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question_no", sa.String(20), nullable=False),
        sa.Column("section_name", sa.String(100)),
        sa.Column("question_type", sa.String(50)),
        sa.Column("content_text", sa.Text()),
        sa.Column("options_json", sa.JSON(), nullable=False),
        sa.Column("max_score", sa.Float(), nullable=False),
        sa.Column("correct_answer_json", sa.JSON(), nullable=False),
        sa.Column("excel_column_key", sa.String(50)),
        sa.Column("included_in_analysis", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("extraction_confidence", sa.Float()),
        sa.Column("source_page", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_exam_questions_paper_version_id", "exam_questions", ["paper_version_id"])

    op.create_table(
        "knowledge_points",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("parent_id", sa.Integer(), sa.ForeignKey("knowledge_points.id", ondelete="SET NULL")),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(100), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("aliases_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_knowledge_points_parent_id", "knowledge_points", ["parent_id"])

    op.create_table(
        "question_knowledge_points",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("question_id", sa.Integer(), sa.ForeignKey("exam_questions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("knowledge_point_id", sa.Integer(), sa.ForeignKey("knowledge_points.id", ondelete="CASCADE"), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source", sa.String(20), nullable=False, server_default="ai"),
        sa.Column("confirmed_by_teacher", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("question_id", "knowledge_point_id", name="uq_qkp_pair"),
    )
    op.create_index("ix_question_knowledge_points_question_id", "question_knowledge_points", ["question_id"])
    op.create_index("ix_question_knowledge_points_knowledge_point_id", "question_knowledge_points", ["knowledge_point_id"])

    op.create_table(
        "agent_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False, server_default="新会话"),
        sa.Column("term_id", sa.Integer(), sa.ForeignKey("terms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("class_id", sa.Integer(), sa.ForeignKey("classes.id")),
        sa.Column("exam_id", sa.Integer(), sa.ForeignKey("exams.id")),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("students.id")),
        sa.Column("summary", sa.Text()),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agent_sessions_term_id", "agent_sessions", ["term_id"])

    op.create_table(
        "analysis_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("agent_sessions.id")),
        sa.Column("capability", sa.String(50), nullable=False),
        sa.Column("term_id", sa.Integer(), sa.ForeignKey("terms.id"), nullable=False),
        sa.Column("class_id", sa.Integer()),
        sa.Column("exam_id", sa.Integer()),
        sa.Column("student_id", sa.Integer()),
        sa.Column("status", sa.String(30), nullable=False, server_default="created"),
        sa.Column("input_summary_json", sa.JSON(), nullable=False),
        sa.Column("rules_version", sa.String(20)),
        sa.Column("prompt_version", sa.String(20)),
        sa.Column("tool_calls_json", sa.JSON(), nullable=False),
        sa.Column("estimated_cost_yuan", sa.Float()),
        sa.Column("actual_cost_yuan", sa.Float()),
        sa.Column("estimated_tokens", sa.Integer()),
        sa.Column("actual_tokens", sa.Integer()),
        sa.Column("error_message", sa.Text()),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_analysis_runs_session_id", "analysis_runs", ["session_id"])
    op.create_index("ix_analysis_runs_term_id", "analysis_runs", ["term_id"])

    op.create_table(
        "agent_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("agent_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("analysis_run_id", sa.Integer(), sa.ForeignKey("analysis_runs.id", ondelete="SET NULL")),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content_text", sa.Text()),
        sa.Column("structured_answer_json", sa.JSON(), nullable=False),
        sa.Column("evidence_ids_json", sa.JSON(), nullable=False),
        sa.Column("model_name", sa.String(100)),
        sa.Column("provider_request_id", sa.String(200)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agent_messages_session_id", "agent_messages", ["session_id"])
    op.create_index("ix_agent_messages_analysis_run_id", "agent_messages", ["analysis_run_id"])

    op.create_table(
        "agent_message_attachments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("message_id", sa.Integer(), sa.ForeignKey("agent_messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attachment_id", sa.Integer(), sa.ForeignKey("attachments.id")),
        sa.Column("chat_attachment_path", sa.String(1000)),
        sa.Column("purpose", sa.String(30), nullable=False, server_default="chat_context"),
        sa.Column("promoted_to_formal", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agent_message_attachments_message_id", "agent_message_attachments", ["message_id"])

    op.create_table(
        "student_item_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("exam_id", sa.Integer(), sa.ForeignKey("exams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question_id", sa.Integer(), sa.ForeignKey("exam_questions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("score", sa.Float()),
        sa.Column("student_answer_text", sa.Text()),
        sa.Column("attendance_status", sa.String(20), nullable=False, server_default="present"),
        sa.Column("source_attachment_id", sa.Integer(), sa.ForeignKey("attachments.id")),
        sa.Column("source_cell", sa.String(50)),
        sa.Column("import_confidence", sa.Float()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("exam_id", "student_id", "question_id", name="uq_sir_exam_student_question"),
    )
    op.create_index("ix_student_item_results_exam_id", "student_item_results", ["exam_id"])
    op.create_index("ix_student_item_results_student_id", "student_item_results", ["student_id"])
    op.create_index("ix_student_item_results_question_id", "student_item_results", ["question_id"])

    op.create_table(
        "error_cause_assessments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("exam_id", sa.Integer(), sa.ForeignKey("exams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question_id", sa.Integer(), sa.ForeignKey("exam_questions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("knowledge_point_id", sa.Integer(), sa.ForeignKey("knowledge_points.id")),
        sa.Column("cause", sa.String(20), nullable=False),
        sa.Column("confidence", sa.Float()),
        sa.Column("source", sa.String(20), nullable=False, server_default="ai"),
        sa.Column("status", sa.String(20), nullable=False, server_default="candidate"),
        sa.Column("analysis_run_id", sa.Integer(), sa.ForeignKey("analysis_runs.id")),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_error_cause_assessments_exam_id", "error_cause_assessments", ["exam_id"])
    op.create_index("ix_error_cause_assessments_student_id", "error_cause_assessments", ["student_id"])
    op.create_index("ix_error_cause_assessments_question_id", "error_cause_assessments", ["question_id"])

    op.create_table(
        "analysis_evidence",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("evidence_id", sa.String(50), nullable=False),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("evidence_type", sa.String(50), nullable=False),
        sa.Column("local_fact_json", sa.JSON(), nullable=False),
        sa.Column("source_entity", sa.String(50)),
        sa.Column("source_field", sa.String(100)),
        sa.Column("source_file", sa.String(500)),
        sa.Column("source_page", sa.Integer()),
        sa.Column("source_question_no", sa.String(20)),
        sa.Column("source_cell", sa.String(50)),
        sa.Column("calculation_formula", sa.String(500)),
        sa.Column("numerator", sa.Float()),
        sa.Column("denominator", sa.Float()),
        sa.Column("rule_id", sa.String(50)),
        sa.Column("rule_version", sa.String(20)),
        sa.Column("contains_personal_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("display_summary", sa.String(500)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "evidence_id", name="uq_evidence_run_eid"),
    )
    op.create_index("ix_analysis_evidence_evidence_id", "analysis_evidence", ["evidence_id"])
    op.create_index("ix_analysis_evidence_run_id", "analysis_evidence", ["run_id"])

    op.create_table(
        "student_evaluations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
        sa.Column("term_id", sa.Integer(), sa.ForeignKey("terms.id"), nullable=False),
        sa.Column("exam_id", sa.Integer(), sa.ForeignKey("exams.id")),
        sa.Column("ai_original_text", sa.Text()),
        sa.Column("teacher_confirmed_text", sa.Text()),
        sa.Column("evidence_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("analysis_run_id", sa.Integer(), sa.ForeignKey("analysis_runs.id")),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_student_evaluations_student_id", "student_evaluations", ["student_id"])
    op.create_index("ix_student_evaluations_term_id", "student_evaluations", ["term_id"])

    op.create_table(
        "background_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_type", sa.String(50), nullable=False),
        sa.Column("scope_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="queued"),
        sa.Column("progress", sa.Float(), nullable=False, server_default="0"),
        sa.Column("checkpoint_json", sa.JSON(), nullable=False),
        sa.Column("estimated_cost_yuan", sa.Float()),
        sa.Column("actual_cost_yuan", sa.Float()),
        sa.Column("idempotency_key", sa.String(200)),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_background_jobs_job_type", "background_jobs", ["job_type"])
    op.create_index("ix_background_jobs_idempotency_key", "background_jobs", ["idempotency_key"])

    op.create_table(
        "llm_usage_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("model_name", sa.String(100), nullable=False),
        sa.Column("stage", sa.String(50), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_yuan", sa.Float(), nullable=False, server_default="0"),
        sa.Column("provider_request_id", sa.String(200)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_llm_usage_records_run_id", "llm_usage_records", ["run_id"])

    op.create_table(
        "agent_analysis_settings",
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("value_json", sa.JSON(), nullable=False),
        sa.Column("rules_version", sa.String(20), nullable=False, server_default="v1.0.0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    for table_name in (
        "agent_analysis_settings",
        "llm_usage_records",
        "background_jobs",
        "student_evaluations",
        "analysis_evidence",
        "error_cause_assessments",
        "student_item_results",
        "agent_message_attachments",
        "agent_messages",
        "analysis_runs",
        "agent_sessions",
        "question_knowledge_points",
        "knowledge_points",
        "exam_questions",
        "exam_paper_versions",
    ):
        op.drop_table(table_name)
