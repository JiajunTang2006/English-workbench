"""
Agent 配置模型

管理 Agent 的全部配置项，包括：
- 功能开关（是否启用 Agent 及子能力）
- 文本/视觉模型供应商配置
- API Base URL、模型名、超时和重试
- 单次预算软上限
- PDF 页数和附件大小限制
- Agent 最大迭代和并行工具数
- 分析规则版本

安全要求：
- API Key 只能来自环境变量或操作系统安全存储
- 配置读取不得把 Key 打印到日志
- 前端设置接口只能返回"已配置/未配置"，不能返回 Key
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# 功能开关默认值
# 所有未达到发布门禁的能力默认关闭。
# 关闭 Agent 后原有工作台完全不受影响。
# ---------------------------------------------------------------------------

DEFAULT_FEATURE_FLAGS: dict[str, bool] = {
    "agent_enabled": False,
    "exam_ingestion_enabled": False,
    "text_agent_enabled": False,
    "vision_analysis_enabled": False,
    "background_worker_enabled": False,
    "system_notifications_enabled": False,
    # --- 长期开发方案 L0 新增（L1-L4 分阶段能力，全部默认关闭）---
    # L1：前端 Multipart 流式上传。关闭时前端回退 Base64 兼容入口。
    "multipart_ui_enabled": False,
    # L2：Codex 只读插件（MCP Server + 配对令牌）。
    "codex_plugin_enabled": False,
    # L4：本地 PDF/DOCX 文档导出。
    "document_export_enabled": False,
    # L4-C：第三方文档 Provider（需先开 document_export_enabled）。
    "remote_document_provider_enabled": False,
}

# 阶段能力开关与所属里程碑（供设置页分组展示与发布门禁校验）。
FEATURE_FLAG_MILESTONES: dict[str, str] = {
    "multipart_ui_enabled": "L1",
    "codex_plugin_enabled": "L2",
    "vision_analysis_enabled": "L3",
    "document_export_enabled": "L4",
    "remote_document_provider_enabled": "L4",
}

# 依赖关系：值中的开关未开启时，键开关必须视为关闭（fail-closed）。
FEATURE_FLAG_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "remote_document_provider_enabled": ("document_export_enabled",),
}


# ---------------------------------------------------------------------------
# 默认参数（对应实施方案第 22 节"默认配置与后续决策"）
# 这些值不作为不可变教学结论，可由设置页修改。
# ---------------------------------------------------------------------------

DEFAULT_BOUNDARY_BAND_PERCENT = 0.05       # 临界带宽 = 考试满分 × 5%
DEFAULT_REGRESSION_WINDOW = 3              # 退步观察窗口 = 最近 3 次有效考试
DEFAULT_SCORE_RATE_CHANGE_THRESHOLD = 0.05  # 得分率显著变化 = 5 个百分点
DEFAULT_COMMON_PROBLEM_MIN_RATIO = 0.20    # 共同问题最低影响比例 = 20%
DEFAULT_COMMON_PROBLEM_MIN_COUNT = 5       # 共同问题最低影响人数 = 5
DEFAULT_COMMON_PROBLEM_MIN_ERROR_RATE = 0.30  # 共同问题最低错误率 = 30%

DEFAULT_AGENT_MAX_ITERATIONS = 4           # 单轮 Agent 最大迭代
DEFAULT_AGENT_MAX_PARALLEL_TOOLS = 4       # 单轮并行工具数
DEFAULT_PDF_MAX_PAGES = 20                 # PDF 最大页数
DEFAULT_BUDGET_SOFT_LIMIT_YUAN = 0.5       # 单次预算软上限（元）
DEFAULT_MODEL_TIMEOUT_SECONDS = 420        # 模型调用超时（7 分钟；Harness 深度思考 + 工具调用常超 2-5 分钟）
DEFAULT_MODEL_MAX_RETRIES = 3              # 模型调用最大重试次数


def _env_bool(name: str, default: bool) -> bool:
    """读取严格布尔环境变量，避免 ``bool("false")`` 之类的误判。"""
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} 必须是 true/false、1/0、yes/no 或 on/off")


def _apply_feature_flag_dependencies(flags: dict[str, bool]) -> dict[str, bool]:
    """按 ``FEATURE_FLAG_DEPENDENCIES`` 做 fail-closed 关闭。

    某开关依赖的前置开关未启用时，该开关强制视为关闭，避免出现
    "开了远端文档 Provider 但没开文档导出" 这类不一致状态。
    """
    resolved = dict(flags)
    for flag, requires in FEATURE_FLAG_DEPENDENCIES.items():
        if resolved.get(flag) and not all(resolved.get(dep) for dep in requires):
            resolved[flag] = False
    return resolved


@dataclass(frozen=True)
class AgentConfig:
    """Agent 全局配置（只读快照）。

    配置来源优先级：
    1. 环境变量（最高优先级，用于 API Key 等敏感信息）
    2. 数据库 agent_analysis_settings 表（运行时可修改）
    3. 本 dataclass 的默认值
    """

    # --- 功能开关 ---
    feature_flags: dict[str, bool] = field(
        default_factory=lambda: dict(DEFAULT_FEATURE_FLAGS)
    )

    # --- 文本模型配置 ---
    # provider_type 决定使用哪种 API 格式：
    #   "deepseek"       → DeepSeek 官方 API（OpenAI 兼容）
    #   "anthropic"      → Anthropic Claude 原生 API（Messages API）
    #   "openai_compat"  → 通用 OpenAI 兼容端点（CC Switch / OpenRouter / 等）
    #   "zhipu"          → 智谱 GLM 官方 OpenAI 兼容端点
    text_provider: str = "deepseek"
    text_api_base_url: str = "https://api.deepseek.com"
    text_model_name: str = "deepseek-chat"
    # 当前使用的已保存模型档案 ID；空字符串表示旧版单配置。
    text_model_profile_id: str = ""
    # 推理配置：DeepSeek 使用 thinking + reasoning_effort；兼容接口使用
    # reasoning_effort（仅在模型声明支持时发送）。
    text_thinking_enabled: bool = True
    text_reasoning_effort: str = "high"
    text_supports_reasoning: bool = True
    text_supports_tool_calls: bool = True
    text_supports_vision: bool = False
    text_api_key_env: str = "DEEPSEEK_API_KEY"  # 环境变量名，不存储 Key 本身
    # 额外 header（如 CC Switch 代理可能需要自定义 header）
    text_extra_headers: dict[str, str] = field(default_factory=dict)
    # 自定义鉴权（默认 Authorization: Bearer）
    text_auth_header: str = "Authorization"
    text_auth_prefix: str = "Bearer"
    # 模型能力声明（可被 provider 覆盖）
    text_context_length: int = 128_000
    text_max_output_tokens: int = 4_096

    # --- 视觉模型配置 ---
    vision_provider: str = ""  # 空字符串表示未配置
    vision_api_base_url: str = ""
    vision_model_name: str = ""
    vision_api_key_env: str = "VISION_API_KEY"

    # --- 预算与限制 ---
    budget_soft_limit_yuan: float = DEFAULT_BUDGET_SOFT_LIMIT_YUAN
    pdf_max_pages: int = DEFAULT_PDF_MAX_PAGES
    model_timeout_seconds: int = DEFAULT_MODEL_TIMEOUT_SECONDS
    model_max_retries: int = DEFAULT_MODEL_MAX_RETRIES

    # --- Agent 循环参数 ---
    max_iterations: int = DEFAULT_AGENT_MAX_ITERATIONS
    max_parallel_tools: int = DEFAULT_AGENT_MAX_PARALLEL_TOOLS

    # --- 分析规则参数 ---
    boundary_band_percent: float = DEFAULT_BOUNDARY_BAND_PERCENT
    regression_window: int = DEFAULT_REGRESSION_WINDOW
    score_rate_change_threshold: float = DEFAULT_SCORE_RATE_CHANGE_THRESHOLD
    common_problem_min_ratio: float = DEFAULT_COMMON_PROBLEM_MIN_RATIO
    common_problem_min_count: int = DEFAULT_COMMON_PROBLEM_MIN_COUNT
    common_problem_min_error_rate: float = DEFAULT_COMMON_PROBLEM_MIN_ERROR_RATE

    # --- 规则版本（修改规则不改变历史报告）---
    rules_version: str = "v1.0.0"

    # --- 配置版本号（每次 provider 切换/配置变更递增，绑定到 AnalysisRun）---
    config_version: int = 1

    # --- 人民币换算率（不硬编码实时汇率）---
    cny_usd_rate: float = 7.2  # 1 USD ≈ 7.2 CNY，可配置

    @property
    def agent_enabled(self) -> bool:
        """Agent 总开关是否启用。"""
        return self.feature_flags.get("agent_enabled", False)

    @property
    def text_api_key(self) -> str | None:
        """读取当前模型档案对应的 Key，再回退到默认保管库和环境变量。"""
        try:
            from .keyvault import load_api_key
            if self.text_model_profile_id and self.text_model_profile_id != "legacy-current":
                profile_key = load_api_key(self.text_model_profile_id)
                if profile_key:
                    return profile_key
            vaulted = load_api_key()
            if vaulted:
                return vaulted
        except Exception:
            pass
        return os.getenv(self.text_api_key_env)

    @property
    def text_api_key_configured(self) -> bool:
        """文本模型 API Key 是否已配置（前端只返回布尔值，不返回 Key 本身）。"""
        return bool(self.text_api_key)

    @property
    def vision_api_key(self) -> str | None:
        """从环境变量读取视觉模型 API Key。"""
        configured = os.getenv(self.vision_api_key_env)
        if configured:
            return configured
        # 同一模型档案同时承担文本和视觉能力时，复用本地保管库中的 Key。
        # 这样在设置页只填写一次智谱 Key，也能驱动图片解析链路。
        if self.vision_provider and self.vision_provider == self.text_provider:
            return self.text_api_key
        return None

    @property
    def vision_api_key_configured(self) -> bool:
        """视觉模型 API Key 是否已配置。"""
        return bool(self.vision_api_key)

    def is_feature_enabled(self, flag_name: str) -> bool:
        """查询某个功能开关是否启用。

        如果 agent_enabled 为 False，所有子能力都视为关闭。
        """
        if not self.agent_enabled and flag_name != "agent_enabled":
            return False
        return self.feature_flags.get(flag_name, False)

    @property
    def provider_display_name(self) -> str:
        """当前 provider 的显示名称。"""
        names = {
            "deepseek": "DeepSeek",
            "anthropic": "Anthropic Claude",
            "openai_compat": "OpenAI 兼容",
            "zhipu": "智谱 GLM",
        }
        return names.get(self.text_provider, self.text_provider)

    @property
    def provider_api_format(self) -> str:
        """当前 provider 的 API 格式标识。"""
        # deepseek 本质上是 OpenAI 兼容格式
        if self.text_provider == "deepseek":
            return "openai"
        elif self.text_provider == "anthropic":
            return "anthropic"
        else:
            return "openai"


# ---------------------------------------------------------------------------
# 运行时持久化配置覆盖
#
# P0-2: 统一 Provider 配置来源 + 数据库持久化。
# switch_provider() 设置覆盖后，get_agent_config() 全链路返回同一配置快照。
# 配置变更持久化到 agent_analysis_settings 表，重启后自动恢复。
# API Key 仍从环境变量实时读取（通过 text_api_key 属性）。
# 每次 set_runtime_config 递增 config_version，绑定到 AnalysisRun 审计。
# ---------------------------------------------------------------------------

_runtime_override: AgentConfig | None = None

# 配置持久化在 DB 中的 key
_CONFIG_DB_KEY = "agent_runtime_config"
_MODEL_PROFILES_DB_KEY = "agent_model_profiles"


def _serialize_config(config: AgentConfig) -> dict:
    """将 AgentConfig 序列化为可存入 JSON 的字典（不含 API Key）。"""
    return {
        "feature_flags": dict(config.feature_flags),
        "text_provider": config.text_provider,
        "text_api_base_url": config.text_api_base_url,
        "text_model_name": config.text_model_name,
        "text_model_profile_id": config.text_model_profile_id,
        "text_thinking_enabled": config.text_thinking_enabled,
        "text_reasoning_effort": config.text_reasoning_effort,
        "text_supports_reasoning": config.text_supports_reasoning,
        "text_supports_tool_calls": config.text_supports_tool_calls,
        "text_supports_vision": config.text_supports_vision,
        "text_api_key_env": config.text_api_key_env,
        "text_extra_headers": dict(config.text_extra_headers),
        "text_auth_header": config.text_auth_header,
        "text_auth_prefix": config.text_auth_prefix,
        "text_context_length": config.text_context_length,
        "text_max_output_tokens": config.text_max_output_tokens,
        "vision_provider": config.vision_provider,
        "vision_api_base_url": config.vision_api_base_url,
        "vision_model_name": config.vision_model_name,
        "vision_api_key_env": config.vision_api_key_env,
        "budget_soft_limit_yuan": config.budget_soft_limit_yuan,
        "pdf_max_pages": config.pdf_max_pages,
        "model_timeout_seconds": config.model_timeout_seconds,
        "model_max_retries": config.model_max_retries,
        "max_iterations": config.max_iterations,
        "max_parallel_tools": config.max_parallel_tools,
        "boundary_band_percent": config.boundary_band_percent,
        "regression_window": config.regression_window,
        "score_rate_change_threshold": config.score_rate_change_threshold,
        "common_problem_min_ratio": config.common_problem_min_ratio,
        "common_problem_min_count": config.common_problem_min_count,
        "common_problem_min_error_rate": config.common_problem_min_error_rate,
        "rules_version": config.rules_version,
        "config_version": config.config_version,
        "cny_usd_rate": config.cny_usd_rate,
    }


def _deserialize_config(data: dict) -> AgentConfig:
    """从序列化字典重建 AgentConfig。"""
    from dataclasses import replace
    # 只取 dataclass 已知字段，忽略多余 key
    return replace(AgentConfig(), **{
        k: v for k, v in data.items()
        if k in AgentConfig.__dataclass_fields__
    })


def _persist_config_to_db(config: AgentConfig) -> None:
    """将运行时配置持久化到 agent_analysis_settings 表。

    使用全局 session factory 访问 DB。如果 DB 尚未初始化则静默跳过（测试环境）。
    """
    try:
        from ..database import get_global_session_factory
        from ..models.agent_entities import AgentAnalysisSetting
        from datetime import datetime, timezone

        factory = get_global_session_factory()
        if factory is None:
            return

        serialized = _serialize_config(config)
        with factory() as db:
            existing = db.get(AgentAnalysisSetting, _CONFIG_DB_KEY)
            if existing is not None:
                existing.value_json = serialized
                existing.rules_version = config.rules_version
                existing.updated_at = datetime.now(timezone.utc)
            else:
                row = AgentAnalysisSetting(
                    key=_CONFIG_DB_KEY,
                    value_json=serialized,
                    rules_version=config.rules_version,
                )
                db.add(row)
            db.commit()
    except Exception:
        # DB 未初始化或表不存在时静默跳过（测试环境常见）
        pass


def _load_config_from_db() -> AgentConfig | None:
    """从 agent_analysis_settings 表加载持久化的运行时配置。

    返回 None 表示无持久化配置或 DB 不可用。
    """
    try:
        from ..database import get_global_session_factory
        from ..models.agent_entities import AgentAnalysisSetting

        factory = get_global_session_factory()
        if factory is None:
            return None

        with factory() as db:
            row = db.get(AgentAnalysisSetting, _CONFIG_DB_KEY)
            if row is None or not row.value_json:
                return None
            return _deserialize_config(row.value_json)
    except Exception:
        return None


def _clear_config_from_db() -> None:
    """从 DB 删除持久化的运行时配置。"""
    try:
        from ..database import get_global_session_factory
        from ..models.agent_entities import AgentAnalysisSetting

        factory = get_global_session_factory()
        if factory is None:
            return

        with factory() as db:
            row = db.get(AgentAnalysisSetting, _CONFIG_DB_KEY)
            if row is not None:
                db.delete(row)
                db.commit()
    except Exception:
        pass


def load_model_profiles() -> list[dict]:
    """读取模型档案元数据；API Key 永远不在这些 JSON 中。"""
    try:
        from ..database import get_global_session_factory
        from ..models.agent_entities import AgentAnalysisSetting
        factory = get_global_session_factory()
        if factory is None:
            return []
        with factory() as db:
            row = db.get(AgentAnalysisSetting, _MODEL_PROFILES_DB_KEY)
            if row is None or not isinstance(row.value_json, list):
                return []
            return [dict(item) for item in row.value_json if isinstance(item, dict)]
    except Exception:
        return []


def save_model_profiles(profiles: list[dict]) -> None:
    """保存模型档案元数据；调用方负责把 Key 写入 keyvault。"""
    try:
        from ..database import get_global_session_factory
        from ..models.agent_entities import AgentAnalysisSetting
        from datetime import datetime, timezone
        factory = get_global_session_factory()
        if factory is None:
            return
        with factory() as db:
            row = db.get(AgentAnalysisSetting, _MODEL_PROFILES_DB_KEY)
            if row is None:
                db.add(AgentAnalysisSetting(key=_MODEL_PROFILES_DB_KEY, value_json=profiles))
            else:
                row.value_json = profiles
                row.updated_at = datetime.now(timezone.utc)
            db.commit()
    except Exception:
        # 与旧配置持久化保持一致：测试环境/迁移期间不阻塞主流程。
        pass


def _next_config_version() -> int:
    """获取下一个配置版本号（基于当前 DB 中持久化的版本 +1）。"""
    current = _load_config_from_db()
    if current is not None:
        return current.config_version + 1
    # 检查内存中的 override
    if _runtime_override is not None:
        return _runtime_override.config_version + 1
    return 1


def set_runtime_config(config: AgentConfig, persist: bool = True) -> None:
    """设置运行时配置覆盖。

    设置后，get_agent_config() 将返回此配置而非重新读取环境变量。
    用于 provider 切换等运行时配置变更。

    参数:
        config: 要设置的配置
        persist: 是否持久化到数据库（默认 True，测试时可设为 False）
    """
    global _runtime_override
    _runtime_override = config
    if persist:
        _persist_config_to_db(config)


def clear_runtime_config(persist: bool = True) -> None:
    """清除运行时配置覆盖，恢复到环境变量读取模式。

    参数:
        persist: 是否同时从数据库删除持久化配置（默认 True）
    """
    global _runtime_override
    _runtime_override = None
    if persist:
        _clear_config_from_db()


def get_runtime_config() -> AgentConfig | None:
    """获取当前运行时覆盖配置（可能为 None）。"""
    return _runtime_override


def restore_runtime_config_from_db() -> bool:
    """从数据库恢复运行时配置覆盖。

    在应用启动时调用。如果 DB 中有持久化配置，加载到内存。
    返回 True 表示成功恢复，False 表示无持久化配置。
    """
    global _runtime_override
    db_config = _load_config_from_db()
    if db_config is not None:
        # 2026-09 migration: early GLM-5.3 profiles in the Chinese build used
        # the international Z.AI endpoint.  BigModel id.secret credentials are
        # not valid in that separate credential domain, so migrate the exact
        # old default before any provider can transmit the key.
        if db_config.text_provider == "zhipu" and db_config.text_api_key:
            from dataclasses import replace
            from .providers.zhipu import resolve_zhipu_base_url

            resolved_base_url = resolve_zhipu_base_url(
                db_config.text_api_base_url, db_config.text_api_key
            )
            if resolved_base_url != db_config.text_api_base_url:
                vision_base_url = db_config.vision_api_base_url
                if db_config.vision_provider == "zhipu":
                    vision_base_url = resolve_zhipu_base_url(
                        vision_base_url, db_config.text_api_key
                    )
                db_config = replace(
                    db_config,
                    text_api_base_url=resolved_base_url,
                    vision_api_base_url=vision_base_url,
                    config_version=db_config.config_version + 1,
                )
                _persist_config_to_db(db_config)
        # 兼容旧版本：设置页已经保存了可用的 API Key，但旧版 provider
        # 切换不会同步打开 Agent 开关，导致重启后前端一直处于 disabled。
        # 只有确认 Key 存在时才迁移开关，避免在未配置模型时误启用 Agent。
        if db_config.text_api_key and not (
            db_config.agent_enabled and db_config.is_feature_enabled("text_agent_enabled")
        ):
            from dataclasses import replace

            feature_flags = dict(db_config.feature_flags)
            feature_flags["agent_enabled"] = True
            feature_flags["text_agent_enabled"] = True
            db_config = replace(db_config, feature_flags=feature_flags)
            _persist_config_to_db(db_config)
        # 兼容旧版本：历史持久化的 model_timeout_seconds 可能仍是 120s
        # （Harness 深度思考 + 工具调用下频繁触发回合超时）。低于当前
        # 默认值（300s）时自动提升，避免"改默认值但 DB 旧值覆盖"的坑。
        if db_config.model_timeout_seconds < DEFAULT_MODEL_TIMEOUT_SECONDS:
            from dataclasses import replace

            db_config = replace(
                db_config,
                model_timeout_seconds=DEFAULT_MODEL_TIMEOUT_SECONDS,
                config_version=db_config.config_version + 1,
            )
            _persist_config_to_db(db_config)
        # L0：历史持久化配置不含 L1-L4 新增开关。补齐缺失键（取默认关闭）
        # 并应用依赖 fail-closed，保证设置页能完整展示阶段开关。
        merged_flags = {**DEFAULT_FEATURE_FLAGS, **dict(db_config.feature_flags)}
        merged_flags = _apply_feature_flag_dependencies(merged_flags)
        if merged_flags != dict(db_config.feature_flags):
            from dataclasses import replace

            db_config = replace(db_config, feature_flags=merged_flags)
        _runtime_override = db_config
        return True
    return False


def get_agent_config() -> AgentConfig:
    """获取当前 Agent 配置。

    配置来源优先级：
    1. 运行时持久化覆盖（由 switch_provider 设置，全链路共用）
    2. 环境变量（用于 API Key 等敏感信息和功能开关）
    3. 本 dataclass 的默认值

    当存在运行时覆盖时，所有调用方（send_message、estimate、run_executor、
    provider info）都返回同一版本化配置，避免"实际调用模型、费用估算模型、
    用量记录模型"不一致。

    支持的环境变量：
    - AGENT_ENABLED / AGENT_TEXT_ENABLED: Agent 总开关与文本能力开关
    - AGENT_EXAM_INGESTION_ENABLED / AGENT_VISION_ANALYSIS_ENABLED
    - AGENT_BACKGROUND_WORKER_ENABLED / AGENT_SYSTEM_NOTIFICATIONS_ENABLED
    - AGENT_TEXT_PROVIDER: deepseek / anthropic / openai_compat / zhipu
    - AGENT_TEXT_API_BASE_URL: API 基础 URL
    - AGENT_TEXT_MODEL_NAME: 默认模型名
    - AGENT_TEXT_API_KEY_ENV: API Key 所在的环境变量名
    - DEEPSEEK_API_KEY / ANTHROPIC_API_KEY / AGENT_API_KEY: API Key
    - VISION_PROVIDER / VISION_API_BASE_URL / VISION_MODEL_NAME / VISION_API_KEY_ENV
      以及对应的 AGENT_VISION_* 别名：视觉模型配置
    """
    # P0-A: 如果存在运行时覆盖，直接返回（全链路共用同一配置快照）
    if _runtime_override is not None:
        return _runtime_override

    return _build_config_from_env()


def _build_config_from_env() -> AgentConfig:
    """从环境变量构建配置（不含运行时覆盖）。"""
    import os

    # 从环境变量读取 provider 配置（覆盖默认值）
    provider = os.getenv("AGENT_TEXT_PROVIDER", "")
    base_url = os.getenv("AGENT_TEXT_API_BASE_URL", "")
    model_name = os.getenv("AGENT_TEXT_MODEL_NAME", "")
    api_key_env = os.getenv("AGENT_TEXT_API_KEY_ENV", "")
    vision_provider = os.getenv("AGENT_VISION_PROVIDER", os.getenv("VISION_PROVIDER", ""))
    vision_base_url = os.getenv("AGENT_VISION_API_BASE_URL", os.getenv("VISION_API_BASE_URL", ""))
    vision_model_name = os.getenv("AGENT_VISION_MODEL_NAME", os.getenv("VISION_MODEL_NAME", ""))
    vision_api_key_env = os.getenv("AGENT_VISION_API_KEY_ENV", os.getenv("VISION_API_KEY_ENV", ""))

    flag_env_names = {
        "agent_enabled": "AGENT_ENABLED",
        "text_agent_enabled": "AGENT_TEXT_ENABLED",
        "exam_ingestion_enabled": "AGENT_EXAM_INGESTION_ENABLED",
        "vision_analysis_enabled": "AGENT_VISION_ANALYSIS_ENABLED",
        "background_worker_enabled": "AGENT_BACKGROUND_WORKER_ENABLED",
        "system_notifications_enabled": "AGENT_SYSTEM_NOTIFICATIONS_ENABLED",
        "multipart_ui_enabled": "AGENT_MULTIPART_UI_ENABLED",
        "codex_plugin_enabled": "AGENT_CODEX_PLUGIN_ENABLED",
        "document_export_enabled": "AGENT_DOCUMENT_EXPORT_ENABLED",
        "remote_document_provider_enabled": "AGENT_REMOTE_DOCUMENT_PROVIDER_ENABLED",
    }
    feature_flags = {
        flag: _env_bool(env_name, default=DEFAULT_FEATURE_FLAGS[flag])
        for flag, env_name in flag_env_names.items()
    }
    feature_flags = _apply_feature_flag_dependencies(feature_flags)

    # 构建 config，合并环境变量覆盖
    overrides: dict = {"feature_flags": feature_flags}

    if provider:
        overrides["text_provider"] = provider
        # 根据 provider 设置默认的 base_url / model / api_key_env
        if provider == "anthropic" and not base_url:
            overrides["text_api_base_url"] = "https://api.anthropic.com"
        elif provider == "openai_compat" and not base_url:
            overrides["text_api_base_url"] = os.getenv("AGENT_API_BASE_URL", "")
        elif provider == "zhipu" and not base_url:
            overrides["text_api_base_url"] = "https://open.bigmodel.cn/api/paas/v4"
        if provider == "zhipu" and not model_name:
            overrides["text_model_name"] = "glm-5.3-flash"
        if not api_key_env:
            if provider == "anthropic":
                overrides["text_api_key_env"] = "ANTHROPIC_API_KEY"
            elif provider == "openai_compat":
                overrides["text_api_key_env"] = "AGENT_API_KEY"
            elif provider == "zhipu":
                overrides["text_api_key_env"] = "ZHIPU_API_KEY"

    if base_url:
        overrides["text_api_base_url"] = base_url
    if model_name:
        overrides["text_model_name"] = model_name
    if api_key_env:
        overrides["text_api_key_env"] = api_key_env
    if vision_provider:
        overrides["vision_provider"] = vision_provider
    if vision_base_url:
        overrides["vision_api_base_url"] = vision_base_url
    if vision_model_name:
        overrides["vision_model_name"] = vision_model_name
    if vision_api_key_env:
        overrides["vision_api_key_env"] = vision_api_key_env

    # 支持环境变量覆盖模型超时与重试（用于调整 Harness 单回合时间）
    raw_timeout = os.getenv("AGENT_MODEL_TIMEOUT_SECONDS")
    if raw_timeout:
        try:
            overrides["model_timeout_seconds"] = max(60, min(int(raw_timeout), 600))
        except ValueError:
            pass
    raw_retries = os.getenv("AGENT_MODEL_MAX_RETRIES")
    if raw_retries:
        try:
            overrides["model_max_retries"] = max(0, min(int(raw_retries), 6))
        except ValueError:
            pass

    # frozen=True 的 dataclass 需要用 dataclasses.replace
    from dataclasses import replace
    return replace(AgentConfig(), **overrides)
