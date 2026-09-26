"""B3-04 统一上下文与隐私门禁测试

覆盖：
1. 身份词典按 student_id 稳定排序 → 同 scope 跨 run 匿名编号一致；
2. 电话进入受保护词：sanitize_text 电话/学号脱敏；
3. 正式资料必须传真实 data_dir：附件文件丢失 → 拒绝引用（不进上下文）；
4. 出站观察器：脱敏后仍含真实身份 → PrivacyViolationError（fail-closed）；
   脱敏正常 → 记录脱敏副本；含姓名/学号/电话的文本在观察器副本中不存在；
5. build_multi_turn_messages 出站内容不含真实姓名/学号/电话
   （含历史 user/assistant 消息与会话摘要、正式附件注入）。
"""

from __future__ import annotations

import os
import tempfile
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.database import Base
from backend.app.models.entities import (
    Term, Class, Exam, Student, Enrollment, ExamScore, AppSetting, Attachment,
)
from backend.app.models.agent_entities import (
    AgentSession, AgentMessage, AgentMessageAttachment,
)
from backend.app.agent.privacy import PrivacyMapper, PrivacyViolationError
from backend.app.agent.outbound_observer import OutboundObserver
from backend.app.services.agent_analysis.identity_dict import (
    build_run_identity_dictionary, register_identity_into_mapper,
)
from backend.app.services.agent_analysis.formal_context import FormalContextProvider
from backend.app.services.agent_analysis.sessions import SessionService


@pytest.fixture
def session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory, tmp_path
    engine.dispose()


@pytest.fixture
def world(session_factory):
    factory, tmp_path = session_factory
    with factory() as db:
        t = Term(code="t1", name="2026春", starts_on=date(2026, 1, 1),
                 ends_on=date(2026, 7, 1), status="active")
        db.add(t)
        db.commit()
        c = Class(name="一班", term_id=t.id)
        db.add(c)
        db.commit()
        s1 = Student(name="张三", student_no="20260001", parent_phone="13800000000")
        s2 = Student(name="李四", student_no="20260002", parent_phone="13912345678")
        db.add_all([s1, s2])
        db.add(AppSetting(key="teacher_name", value_json="王老师"))
        db.add(AppSetting(key="school_name", value_json="第一中学"))
        db.commit()
        db.add_all([
            Enrollment(term_id=t.id, class_id=c.id, student_id=s1.id, status="active"),
            Enrollment(term_id=t.id, class_id=c.id, student_id=s2.id, status="active"),
        ])
        agent = AgentSession(title="S1", term_id=t.id, status="active")
        db.add(agent)
        db.commit()
        return {"factory": factory, "tmp": tmp_path, "term_id": t.id,
                "s1": s1.id, "s2": s2.id, "agent_session_id": agent.id}


def _mapper_for(db, world) -> PrivacyMapper:
    identity = build_run_identity_dictionary(
        db, term_id=world["term_id"],
    )
    mapper = PrivacyMapper()
    register_identity_into_mapper(mapper, identity)
    return mapper


# ---------------------------------------------------------------------------
# 1. 匿名编号稳定
# ---------------------------------------------------------------------------

class TestStableAnonymity:

    def test_identity_order_stable_across_builds(self, world):
        factory = world["factory"]
        with factory() as db:
            m1 = _mapper_for(db, world)
        with factory() as db:
            m2 = _mapper_for(db, world)
        # 同 scope 两次构建：编号绑定同一学生（稳定）
        for sid in (world["s1"], world["s2"]):
            assert m1.to_anonymous(sid) == m2.to_anonymous(sid)
        # 且编号不重叠
        assert m1.to_anonymous(world["s1"]) != m1.to_anonymous(world["s2"])

    def test_phone_and_teacher_in_dictionary(self, world):
        factory = world["factory"]
        with factory() as db:
            identity = build_run_identity_dictionary(db, term_id=world["term_id"])
            assert "13800000000" in identity.phones
            assert "王老师" in identity.protected_terms
            assert "第一中学" in identity.school_names

    def test_phone_sanitized_in_text(self, world):
        factory = world["factory"]
        with factory() as db:
            mapper = _mapper_for(db, world)
        out = mapper.sanitize_text("张三的电话是13800000000，学号20210001")
        assert "张三" not in out
        assert "13800000000" not in out
        assert "电话已脱敏" in out or "学号已脱敏" in out

    def test_identity_falls_back_when_run_class_id_has_no_enrollments(self, world):
        """历史重复班级编号不应让教师端姓名恢复失效。"""
        factory = world["factory"]
        with factory() as db:
            # 传入一个同学期但没有 Enrollment 的 class_id，模拟同步产生的
            # 重复班级；词典应回退到该学期的有效学生名单。
            empty_class = Class(name="重复班", term_id=world["term_id"])
            db.add(empty_class)
            db.commit()
            identity = build_run_identity_dictionary(
                db, term_id=world["term_id"], class_id=empty_class.id,
            )
            assert identity.student_names == ["张三", "李四"]


# ---------------------------------------------------------------------------
# 2. 正式资料：文件丢失拒绝引用
# ---------------------------------------------------------------------------

def _make_formal_attachment(db, world, *, present: bool) -> Path:
    t_id = world["term_id"]
    storage_name = "real-file.txt" if present else "missing-file.bin"
    att = Attachment(
        term_id=t_id, title="考试说明",
        original_name="考试说明.txt",
        mime_type="text/plain", size_bytes=1024, sha256="0" * 64,
        storage_name=storage_name,
        metadata_json={"parsed": {"status": "confirmed",
                                  "content": "张三同学需携带准考证。联系电话13800000000。",
                                  "confirmed_at": "2026-01-01T00:00:00Z"}},
    )
    db.add(att)
    db.commit()
    msg = AgentMessage(session_id=world["agent_session_id"], role="user",
                       content_text="请参考正式资料")
    db.add(msg)
    db.commit()
    db.add(AgentMessageAttachment(
        message_id=msg.id, attachment_id=att.id, purpose="exam_rules",
        promoted_to_formal=True,
    ))
    db.commit()
    target = world["tmp"] / "attachments" / storage_name
    target.parent.mkdir(parents=True, exist_ok=True)
    if present:
        target.write_text("dummy", encoding="utf-8")
    return att.id


def test_formal_context_rejects_missing_file(world):
    factory = world["factory"]
    with factory() as db:
        _make_formal_attachment(db, world, present=False)
        provider = FormalContextProvider(db, data_dir=world["tmp"])
        assert provider.list_formal_attachments(world["agent_session_id"]) == []
        assert provider.get_formal_context_text(world["agent_session_id"]) is None


def test_formal_context_accepts_existing_file(world):
    factory = world["factory"]
    with factory() as db:
        _make_formal_attachment(db, world, present=True)
    with factory() as db:
        provider = FormalContextProvider(db, data_dir=world["tmp"])
        text = provider.get_formal_context_text(world["agent_session_id"])
        assert text is not None
        assert "考试说明" in text


def test_formal_context_rejects_unconfirmed_parse_failed(world):
    factory = world["factory"]
    with factory() as db:
        att = Attachment(
            term_id=world["term_id"], title="bad",
            original_name="bad.txt", mime_type="application/pdf",
            size_bytes=1, sha256="1" * 64,
            storage_name="bad.bin",
            metadata_json={"parsed": {"status": "parse_failed",
                                      "error": "OCR 失败"}},
        )
        db.add(att)
        db.commit()
        msg = AgentMessage(session_id=world["agent_session_id"], role="user",
                           content_text="x")
        db.add(msg)
        db.commit()
        db.add(AgentMessageAttachment(
            message_id=msg.id, attachment_id=att.id, purpose="rules",
            promoted_to_formal=True))
        db.commit()
        provider = FormalContextProvider(db, data_dir=world["tmp"])
        assert provider.list_formal_attachments(world["agent_session_id"]) == []


# ---------------------------------------------------------------------------
# 3. 出站观察器
# ---------------------------------------------------------------------------

class TestOutboundObserver:

    def test_capture_sanitized_ok(self, world):
        factory = world["factory"]
        with factory() as db:
            mapper = _mapper_for(db, world)
        text = "张三的家长电话13800000000，学号20210001"
        observer = OutboundObserver()
        entry = observer.capture(prompt_text=text, privacy_mapper=mapper)
        blob = entry["content"]
        assert "张三" not in blob
        assert "13800000000" not in blob
        assert "20210001" not in blob

    def test_leak_raises_fail_closed(self, world):
        # 无 mapper 的原始文本携带电话 → 格式自检必须拦截（fail-closed）
        with pytest.raises(PrivacyViolationError):
            OutboundObserver().capture(
                prompt_text="家长联系电话 13800000000", privacy_mapper=None,
            )

    def test_check_identity_detects_known_real_name(self, world):
        # 检查器本身必须能命中词典中的真实姓名（capture 的 fail-closed 防线）
        mapper = PrivacyMapper()
        mapper.register_names(["王小明"])
        hits, _ = OutboundObserver()._check_identity("王小明未脱敏", mapper)
        assert "身份词典命中" in hits

    def test_audit_copy_is_anonymized(self, world, tmp_path):
        factory = world["factory"]
        with factory() as db:
            mapper = _mapper_for(db, world)
        observer = OutboundObserver(storage_dir=tmp_path)
        observer.capture(prompt_text=mapper.sanitize_text("张三 13800000000"),
                         privacy_mapper=mapper)
        audit = (tmp_path / "outbound.jsonl").read_text(encoding="utf-8")
        assert "张三" not in audit
        assert "13800000000" not in audit


# ---------------------------------------------------------------------------
# 4. 多轮上下文出站（含历史 user/assistant + 正式附件）
# ---------------------------------------------------------------------------

def test_multi_turn_outbound_clean(world):
    factory, tmp_path = world["factory"], world["tmp"]
    with factory() as db:
        _make_formal_attachment(db, world, present=True)
        # 历史 user / assistant（含真实姓名与电话）
        db.add(AgentMessage(
            session_id=world["agent_session_id"], role="user",
            content_text="张三上次问过"),
        )
        db.add(AgentMessage(
            session_id=world["agent_session_id"], role="assistant",
            content_text="李四的表现联系电话13912345678",
            structured_answer_json={"summary": "李四成绩"}, model_name="harness",
        ))
        db.commit()

    with factory() as db:
        mapper = _mapper_for(db, world)
        messages = SessionService(db).build_multi_turn_messages(
            session_id=world["agent_session_id"],
            system_prompt="系统规则",
            user_message="张三最近怎么样？",
            privacy_mapper=mapper,
        )

    rendered = "\n\n".join(m.get("content", "") for m in messages)
    observer = OutboundObserver()
    entry = observer.capture(prompt_text=rendered, privacy_mapper=mapper)
    for real in ("张三", "李四", "王老师", "第一中学", "20210001", "13800000000"):
        assert real not in entry["content"], f"出站内容包含真实身份: {real}"
