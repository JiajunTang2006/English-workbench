"""P0-C 回归测试：Agent 路由 Session 生命周期。

验证 get_session 依赖在正常、异常、取消路径下均关闭 Session。
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch
from fastapi import Request


class TestSessionLifecycle:
    """验证 get_session 的 yield/finally close 行为。"""

    @pytest.fixture
    def mock_session(self):
        """创建一个 mock SQLAlchemy Session。"""
        session = MagicMock()
        session.close = MagicMock()
        return session

    @pytest.fixture
    def mock_request(self, mock_session):
        """创建一个带有 mock session_factory 的 Request。"""
        request = MagicMock(spec=Request)
        request.app.state.session_factory = MagicMock(return_value=mock_session)
        return request

    def test_session_closed_on_normal_exit(self, mock_request, mock_session):
        """正常路径：generator 完成后 Session 被关闭。"""
        from backend.app.routers.agent import get_session

        gen = get_session(mock_request)
        session = next(gen)
        assert session is mock_session

        # 正常结束 generator
        with pytest.raises(StopIteration):
            next(gen)

        mock_session.close.assert_called_once()

    def test_session_closed_on_exception(self, mock_request, mock_session):
        """异常路径：抛出异常时 Session 仍被关闭。"""
        from backend.app.routers.agent import get_session

        gen = get_session(mock_request)
        session = next(gen)
        assert session is mock_session

        # 模拟请求处理中抛出异常
        with pytest.raises(RuntimeError, match="模拟异常"):
            try:
                gen.throw(RuntimeError("模拟异常"))
            finally:
                pass

        mock_session.close.assert_called_once()

    def test_session_closed_on_generator_close(self, mock_request, mock_session):
        """取消路径：generator 被 close() 时 Session 仍被关闭。"""
        from backend.app.routers.agent import get_session

        gen = get_session(mock_request)
        session = next(gen)
        assert session is mock_session

        # 模拟请求被取消（FastAPI 会在 client 断开时 close generator）
        gen.close()

        mock_session.close.assert_called_once()

    def test_session_factory_called_once(self, mock_request, mock_session):
        """session_factory 只被调用一次。"""
        from backend.app.routers.agent import get_session

        gen = get_session(mock_request)
        _ = next(gen)
        with pytest.raises(StopIteration):
            next(gen)

        mock_request.app.state.session_factory.assert_called_once()
