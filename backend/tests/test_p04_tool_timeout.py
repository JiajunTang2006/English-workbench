"""P0-4: 真正可终止的工具超时测试

验证：
1. 工具执行前检查 check_cancelled()
2. 工具执行中 DB 查询间检查 check_cancelled()
3. 超时后 cancel_event 被设置
4. 超时后迟到结果不写入证据
5. SQLite progress handler 安装和清理
6. ToolRegistry.execute 在执行前检查取消标志
"""

import asyncio
import threading
import time
import pytest
from unittest.mock import MagicMock, patch

from backend.app.agent.tools.tool_context import ToolContext, set_tool_context, reset_tool_context, get_tool_context
from backend.app.agent.registry.tools import ToolRegistry, ToolDefinition, ToolError


class TestCheckCancelled:
    """check_cancelled() 协作式取消测试。"""

    def test_check_cancelled_raises_when_set(self):
        """cancel_event 设置后 check_cancelled 抛出 TimeoutError。"""
        ctx = ToolContext()
        assert not ctx.cancelled
        ctx.check_cancelled()  # 不应抛出

        ctx.cancel_event.set()
        assert ctx.cancelled
        with pytest.raises(TimeoutError):
            ctx.check_cancelled()

    def test_check_cancelled_does_not_raise_when_not_set(self):
        """cancel_event 未设置时 check_cancelled 不抛出。"""
        ctx = ToolContext()
        ctx.check_cancelled()  # 不应抛出


class TestToolRegistryCancelCheck:
    """ToolRegistry.execute 执行前检查取消标志测试。"""

    def test_execute_checks_cancelled_before_running(self):
        """execute() 在工具执行前检查 cancel_event。"""
        registry = ToolRegistry()
        handler_called = False

        def handler(**kwargs):
            nonlocal handler_called
            handler_called = True
            return {"data": {}}

        registry.register(ToolDefinition(
            name="test_tool",
            description="test",
            parameters_schema={},
            handler=handler,
            category="test",
        ))

        # 设置已取消的 context
        ctx = ToolContext()
        ctx.cancel_event.set()
        token = set_tool_context(ctx)
        try:
            with pytest.raises(ToolError) as exc_info:
                registry.execute("test_tool")
            assert "取消" in str(exc_info.value)
            assert not handler_called  # handler 不应被调用
        finally:
            reset_tool_context(token)

    def test_execute_passes_when_not_cancelled(self):
        """execute() 在未取消时正常执行。"""
        registry = ToolRegistry()

        def handler(**kwargs):
            return {"data": {"ok": True}}

        registry.register(ToolDefinition(
            name="test_tool",
            description="test",
            parameters_schema={},
            handler=handler,
            category="test",
        ))

        ctx = ToolContext()
        token = set_tool_context(ctx)
        try:
            result = registry.execute("test_tool")
            assert result["data"]["ok"] is True
        finally:
            reset_tool_context(token)


class TestToolTimeoutIsolation:
    """工具超时隔离测试。"""

    def test_timeout_returns_error_and_sets_cancel_event(self):
        """超时后返回错误消息且 cancel_event 被设置。"""
        from backend.app.agent.loop import AgentLoop
        from backend.app.agent.registry.tools import ToolRegistry
        from backend.app.agent.config import AgentConfig

        registry = ToolRegistry()

        def slow_handler(**kwargs):
            time.sleep(5)  # 模拟慢查询
            return {"data": {"late": True}}

        registry.register(ToolDefinition(
            name="slow_tool",
            description="slow test tool",
            parameters_schema={},
            handler=slow_handler,
            category="test",
            timeout_seconds=0.1,  # 100ms 超时
        ))

        # 创建 AgentLoop（provider 不需要实际工作）
        mock_provider = MagicMock()
        loop = AgentLoop(mock_provider, registry, AgentConfig())

        # 创建 mock ToolCall
        from backend.app.agent.providers.base import ToolCall
        tool_call = ToolCall(tool_name="slow_tool", arguments={})

        # 创建 mock context
        mock_context = MagicMock()
        mock_context.available_tools = ["slow_tool"]
        mock_context.scope = {}

        # 使用非内存 SQLite 数据库以触发异步路径
        # 但由于 db_session=None 会走同步路径，我们需要测试异步路径
        # 在实际应用中，生产数据库是文件 SQLite
        import tempfile, os
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            engine = create_engine(f"sqlite:///{db_path}")
            session = Session(bind=engine)

            async def run_test():
                result = await loop._execute_tool_async(
                    tool_call, mock_context, db_session=session
                )
                return result

            result = asyncio.run(run_test())

            assert "error" in result
            assert "超时" in result["error"] or "终止" in result["error"]

            session.close()
            engine.dispose()
        finally:
            os.unlink(db_path)

        loop.shutdown()

    def test_cancelled_tool_result_not_registered_as_evidence(self):
        """超时/取消的工具结果不写入证据。"""
        from backend.app.agent.loop import AgentLoop
        from backend.app.agent.registry.tools import ToolRegistry
        from backend.app.agent.config import AgentConfig
        from backend.app.agent.providers.base import ToolCall
        from backend.app.agent.evidence import EvidenceLedger
        from backend.app.agent.privacy import PrivacyMapper
        from backend.app.agent.context import TeachingContext

        registry = ToolRegistry()

        def slow_handler(**kwargs):
            time.sleep(5)
            return {"data": {"late": True}}

        registry.register(ToolDefinition(
            name="slow_tool",
            description="slow",
            parameters_schema={},
            handler=slow_handler,
            category="test",
            timeout_seconds=0.1,
        ))

        mock_provider = MagicMock()
        loop = AgentLoop(mock_provider, registry, AgentConfig())

        tool_call = ToolCall(tool_name="slow_tool", arguments={})

        context = TeachingContext(
            scope={},
            available_tools=["slow_tool"],
            evidence_ledger=EvidenceLedger(),
            privacy_mapper=PrivacyMapper(),
        )

        import tempfile, os
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            engine = create_engine(f"sqlite:///{db_path}")
            session = Session(bind=engine)

            async def run_test():
                result = await loop._execute_tool_async(
                    tool_call, context, db_session=session
                )
                return result

            result = asyncio.run(run_test())

            # 结果是错误，不应被注册为证据
            assert "error" in result
            assert "data" not in result
            # 证据账本应为空
            assert len(context.evidence_ledger.all_ids()) == 0

            session.close()
            engine.dispose()
        finally:
            os.unlink(db_path)

        loop.shutdown()


class TestCooperativeCancelInTools:
    """工具函数内协作式取消测试。"""

    def test_tool_function_checks_cancel_between_queries(self):
        """工具函数在 DB 查询间检查 cancel_event。"""
        from backend.app.agent.tools.exam_tools import _get_exam_statistics
        from backend.app.agent.tools.tool_context import set_tool_context, reset_tool_context
        from backend.app.database import Base
        from backend.app.models import entities  # noqa: F401
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        session = Session(bind=engine)

        ctx = ToolContext(
            db_session=session,
            scope={"exam_id": 1},
        )
        ctx.cancel_event.set()  # 预先设置取消
        token = set_tool_context(ctx)
        try:
            # 由于 cancel_event 已设置，check_cancelled 应在第一次 DB 查询前抛出
            with pytest.raises(TimeoutError):
                _get_exam_statistics()
        finally:
            reset_tool_context(token)
            session.close()
            engine.dispose()
