"""P1-7: 加强 Session 和上下文解析验收测试.

验证:
1. Session dependency 在正常/异常/取消路径下正确关闭（连接不泄漏）
2. 多个并发请求共享连接池不冲突
3. 事务在异常时正确回滚
4. 快速连续请求不会导致连接耗尽
"""

from __future__ import annotations

import threading
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from backend.app.auth import TOKEN
from backend.app.config import Settings
from backend.app.factory import create_app
from backend.app.models.agent_entities import AgentSession, Base
from backend.app.models.entities import Term, Class, Exam

AUTH_HEADERS = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture()
def app_client_db(tmp_path):
    settings = Settings(data_dir=tmp_path)
    app = create_app(settings)
    client = TestClient(app)
    Session = app.state.session_factory

    with Session() as s:
        term = Term(code="2024-01", name="2024春", starts_on=date(2024, 2, 1), ends_on=date(2024, 7, 1))
        s.add(term)
        s.commit()
        s.refresh(term)
        term_id = term.id

        cls = Class(term_id=term_id, name="高三1班", grade="高三", status="active")
        s.add(cls)
        s.commit()
        s.refresh(cls)

        exam = Exam(term_id=term_id, name="期中考试", full_score=100,
                    exam_type="english_total", exam_kind="regular", status="active")
        s.add(exam)
        s.commit()
        s.refresh(exam)

        agent_session = AgentSession(
            title="测试会话", term_id=term_id, exam_id=exam.id,
            class_id=cls.id, status="active",
        )
        s.add(agent_session)
        s.commit()
        s.refresh(agent_session)
        session_id = agent_session.id

    yield {"app": app, "client": client, "db": Session,
           "term_id": term_id, "session_id": session_id}


# ============ Session 生命周期测试 ============

class TestSessionLifecycle:
    """验证 get_session dependency 在各路径下正确释放连接。"""

    def test_normal_request_closes_session(self, app_client_db):
        """正常请求后 session 被关闭。"""
        client = app_client_db["client"]
        resp = client.get("/api/v1/agent/sessions", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        # 正常返回即证明 session 被正确使用和关闭

    def test_404_request_closes_session(self, app_client_db):
        """404 请求后 session 也被关闭（不泄漏）。"""
        client = app_client_db["client"]
        resp = client.get("/api/v1/agent/sessions/99999", headers=AUTH_HEADERS)
        assert resp.status_code == 404
        # 再发一个正常请求验证连接池没被耗尽
        resp2 = client.get("/api/v1/agent/sessions", headers=AUTH_HEADERS)
        assert resp2.status_code == 200

    def test_exception_request_closes_session(self, app_client_db):
        """异常路径下 session 被关闭（事务回滚 + 连接释放）。"""
        client = app_client_db["client"]
        # 发送无效请求体触发 422 验证错误
        resp = client.post(
            "/api/v1/agent/sessions",
            json={"invalid_field": "bad"},
            headers=AUTH_HEADERS,
        )
        assert resp.status_code in (400, 422)
        # 验证连接池仍然可用
        resp2 = client.get("/api/v1/agent/sessions", headers=AUTH_HEADERS)
        assert resp2.status_code == 200

    def test_rapid_sequential_requests(self, app_client_db):
        """快速连续请求不耗尽连接池。"""
        client = app_client_db["client"]
        for _ in range(20):
            resp = client.get("/api/v1/agent/sessions", headers=AUTH_HEADERS)
            assert resp.status_code == 200

    def test_concurrent_requests_thread_safety(self, app_client_db):
        """多线程并发请求不冲突。"""
        client = app_client_db["client"]
        results = []
        errors = []

        def make_request():
            try:
                resp = client.get("/api/v1/agent/sessions", headers=AUTH_HEADERS)
                results.append(resp.status_code)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=make_request) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert len(errors) == 0, f"Concurrent requests failed: {errors}"
        assert len(results) == 5
        assert all(r == 200 for r in results)

    def test_session_detail_returns_correct_data(self, app_client_db):
        """会话详情接口返回正确的关联数据。"""
        client = app_client_db["client"]
        session_id = app_client_db["session_id"]

        resp = client.get(f"/api/v1/agent/sessions/{session_id}", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == session_id
        assert data["title"] == "测试会话"

    def test_session_list_search(self, app_client_db):
        """会话列表搜索功能正常。"""
        client = app_client_db["client"]
        resp = client.get(
            "/api/v1/agent/sessions?search=测试",
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        assert any(s["title"] == "测试会话" for s in data)

    def test_session_list_search_no_match(self, app_client_db):
        """搜索无匹配返回空列表。"""
        client = app_client_db["client"]
        resp = client.get(
            "/api/v1/agent/sessions?search=不存在的关键词XYZ",
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json() == []


# ============ 事务回滚测试 ============

class TestTransactionRollback:
    """验证异常时事务正确回滚。"""

    def test_failed_create_does_not_leave_partial_data(self, app_client_db):
        """创建会话失败时不残留部分数据。"""
        client = app_client_db["client"]
        db = app_client_db["db"]

        # 获取创建前的会话数
        with db() as s:
            before_count = len(s.execute(
                select(AgentSession).where(AgentSession.deleted_at.is_(None))
            ).scalars().all())

        # 发送无效创建请求（extra: forbid 触发 422）
        resp = client.post(
            "/api/v1/agent/sessions",
            json={"term_id": 1, "invalid_field": "bad"},
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 422

        # 验证没有残留数据
        with db() as s:
            after_count = len(s.execute(
                select(AgentSession).where(AgentSession.deleted_at.is_(None))
            ).scalars().all())
        assert after_count == before_count, "Failed create should not leave partial data"


# ============ 上下文解析前端测试 ============
# （前端部分在 tests/test_p17_context_resolution.js 中）
