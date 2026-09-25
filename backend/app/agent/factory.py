"""Agent 工厂

从配置和环境变量构建 Agent 编排所需的全部组件：
- TextModelProvider (DeepSeek / Anthropic / OpenAI 兼容)
- ToolRegistry (内置只读工具)
- CapabilityRegistry (分析能力)
- AgentOrchestrator (编排器)

使用方式::

    from .factory import create_orchestrator
    orchestrator = create_orchestrator()
    response = await orchestrator.run(request, db_session=session)

支持的 provider 类型：
- "deepseek":      DeepSeek 官方 API（OpenAI 兼容格式）
- "anthropic":     Anthropic Claude 原生 API（Messages API）
- "openai_compat": 通用 OpenAI 兼容端点（CC Switch / OpenRouter / 等）
- "zhipu":         智谱 GLM 官方 OpenAI 兼容端点
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from .config import AgentConfig, get_agent_config
from .orchestrator import AgentOrchestrator
from .providers.deepseek_text import DeepSeekTextProvider
from .providers.anthropic_text import AnthropicTextProvider
from .providers.openai_compat_text import OpenAICompatTextProvider
from .providers.zhipu import ZhipuTextProvider
from .providers.vision import create_vision_provider
from .providers.base import ModelError, ModelErrorType
from .registry.capabilities import create_default_capability_registry
from .registry.tools import create_default_registry

if TYPE_CHECKING:
    from .providers.base import VisionModelProvider, TextModelProvider

logger = logging.getLogger(__name__)

# 单例缓存
_orchestrator_instance: AgentOrchestrator | None = None
# 异步锁，防止并发重复创建
_orchestrator_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    """获取或创建异步锁。"""
    global _orchestrator_lock
    if _orchestrator_lock is None:
        _orchestrator_lock = asyncio.Lock()
    return _orchestrator_lock


def _create_text_provider(config: AgentConfig) -> "TextModelProvider":
    """根据 config.text_provider 创建对应的文本模型适配器。

    :raises RuntimeError: API Key 未配置
    """
    api_key = config.text_api_key
    if not api_key:
        raise RuntimeError(
            f"API Key 未配置，请设置环境变量 {config.text_api_key_env}"
        )

    provider_type = config.text_provider

    if provider_type == "anthropic":
        logger.info("使用 Anthropic Claude provider: model=%s, base_url=%s",
                     config.text_model_name, config.text_api_base_url)
        return AnthropicTextProvider(
            api_key=api_key,
            base_url=config.text_api_base_url,
            default_model=config.text_model_name,
            timeout=config.model_timeout_seconds,
            max_retries=config.model_max_retries,
        )

    elif provider_type == "zhipu":
        logger.info("使用智谱 GLM provider: model=%s, base_url=%s",
                    config.text_model_name, config.text_api_base_url)
        return ZhipuTextProvider(
            api_key=api_key,
            base_url=config.text_api_base_url,
            default_model=config.text_model_name,
            timeout=config.model_timeout_seconds,
            max_retries=config.model_max_retries,
            auth_header=config.text_auth_header,
            auth_prefix=config.text_auth_prefix,
            extra_headers=config.text_extra_headers,
            context_length=config.text_context_length,
            max_output_tokens=config.text_max_output_tokens,
            supports_vision=config.text_supports_vision,
            supports_tool_calls=config.text_supports_tool_calls,
            thinking_enabled=config.text_thinking_enabled,
            reasoning_effort=config.text_reasoning_effort,
        )

    elif provider_type == "openai_compat":
        logger.info("使用 OpenAI 兼容 provider: model=%s, base_url=%s",
                     config.text_model_name, config.text_api_base_url)
        return OpenAICompatTextProvider(
            api_key=api_key,
            base_url=config.text_api_base_url,
            default_model=config.text_model_name,
            timeout=config.model_timeout_seconds,
            max_retries=config.model_max_retries,
            auth_header=config.text_auth_header,
            auth_prefix=config.text_auth_prefix,
            extra_headers=config.text_extra_headers,
            context_length=config.text_context_length,
            max_output_tokens=config.text_max_output_tokens,
            thinking_enabled=config.text_thinking_enabled,
            reasoning_effort=config.text_reasoning_effort,
            supports_reasoning=config.text_supports_reasoning,
            supports_vision=config.text_supports_vision,
            supports_tool_calls=config.text_supports_tool_calls,
        )

    elif provider_type == "deepseek":
        logger.info("使用 DeepSeek provider: model=%s, base_url=%s",
                     config.text_model_name, config.text_api_base_url)
        return DeepSeekTextProvider(
            api_key=api_key,
            base_url=config.text_api_base_url,
            default_model=config.text_model_name,
            timeout=config.model_timeout_seconds,
            max_retries=config.model_max_retries,
            thinking_enabled=config.text_thinking_enabled,
            reasoning_effort=config.text_reasoning_effort,
        )

    else:
        logger.warning("未知 provider 类型 '%s'，回退到 OpenAI 兼容模式", provider_type)
        return OpenAICompatTextProvider(
            api_key=api_key,
            base_url=config.text_api_base_url,
            default_model=config.text_model_name,
            timeout=config.model_timeout_seconds,
            max_retries=config.model_max_retries,
        )


def create_orchestrator(
    config: AgentConfig | None = None,
    *,
    vision_provider: "VisionModelProvider | None" = None,
) -> AgentOrchestrator:
    """创建 Agent 编排器。

    从环境变量读取 API Key，根据 config.text_provider 创建对应适配器。

    :param config: Agent 配置，默认从 get_agent_config() 获取
    :param vision_provider: 视觉模型供应商（可选）
    :raises RuntimeError: API Key 未配置
    """
    global _orchestrator_instance

    if config is None:
        config = get_agent_config()

    text_provider = _create_text_provider(config)

    tool_registry = create_default_registry()
    capability_registry = create_default_capability_registry()

    # Vision is optional: with no configured vision key the factory keeps the
    # text-only agent usable, while configured OpenAI-compatible/Tencent OCR
    # providers are made available to attachment-aware flows.
    if vision_provider is None:
        vision_provider = create_vision_provider(config)

    orchestrator = AgentOrchestrator(
        config=config,
        text_provider=text_provider,
        vision_provider=vision_provider,
        tool_registry=tool_registry,
        capability_registry=capability_registry,
    )

    _orchestrator_instance = orchestrator
    return orchestrator


def get_orchestrator() -> AgentOrchestrator | None:
    """获取已创建的编排器单例（可能为 None）。"""
    return _orchestrator_instance


async def get_or_create_orchestrator(
    config: AgentConfig | None = None,
    *,
    vision_provider: "VisionModelProvider | None" = None,
) -> AgentOrchestrator:
    """获取或创建编排器单例。

    使用异步锁防止并发重复创建。
    如果已有实例，复用；否则创建新实例。
    """
    global _orchestrator_instance

    if _orchestrator_instance is not None and config is None:
        return _orchestrator_instance

    async with _get_lock():
        # 双重检查
        if _orchestrator_instance is not None and config is None:
            return _orchestrator_instance

        # 如果有旧实例且要切换 config，先关闭旧的
        if _orchestrator_instance is not None and config is not None:
            await _close_provider(_orchestrator_instance)
            _orchestrator_instance = None

        return create_orchestrator(config, vision_provider=vision_provider)


async def _close_provider(orchestrator: AgentOrchestrator) -> None:
    """关闭编排器中的 provider 客户端。

    通过公开方法 close() 完成生命周期管理，
    不访问 _text_provider 等私有字段。
    """
    await orchestrator.close()


async def close_orchestrator() -> None:
    """关闭编排器，释放资源（如 httpx client）。

    通过公开方法 close() 完成生命周期管理。
    """
    global _orchestrator_instance
    if _orchestrator_instance is not None:
        try:
            await _orchestrator_instance.close()
        except RuntimeError as exc:
            # TestClient/多 portal 场景下，provider client 可能绑定到已关闭的
            # 请求事件循环；资源已不可再 await，直接丢弃单例，避免阻塞应用 shutdown。
            if "Event loop is closed" not in str(exc):
                raise
            logger.warning("编排器 provider 所属事件循环已关闭，跳过异步清理")
        finally:
            _orchestrator_instance = None


def get_provider_info(config: AgentConfig | None = None) -> dict:
    """获取当前 provider 的配置信息（不包含 API Key）。

    用于前端显示当前使用的 provider 类型和状态。
    """
    if config is None:
        config = get_agent_config()

    return {
        "provider": config.text_provider,
        "display_name": config.provider_display_name,
        "api_format": config.provider_api_format,
        "base_url": config.text_api_base_url,
        "model_name": config.text_model_name,
        "profile_id": config.text_model_profile_id,
        "api_key_configured": config.text_api_key_configured,
        "api_key_env": config.text_api_key_env,
        "agent_enabled": config.agent_enabled,
        "text_agent_enabled": config.is_feature_enabled("text_agent_enabled"),
        "vision_analysis_enabled": config.is_feature_enabled("vision_analysis_enabled"),
    }
