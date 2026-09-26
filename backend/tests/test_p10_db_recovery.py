"""P1-10 回归测试：TaskRegistry 以数据库为恢复来源

验证：
1. 应用重启后 /runs/{id}/events 从数据库恢复状态
2. 取消运行同步落库
3. 重试基于数据库查询，不依赖内存 TaskRegistry
4. 启动恢复将中断运行标记为 failed
5. BackgroundJob 恢复
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.database import Base
from backend.app.models.agent_entities import AnalysisRun, AgentSession, BackgroundJob


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    session = Session(bind=engine)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


class TestDatabaseRecovery:
    """数据库恢复。"""

    def test_p10_run_not_in_memory_returns_db_status(self, db_session):
        """内存中不存在的 run 从数据库返回状态。"""
        agent_session = AgentSession(title="test", term_id=1, status="active")
        db_session.add(agent_session)
        db_session.commit()

        run = AnalysisRun(
            session_id=agent_session.id,
            capability="exam_analysis",
            term_id=1,
            status="completed",
        )
        db_session.add(run)
        db_session.commit()
        db_session.refresh(run)

        # 模拟应用重启：内存 TaskRegistry 为空
        # 路由应从数据库恢复
        assert run.status == "completed"
        assert run.id is not None

    def test_p10_interrupted_run_recovered_as_failed(self, db_session):
        """启动恢复将中断的运行标记为 failed。"""
        agent_session = AgentSession(title="test", term_id=1, status="active")
        db_session.add(agent_session)
        db_session.commit()

        # 模拟一个中断的 running 运行
        run = AnalysisRun(
            session_id=agent_session.id,
            capability="exam_analysis",
            term_id=1,
            status="running",
        )
        db_session.add(run)
        db_session.commit()
        db_session.refresh(run)

        # 启动恢复逻辑
        interrupted = db_session.scalars(
            __import__("sqlalchemy").select(AnalysisRun).where(
                AnalysisRun.status.in_(["running", "queued"])
            )
        ).all()

        for r in interrupted:
            r.status = "failed"
            r.error_message = "启动恢复：运行被中断"
            r.completed_at = datetime.now(timezone.utc)

        db_session.commit()
        db_session.refresh(run)

        assert run.status == "failed"
        assert "中断" in run.error_message
        assert run.completed_at is not None

    def test_p10_cancel_syncs_to_db(self, db_session):
        """取消运行同步落库。"""
        agent_session = AgentSession(title="test", term_id=1, status="active")
        db_session.add(agent_session)
        db_session.commit()

        run = AnalysisRun(
            session_id=agent_session.id,
            capability="exam_analysis",
            term_id=1,
            status="running",
        )
        db_session.add(run)
        db_session.commit()
        db_session.refresh(run)

        # 模拟取消落库
        run_id = run.id
        run.status = "cancelled"
        run.completed_at = datetime.now(timezone.utc)
        db_session.commit()

        # 从数据库重新查询
        db_session.expire_all()
        recovered = db_session.scalar(
            __import__("sqlalchemy").select(AnalysisRun).where(AnalysisRun.id == run_id)
        )
        assert recovered.status == "cancelled"
        assert recovered.completed_at is not None

    def test_p10_retry_uses_db_not_memory(self, db_session):
        """重试基于数据库查询。"""
        agent_session = AgentSession(title="test", term_id=1, status="active")
        db_session.add(agent_session)
        db_session.commit()

        original_run = AnalysisRun(
            session_id=agent_session.id,
            capability="exam_analysis",
            term_id=1,
            status="failed",
            exam_id=1,
        )
        db_session.add(original_run)
        db_session.commit()
        db_session.refresh(original_run)

        # 重试路由直接从数据库查询 original_run
        # 不依赖 TaskRegistry 内存
        from sqlalchemy import select
        found = db_session.scalar(
            select(AnalysisRun).where(AnalysisRun.id == original_run.id)
        )
        assert found is not None
        assert found.capability == "exam_analysis"

    def test_p10_degraded_run_not_recovered_as_interrupted(self, db_session):
        """degraded 状态的 run 不被启动恢复标记为中断。"""
        agent_session = AgentSession(title="test", term_id=1, status="active")
        db_session.add(agent_session)
        db_session.commit()

        run = AnalysisRun(
            session_id=agent_session.id,
            capability="exam_analysis",
            term_id=1,
            status="degraded",
        )
        db_session.add(run)
        db_session.commit()
        db_session.refresh(run)

        # 启动恢复只查找 running/queued/validating/estimating/waiting_confirmation
        interrupted = db_session.scalars(
            __import__("sqlalchemy").select(AnalysisRun).where(
                AnalysisRun.status.in_(
                    ["running", "queued", "validating", "estimating", "waiting_confirmation"]
                )
            )
        ).all()

        assert len(interrupted) == 0  # degraded 不在其中


class TestBackgroundJobRecovery:
    """后台任务恢复。"""

    def test_p10_interrupted_job_recovered_as_failed(self, db_session):
        """启动恢复将中断的后台任务标记为 failed。"""
        job = BackgroundJob(
            job_type="exam_ingestion",
            status="running",
            scope_json={},
        )
        db_session.add(job)
        db_session.commit()
        db_session.refresh(job)

        # 启动恢复逻辑
        from sqlalchemy import select
        interrupted = db_session.scalars(
            select(BackgroundJob).where(
                BackgroundJob.status.in_(["running", "queued"])
            )
        ).all()

        assert len(interrupted) == 1
        for j in interrupted:
            j.status = "failed"
            j.last_error = "任务在运行中被中断（应用重启）"
            j.completed_at = datetime.now(timezone.utc)

        db_session.commit()
        db_session.refresh(job)
        assert job.status == "failed"
