"""
Agent Pydantic Schema

定义 Agent API 的请求和响应模型，包括：
- 会话管理 (AgentSessionCreate, AgentSessionRead, AgentMessageRead)
- 分析请求与响应 (AnalysisEstimateRequest, AnalysisRunRequest, AnalysisRunRead)
- 结构化回答 (StructuredAnswer, Finding, Recommendation)
- 证据展示 (EvidenceRead)
- 后台任务 (JobRead)
- 学生评价确认 (EvaluationConfirmRequest, EvaluationRead)

分离原则：
- 草稿、确认、只读结果使用不同 Schema
- Agent 模型输出 Schema 与数据库写入 Schema 分离
- 教师确认接口不得接受模型供应商、费用等客户端伪造字段
- 输入长度和列表数量有上限
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


# =====================================================================
# 会话管理
# =====================================================================

class AgentSessionCreate(BaseModel):
    """创建 Agent 会话。"""
    title: str = Field(default="新会话", max_length=200)
    term_id: int
    class_id: int | None = None
    exam_id: int | None = None
    student_id: int | None = None
    model_config = {"extra": "forbid"}

    @field_validator("title")
    @classmethod
    def _normalize_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("会话标题不能为空")
        return value


class AgentSessionUpdate(BaseModel):
    """更新会话（受约束 Schema）。

    只允许更新标题和状态（归档/恢复）。
    不允许直接写内部 summary 或任意 status。
    """
    title: str | None = Field(default=None, max_length=200)
    status: str | None = Field(default=None, pattern="^(active|archived)$")


class AgentSessionRead(BaseModel):
    """读取 Agent 会话。"""
    id: int
    title: str
    term_id: int
    subject_key: str = "english"
    class_id: int | None = None
    exam_id: int | None = None
    student_id: int | None = None
    teaching_task_id: int | None = None
    summary: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SessionPreferences(BaseModel):
    """TeachMate 导航偏好；只保存会话 ID 和文件夹元数据。"""
    term_id: int
    pinned_session_ids: list[int] = Field(default_factory=list, max_length=500)
    folders: list[dict[str, Any]] = Field(default_factory=list, max_length=100)


class AgentMessageCreate(BaseModel):
    """发送消息到 Agent。"""
    content: str = Field(min_length=1, max_length=10000)
    attachment_ids: list[int] = Field(default_factory=list, max_length=20)
    plugin_id: Literal["targeted_practice"] | None = None
    teaching_artifact_id: int | None = Field(default=None, gt=0)
    student_refs: list[str] = Field(default_factory=list, max_length=10)
    student_aliases: dict[str, str] = Field(default_factory=dict, max_length=20)
    identity_mode: Literal["teacher", "strict"] | None = None
    # 当前消息明确绑定的模型档案；为空时使用当前全局激活模型。
    model_id: str | None = Field(default=None, min_length=1, max_length=200)
    # 快捷任务路由
    quick_task: Literal[
        "exam_analysis", "student_diagnosis", "review_plan", "exam_ingestion", "general_chat"
    ] | None = None
    # exam_analysis / student_diagnosis / review_plan / None(自由文本)

    @field_validator("content")
    @classmethod
    def _normalize_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("消息内容不能为空")
        return value


class AttachmentSummary(BaseModel):
    """消息附件元数据（仅展示用：不含文件路径、解析正文与哈希）。"""
    id: int
    title: str | None = None
    original_name: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None


class AgentMessageRead(BaseModel):
    """读取 Agent 消息。

    P0-8: 固定使用 content_text / structured_answer / evidence_ids 三个字段名。
    从 ORM 的 content_text / structured_answer_json / evidence_ids_json 映射，
    前端不应直接读取 ORM 字段名或对已经是对象/数组的字段再次 JSON.parse()。
    """
    id: int
    role: str
    content_text: str | None = None
    structured_answer: dict[str, Any] | None = None
    material_edits: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    model_name: str | None = None
    provider_request_id: str | None = None
    run_id: int | None = None
    # 用户消息绑定的能力，用于历史对话恢复“考试分析”等插件标识。
    capability: str | None = None
    created_at: datetime
    # 消息关联的附件元数据（供聊天气泡展示附件 chip）
    attachments: list[AttachmentSummary] = Field(default_factory=list)

    model_config = {"from_attributes": True}

    @model_validator(mode="before")
    @classmethod
    def _map_orm_fields(cls, data: Any) -> Any:
        """从 ORM 对象映射字段名：structured_answer_json -> structured_answer, evidence_ids_json -> evidence_ids。"""
        if hasattr(data, "__dict__") or hasattr(data, "__table__"):
            # ORM 对象
            if not isinstance(data, dict):
                result = {}
                for field_name in ["id", "role", "content_text", "model_name",
                                   "provider_request_id", "created_at"]:
                    if hasattr(data, field_name):
                        result[field_name] = getattr(data, field_name)
                # 映射 JSON 字段
                sa = getattr(data, "structured_answer_json", None)
                result["structured_answer"] = sa if sa else None
                result["material_edits"] = getattr(data, "teacher_material_edits_json", None) or {}
                ei = getattr(data, "evidence_ids_json", None)
                result["evidence_ids"] = ei if ei else []
                result["run_id"] = getattr(data, "analysis_run_id", None)
                return result
        elif isinstance(data, dict):
            # 字典：如果已有 structured_answer_json 则映射
            if "structured_answer_json" in data and "structured_answer" not in data:
                data["structured_answer"] = data.pop("structured_answer_json") or None
            if "teacher_material_edits_json" in data and "material_edits" not in data:
                data["material_edits"] = data.pop("teacher_material_edits_json") or {}
            if "evidence_ids_json" in data and "evidence_ids" not in data:
                data["evidence_ids"] = data.pop("evidence_ids_json") or []
            if "analysis_run_id" in data and "run_id" not in data:
                data["run_id"] = data.pop("analysis_run_id")
        return data


# =====================================================================
# 结构化回答（对应实施方案第 12 节）
# =====================================================================

class Finding(BaseModel):
    """分析发现。每条发现必须关联至少一条证据。"""
    title: str
    scope: str | None = None
    # student_A17 等匿名编号
    claim: str
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: str = "medium"
    # high / medium / low


class Recommendation(BaseModel):
    """建议。必须关联证据。"""
    priority: int = Field(ge=1, le=10)
    action: str
    supports: list[str] = Field(default_factory=list)
    # 支撑证据 ID


class StructuredAnswer(BaseModel):
    """Agent 统一结构化输出。"""
    answer_type: str
    # exam_report / student_diagnosis / review_plan / exam_ingestion
    summary: str
    findings: list[Finding] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


# =====================================================================
# 证据
# =====================================================================

class EvidenceRead(BaseModel):
    """证据展示。学生姓名只在本地渲染。"""
    evidence_id: str
    evidence_type: str
    display_summary: str | None = None
    source_entity: str | None = None
    source_field: str | None = None
    source_page: int | None = None
    source_question_no: str | None = None
    calculation_formula: str | None = None
    numerator: float | None = None
    denominator: float | None = None
    rule_id: str | None = None
    rule_version: str | None = None

    model_config = {"from_attributes": True}


# =====================================================================
# 分析运行
# =====================================================================

class AnalysisEstimateRequest(BaseModel):
    """成本估算请求。"""
    capability: str
    term_id: int
    class_id: int | None = None
    exam_id: int | None = None
    student_id: int | None = None


class AnalysisEstimateResponse(BaseModel):
    """成本估算响应。"""
    estimated_cost_yuan: float
    estimated_tokens: int
    estimated_duration_seconds: int
    budget_soft_limit_yuan: float
    requires_confirmation: bool


class AnalysisRunRequest(BaseModel):
    """启动分析运行。"""
    session_id: int
    capability: str
    term_id: int
    class_id: int | None = None
    exam_id: int | None = None
    student_id: int | None = None
    confirmed_budget_yuan: float | None = None


class AnalysisGroupCreateRequest(BaseModel):
    """创建批量学生画像/考试协同任务组。"""
    session_id: int
    capability: Literal["student_diagnosis", "exam_plus_students"] = "student_diagnosis"
    term_id: int
    class_id: int | None = None
    exam_id: int
    student_ids: list[int] = Field(min_length=1, max_length=200)
    max_concurrency: int = Field(default=4, ge=1, le=4)
    shard_size: int = Field(default=6, ge=1, le=20)
    # 交互式 Agent 先返回完整执行方案，只有教师明确确认后才开始调度。
    # 保留 False 默认值，兼容可信 API 调用方原有的一步执行方式。
    require_confirmation: bool = False
    confirmed_budget_yuan: float | None = Field(default=None, ge=0)


class AnalysisGroupRead(BaseModel):
    id: int
    group_type: str
    term_id: int
    class_id: int | None = None
    exam_id: int | None = None
    status: str
    requested_student_count: int
    max_concurrency: int
    estimated_cost_yuan: float | None = None
    # 面向教师展示的资源预估；费用仅作为后台确认门禁，不在前端显示。
    estimated_tokens: int | None = None
    actual_cost_yuan: float | None = None
    scope_snapshot_id: str
    error_message: str | None = None
    created_at: datetime
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}


class AnalysisGroupTaskRead(BaseModel):
    id: int
    group_id: int
    analysis_run_id: int
    task_role: str
    shard_index: int
    student_ids: list[int] = Field(default_factory=list)
    status: str
    retry_count: int = 0
    error_message: str | None = None
    created_at: datetime
    completed_at: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def _map_student_ids(cls, data: Any) -> Any:
        if hasattr(data, "student_ids_json"):
            return {
                "id": data.id,
                "group_id": data.group_id,
                "analysis_run_id": data.analysis_run_id,
                "task_role": data.task_role,
                "shard_index": data.shard_index,
                "student_ids": list(data.student_ids_json or []),
                "status": data.status,
                "retry_count": data.retry_count,
                "error_message": data.error_message,
                "created_at": data.created_at,
                "completed_at": data.completed_at,
            }
        return data


class AnalysisGroupDetailRead(AnalysisGroupRead):
    tasks: list[AnalysisGroupTaskRead] = Field(default_factory=list)
    merged_result: dict[str, Any] | None = None


class AnalysisGroupConfirmRequest(BaseModel):
    confirmed_budget_yuan: float = Field(gt=0)


class AnalysisGroupRetryRequest(BaseModel):
    """选择批量任务中需要重新生成画像的学生。"""
    student_ids: list[int] = Field(min_length=1, max_length=200)


class AnalysisRunRead(BaseModel):
    """分析运行状态。"""
    id: int
    capability: str
    subject_key: str = "english"
    status: str
    estimated_cost_yuan: float | None = None
    actual_cost_yuan: float | None = None
    confirmed_budget_yuan: float | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    needs_confirmation: bool = False
    runtime_kind: str = "legacy"
    runtime_version: str | None = None
    harness_session_id: str | None = None
    timings_ms: dict[str, int] | None = None
    input_parts_estimate: dict[str, int] | None = None
    query_stats: dict[str, int] | None = None
    usage_known: bool | None = None
    report_freshness: Literal["current", "stale", "unknown"] = "unknown"
    # 与发送响应保持一致，刷新页面后仍能恢复轻量/完整进度视图。
    progress_mode: Literal["full", "light"] = "light"

    model_config = {"from_attributes": True}

    @model_validator(mode="before")
    @classmethod
    def _extract_confirmed_budget(cls, data: Any) -> Any:
        """从 input_summary_json 提取 confirmed_budget_yuan。"""
        if hasattr(data, "__dict__") or hasattr(data, "__table__"):
            if not isinstance(data, dict):
                result = {}
                for f in ["id", "capability", "status", "estimated_cost_yuan",
                           "actual_cost_yuan", "error_message", "started_at",
                           "completed_at", "created_at", "runtime_kind",
                           "runtime_version", "harness_session_id"]:
                    if hasattr(data, f):
                        result[f] = getattr(data, f)
                summary = getattr(data, "input_summary_json", None) or {}
                result["confirmed_budget_yuan"] = summary.get("confirmed_budget_yuan")
                result["timings_ms"] = summary.get("timings_ms")
                for key in ("input_parts_estimate", "query_stats", "usage_known"):
                    result[key] = summary.get(key)
                result["needs_confirmation"] = data.status == "waiting_confirmation"
                result["progress_mode"] = (
                    "full" if getattr(data, "capability", "general_chat") != "general_chat"
                    or bool(summary.get("attachment_ids")) else "light"
                )
                return result
        elif isinstance(data, dict):
            for key in ("input_parts_estimate", "query_stats", "usage_known"):
                data.setdefault(key, (data.get("input_summary_json") or {}).get(key))
            if "confirmed_budget_yuan" not in data:
                data["confirmed_budget_yuan"] = (data.get("input_summary_json") or {}).get("confirmed_budget_yuan")
            if "timings_ms" not in data:
                data["timings_ms"] = (data.get("input_summary_json") or {}).get("timings_ms")
            if "needs_confirmation" not in data:
                data["needs_confirmation"] = data.get("status") == "waiting_confirmation"
            if "progress_mode" not in data:
                summary = data.get("input_summary_json") or {}
                data["progress_mode"] = (
                    "full" if data.get("capability") != "general_chat"
                    or bool(summary.get("attachment_ids")) else "light"
                )
        return data


class SendMessageResponse(BaseModel):
    """异步发送消息的响应（202 + run_id）。"""
    message_id: int
    run_id: int
    status: str = "queued"
    capability: str = "general_chat"
    # full：结构化分析/附件处理，展示步骤卡；light：普通聊天，仅显示轻量回复状态。
    progress_mode: Literal["full", "light"] = "light"


class RunEventRead(BaseModel):
    """运行事件。"""
    event: str
    timestamp: str
    data: dict[str, Any] = Field(default_factory=dict)


class RunCancelResponse(BaseModel):
    """取消运行的响应。"""
    run_id: int
    cancelled: bool
    message: str = ""


class RunRetryResponse(BaseModel):
    """重试运行的响应。"""
    new_run_id: int
    status: str = "queued"


class RunConfirmResponse(BaseModel):
    """预算确认后继续运行的响应。"""
    run_id: int
    status: str = "queued"
    message: str = ""


# =====================================================================
# 后台任务
# =====================================================================

class JobRead(BaseModel):
    """后台任务状态。"""
    id: int
    job_type: str
    status: str
    progress: float
    attempts: int = 0
    next_attempt_at: datetime | None = None
    updated_at: datetime | None = None
    checkpoint: dict[str, Any] = Field(default_factory=dict)
    phase: str | None = None
    phase_label: str | None = None
    display_status: str | None = None
    status_label: str | None = None
    message: str | None = None
    processed: int | None = None
    total: int | None = None
    conflict_count: int = 0
    warning_count: int = 0
    has_conflicts: bool = False
    can_retry: bool = False
    estimated_cost_yuan: float | None = None
    actual_cost_yuan: float | None = None
    last_error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}


# =====================================================================
# 附件解析与晋升 (U4-02)
# =====================================================================

class AttachmentParseRequest(BaseModel):
    """请求解析附件。"""
    attachment_id: int
    purpose: str = Field(
        default="chat_context",
        description="附件用途: exam_paper/answer_key/item_score_sheet/"
                    "student_answer_sheet/student_exam_image/chat_context",
    )


class AttachmentParseResponse(BaseModel):
    """附件解析任务提交结果。"""
    job_id: int
    attachment_id: int
    status: str = "queued"


class AttachmentParseResultRead(BaseModel):
    """附件解析结果（教师校对用）。"""
    attachment_id: int
    format: str = "unknown"
    content: str = ""
    needs_ocr: bool = False
    error: str | None = None
    status: str = "pending_review"  # pending_review / confirmed / rejected
    parsed_at: str | None = None
    metadata: dict = Field(default_factory=dict)
    pages: list = Field(default_factory=list)


class AttachmentPromoteRequest(BaseModel):
    """教师确认后晋升附件为正式资料。"""
    attachment_id: int
    session_id: int | None = None
    purpose: str = "chat_context"
    corrections: dict = Field(
        default_factory=dict,
        description="教师校对修正内容，合并到解析结果中",
    )


class AttachmentPromoteResponse(BaseModel):
    """晋升结果。"""
    attachment_id: int
    promoted: bool
    message: str = ""

class EvaluationCreateRequest(BaseModel):
    """从 Agent 报告草稿创建学生评价。

    保留 AI 原文，等待教师编辑和确认。
    """
    student_id: int
    term_id: int
    exam_id: int | None = None
    ai_original_text: str = Field(max_length=10000)
    evidence_snapshot: dict[str, Any] = Field(default_factory=dict)
    analysis_run_id: int | None = None

    model_config = {"extra": "forbid"}


class EvaluationUpdateRequest(BaseModel):
    """教师编辑评价草稿。

    只允许编辑 teacher_confirmed_text（预览），不可修改 AI 原文。
    """
    teacher_confirmed_text: str = Field(max_length=5000)

    model_config = {"extra": "forbid"}


class EvaluationConfirmRequest(BaseModel):
    """教师确认学生评价。

    不得接受模型供应商、费用等客户端伪造字段。
    只追加历史，不覆盖。
    """
    student_id: int
    term_id: int
    exam_id: int | None = None
    ai_original_text: str | None = None
    teacher_confirmed_text: str = Field(max_length=5000)
    evidence_snapshot: dict[str, Any] = Field(default_factory=dict)
    analysis_run_id: int | None = None


class EvaluationRead(BaseModel):
    """学生评价读取。AI 原文与教师确认文本分别保留。"""
    id: int
    student_id: int
    term_id: int
    exam_id: int | None = None
    ai_original_text: str | None = None
    teacher_confirmed_text: str | None = None
    evidence_snapshot: dict[str, Any] = Field(default_factory=dict)
    analysis_run_id: int | None = None
    status: str
    created_at: datetime
    confirmed_at: datetime | None = None
    confirmed_by: str | None = None
    rejection_reason: str | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class EvaluationAuditEntry(BaseModel):
    """评价审计日志条目。"""
    id: int
    evaluation_id: int
    action: str
    actor: str
    detail: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    model_config = {"from_attributes": True}


class StudentProfileRevisionCreate(BaseModel):
    """创建画像变更草稿；正式画像不会因该请求直接改变。"""
    student_id: int = Field(gt=0)
    term_id: int = Field(gt=0)
    patch: dict[str, Any] = Field(default_factory=dict)
    analysis_run_id: int | None = Field(default=None, gt=0)
    evidence_ids: list[str] = Field(default_factory=list, max_length=50)


class StudentProfileRevisionRead(BaseModel):
    id: int
    student_id: int
    term_id: int
    analysis_run_id: int | None = None
    base_version: int
    current_version: int
    patch: dict[str, Any] = Field(default_factory=dict)
    preview: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    status: str
    created_at: datetime
    confirmed_at: datetime | None = None


class StudentProfileRevisionAction(BaseModel):
    reason: str = Field(default="", max_length=500)


# =====================================================================
# Provider 配置
# =====================================================================

class ProviderInfo(BaseModel):
    """当前 provider 配置信息（不含 API Key）。"""
    provider: str
    display_name: str
    api_format: str
    base_url: str
    model_name: str
    api_key_configured: bool
    api_key_env: str
    agent_enabled: bool
    text_agent_enabled: bool
    vision_analysis_enabled: bool


class ProviderSwitchRequest(BaseModel):
    """切换 provider 请求。

    切换后需要重启 Agent 编排器才能生效。
    API Key（B3-10）：
    - api_key=None：不修改；
    - api_key=""：清除本地保管库中的 Key；
    - api_key 非空：写入本地保管库（0600 文件），此后 text_api_key 优先读取它。
    """
    provider: str = Field(description="deepseek / anthropic / openai_compat / zhipu")
    # 内部模型档案激活时一并传入，保证创建 Provider 的瞬间读取该档案的 Key，
    # 而不是沿用上一个模型档案的 Key。普通手动切换不传时会清除旧绑定。
    profile_id: str | None = None
    base_url: str | None = None
    model_name: str | None = None
    api_key_env: str | None = None
    api_key: str | None = None
    extra_headers: dict[str, str] = Field(default_factory=dict)
    auth_header: str | None = None
    auth_prefix: str | None = None
    context_length: int | None = None
    max_output_tokens: int | None = None
    thinking_enabled: bool | None = None
    reasoning_effort: str | None = None
    supports_reasoning: bool | None = None
    supports_tool_calls: bool | None = None
    supports_vision: bool | None = None


class ProviderTestRequest(BaseModel):
    """测试连接请求（B3-10）。未提供字段时使用当前配置（含保管库 Key）。"""
    provider: str | None = None
    base_url: str | None = None
    model_name: str | None = None
    api_key: str | None = None
    profile_id: str | None = None
    thinking_enabled: bool | None = None
    reasoning_effort: str | None = None
    # 默认发一个最小 chat completion；若只想验证可达性可传 false
    send_chat_request: bool = True


class ProviderTestResult(BaseModel):
    """测试连接结果（不保存模型回答）。"""
    ok: bool
    latency_ms: int | None = None
    message: str = ""
    provider: str | None = None
    model_name: str | None = None


class ProviderRuntimeStatus(BaseModel):
    """Harness/运行时运行状态（B3-10，不含敏感信息）。"""
    mode: str
    configured: bool
    running: bool
    pid: int | None = None
    restart_count: int = 0
    last_error: str | None = None
    provider: str | None = None
    model_name: str | None = None
    config_version: int | None = None
    key_configured: bool = False
    queue_depth: int = 0
    active_method: str | None = None
    runtime_generation: int = 0
    pool_size: int = 4
    active_slots: int = 0
    # P1-5：Harness 模式实际支持的供应商白名单（其余标记"暂未支持"）
    harness_supported_providers: list[str] = ["deepseek", "openai_compat"]
    harness_unsupported_providers: list[str] = ["anthropic"]


class ProviderSwitchResponse(BaseModel):
    """切换 provider 响应。"""
    success: bool
    message: str
    provider_info: ProviderInfo


class AvailableProvider(BaseModel):
    """可用 provider 描述。"""
    id: str
    display_name: str
    api_format: str
    default_base_url: str
    default_model: str
    default_api_key_env: str
    description: str
    # P1-5：Harness 模式是否支持；False 时页面必须禁用并标注"暂未支持"
    supported_in_harness: bool = True
    unsupported_reason: str = ""


class ModelProfile(BaseModel):
    """已保存模型档案（不含 API Key）。"""
    id: str
    display_name: str
    provider: str
    base_url: str
    endpoint: str
    model_name: str
    api_key_configured: bool = False
    api_key_env: str = ""
    context_length: int = 128_000
    max_output_tokens: int = 4_096
    supports_tool_calls: bool = True
    supports_vision: bool = False
    supports_reasoning: bool = False
    thinking_enabled: bool = True
    reasoning_effort: str = "high"
    builtin: bool = False


class ModelProfileRequest(BaseModel):
    """创建/更新模型档案；api_key 只在请求期间存在。"""
    id: str | None = None
    display_name: str = Field(min_length=1, max_length=120)
    provider: str = "openai_compat"
    base_url: str = ""
    endpoint: str = ""
    model_name: str = Field(min_length=1, max_length=200)
    api_key_env: str | None = None
    api_key: str | None = None
    context_length: int | None = None
    max_output_tokens: int | None = None
    supports_tool_calls: bool = True
    supports_vision: bool = False
    supports_reasoning: bool = False
    thinking_enabled: bool = True
    reasoning_effort: str = "high"
    apply: bool = True


class ModelProfilesResponse(BaseModel):
    models: list[ModelProfile]
    current_id: str = ""
