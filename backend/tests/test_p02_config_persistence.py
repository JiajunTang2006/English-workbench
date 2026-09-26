"""P0-2: Provider 配置持久化与原子切换测试

验证：
1. config_version 每次切换 provider 时递增
2. 运行时配置持久化到 agent_analysis_settings 表
3. 重启后从 DB 恢复配置
4. AnalysisRun 绑定 config_version
5. 原子回滚恢复旧 config_version
6. clear_runtime_config 同时清除 DB
"""

import pytest
from dataclasses import replace
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.database import set_global_session_factory, get_global_session_factory
from backend.app.models.agent_entities import AgentAnalysisSetting
from backend.app.agent.config import (
    AgentConfig,
    set_runtime_config,
    clear_runtime_config,
    get_runtime_config,
    get_agent_config,
    restore_runtime_config_from_db,
    _serialize_config,
    _deserialize_config,
    _next_config_version,
    _persist_config_to_db,
    _load_config_from_db,
    _clear_config_from_db,
)


@pytest.fixture(autouse=True)
def cleanup_runtime_config():
    """每个测试前后清理运行时配置。"""
    clear_runtime_config(persist=False)
    yield
    clear_runtime_config(persist=False)


@pytest.fixture
def db_factory():
    """创建内存 DB 并设置全局 session factory，测试后恢复。"""
    from backend.app.database import Base
    # 导入所有模型确保表创建
    from backend.app import models  # noqa: F401
    from backend.app.models import agent_entities  # noqa: F401

    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    old_factory = get_global_session_factory()
    set_global_session_factory(factory)
    yield factory
    set_global_session_factory(old_factory)
    engine.dispose()


class TestConfigSerialization:
    """配置序列化/反序列化测试。"""

    def test_serialize_deserialize_roundtrip(self):
        """序列化再反序列化应得到等价配置。"""
        original = AgentConfig(
            text_provider="anthropic",
            text_model_name="claude-sonnet-4-20250514",
            text_api_base_url="https://api.anthropic.com",
            config_version=42,
        )
        serialized = _serialize_config(original)
        assert serialized["text_provider"] == "anthropic"
        assert serialized["config_version"] == 42
        assert "text_api_key" not in serialized  # API Key 不被序列化

        restored = _deserialize_config(serialized)
        assert restored.text_provider == "anthropic"
        assert restored.text_model_name == "claude-sonnet-4-20250514"
        assert restored.config_version == 42

    def test_serialize_excludes_api_key(self):
        """序列化结果只含环境变量名，不含 API Key 本身。"""
        config = AgentConfig(text_provider="deepseek")
        serialized = _serialize_config(config)
        # 只允许 *_api_key_env（环境变量名），不允许存储 key 本身
        for key in serialized:
            if "api_key" in key.lower():
                assert key.endswith("_env"), f"字段 {key} 应是环境变量名（_env 后缀），不是 key 本身"
        # 不应有 text_api_key 或 vision_api_key 这种裸 key 字段
        assert "text_api_key" not in serialized
        assert "vision_api_key" not in serialized

    def test_deserialize_ignores_extra_keys(self):
        """反序列化时忽略 dataclass 没有的字段。"""
        data = _serialize_config(AgentConfig())
        data["unknown_field"] = "ignored"
        data["another_extra"] = 123
        restored = _deserialize_config(data)
        assert restored.text_provider == "deepseek"  # 默认值


class TestConfigVersionIncrement:
    """config_version 递增逻辑测试。"""

    def test_next_version_from_none(self, db_factory):
        """无任何配置时，next version = 1。"""
        clear_runtime_config(persist=False)
        _clear_config_from_db()
        assert _next_config_version() == 1

    def test_next_version_from_override(self, db_factory):
        """有内存 override 时，next version = override.version + 1。"""
        config = AgentConfig(config_version=5)
        set_runtime_config(config, persist=False)
        try:
            assert _next_config_version() == 6
        finally:
            clear_runtime_config(persist=False)

    def test_next_version_from_db(self, db_factory):
        """有 DB 持久化配置时，next version = DB version + 1。"""
        _clear_config_from_db()
        clear_runtime_config(persist=False)
        config = AgentConfig(config_version=10, text_provider="anthropic")
        _persist_config_to_db(config)
        try:
            assert _next_config_version() == 11
        finally:
            _clear_config_from_db()
            clear_runtime_config(persist=False)


class TestRuntimeConfigPersistence:
    """运行时配置 DB 持久化测试。"""

    def test_set_and_load_from_db(self, db_factory):
        """set_runtime_config(persist=True) 写入 DB，_load_config_from_db 读回。"""
        _clear_config_from_db()
        clear_runtime_config(persist=False)
        config = AgentConfig(
            text_provider="anthropic",
            text_model_name="claude-sonnet-4-20250514",
            config_version=3,
        )
        set_runtime_config(config, persist=True)
        try:
            loaded = _load_config_from_db()
            assert loaded is not None
            assert loaded.text_provider == "anthropic"
            assert loaded.config_version == 3
        finally:
            clear_runtime_config(persist=True)

    def test_clear_removes_from_db(self, db_factory):
        """clear_runtime_config(persist=True) 从 DB 删除。"""
        config = AgentConfig(config_version=1)
        set_runtime_config(config, persist=True)
        assert _load_config_from_db() is not None
        clear_runtime_config(persist=True)
        assert _load_config_from_db() is None
        assert get_runtime_config() is None

    def test_restore_from_db(self, db_factory):
        """restore_runtime_config_from_db 从 DB 恢复到内存。"""
        _clear_config_from_db()
        clear_runtime_config(persist=False)
        config = AgentConfig(
            text_provider="openai_compat",
            text_model_name="gpt-4o",
            config_version=7,
        )
        _persist_config_to_db(config)
        try:
            result = restore_runtime_config_from_db()
            assert result is True
            restored = get_runtime_config()
            assert restored is not None
            assert restored.text_provider == "openai_compat"
            assert restored.config_version == 7
        finally:
            clear_runtime_config(persist=True)

    def test_restore_from_empty_db(self, db_factory):
        """DB 无配置时 restore 返回 False。"""
        _clear_config_from_db()
        clear_runtime_config(persist=False)
        result = restore_runtime_config_from_db()
        assert result is False
        assert get_runtime_config() is None


class TestGetAgentConfigWithOverride:
    """get_agent_config 优先返回 override 的测试。"""

    def test_get_agent_config_returns_override(self):
        """有 override 时 get_agent_config 返回 override。"""
        config = AgentConfig(
            text_provider="anthropic",
            config_version=2,
        )
        set_runtime_config(config, persist=False)
        try:
            result = get_agent_config()
            assert result.text_provider == "anthropic"
            assert result.config_version == 2
        finally:
            clear_runtime_config(persist=False)

    def test_get_agent_config_falls_back_to_env(self):
        """无 override 时 get_agent_config 从环境变量构建。"""
        clear_runtime_config(persist=False)
        result = get_agent_config()
        # 应返回默认配置（deepseek）
        assert result.text_provider == "deepseek"
        assert result.config_version == 1  # 默认版本号
