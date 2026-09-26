"""B2-03 模型输入/输出隐私门禁测试

覆盖：
- 输入侧：当前消息、历史 user/assistant、学校名、教师名统一脱敏，
  真实姓名替换为匿名编号（不删除）；
- 输出侧：OutputValidator 递归扫描 raw_text 与 structured_answer（含嵌套），
  命中当前运行身份词典（姓名/学校/受保护文本）及学号/电话时拒绝持久化；
- fail-closed：未注册身份字段拒绝发送；
- 日志与错误信息不包含原始敏感值。
"""

from __future__ import annotations

from backend.app.agent.output_validator import OutputValidator
from backend.app.agent.privacy import PrivacyMapper


def _mapper():
    mapper = PrivacyMapper()
    mapper.register_student(11)
    mapper.register_name("陈小明", student_id=11)
    mapper.register_names(["李小红", "王强"])
    mapper.register_school_names(["杭州市城实验中学"])
    mapper.register_protected_terms(["冯老师"])
    return mapper


class TestInputSanitization:
    def test_current_message_name_replaced_with_anon_id(self):
        mapper = _mapper()
        sanitized = mapper.sanitize_text("请分析陈小明在本次考试中的表现")
        assert "陈小明" not in sanitized
        assert "student_01" in sanitized  # 替换为匿名编号而非删除

    def test_history_user_message_sanitized(self):
        mapper = _mapper()
        sanitized = mapper.sanitize_text("李小红本次听写满分，王强需要加强背诵")
        assert "李小红" not in sanitized
        assert "王强" not in sanitized
        assert "student_0" in sanitized

    def test_assistant_history_sanitized(self):
        mapper = _mapper()
        sanitized = mapper.sanitize_text("根据分析，陈小明适合基础巩固训练")
        assert "陈小明" not in sanitized

    def test_school_name_sanitized(self):
        mapper = _mapper()
        sanitized = mapper.sanitize_text("杭州市城实验中学2026年秋季成绩")
        assert "杭州市城实验中学" not in sanitized
        assert "[学校已脱敏]" in sanitized

    def test_teacher_name_sanitized(self):
        mapper = _mapper()
        sanitized = mapper.sanitize_text("冯老师提交的默写数据")
        assert "冯老师" not in sanitized

    def test_student_no_and_phone_patterns(self):
        mapper = _mapper()
        sanitized = mapper.sanitize_text("学号20240101，电话13800138000")
        assert "20240101" not in sanitized
        assert "13800138000" not in sanitized

    def test_sanitize_for_model_fail_closed_on_unregistered(self):
        mapper = _mapper()
        try:
            mapper.sanitize_for_model({"student_id": 999, "score": 90})
            raised = False
        except Exception:
            raised = True
        assert raised, "未注册 student_id 必须 fail closed"

    def test_clean_text_unchanged(self):
        mapper = _mapper()
        text = "本次平均分85.2，参与率92%"
        assert mapper.sanitize_text(text) == text


class TestOutputValidation:
    def _validate(self, mapper, answer, raw_text=""):
        validator = OutputValidator()
        return validator.validate(
            structured_answer=answer,
            evidence_ledger=_DummyLedger(),
            privacy_mapper=mapper,
            raw_text=raw_text,
            finish_reason="stop",
        )

    def test_clean_anonymous_output_passes(self):
        mapper = _mapper()
        result = self._validate(mapper, {
            "answer_type": "exam_analysis",
            "summary": "整体平均分为85分，及格率93%",
            "findings": [{"title": "f1", "evidence_ids": ["ev_ok"]}],
            "recommendations": [],
            "limitations": [],
            "scope_snapshot": {},
            "schema_version": "1.0.0",
        })
        privacy_errors = [e for e in result.errors if "身份" in e or "学号" in e or "电话" in e]
        assert privacy_errors == []

    def test_raw_text_real_name_rejected(self):
        mapper = _mapper()
        result = self._validate(mapper, {
            "answer_type": "exam_analysis",
            "summary": "总",
            "findings": [{"title": "f1", "evidence_ids": ["ev_ok"]}],
            "recommendations": [],
            "limitations": [],
            "scope_snapshot": {},
            "schema_version": "1.0.0",
        }, raw_text="本部分析陈小明同学的重点")
        assert not result.valid
        assert any("身份信息" in e for e in result.errors)

    def test_nested_structured_answer_name_rejected(self):
        mapper = _mapper()
        answer = {
            "answer_type": "exam_analysis",
            "summary": "摘要",
            "findings": [{
                "title": "以小见大",
                "evidence_ids": ["ev_ok"],
                "description": "嵌套数组与对象内的学校为杭州市城实验中学",
            }],
            "recommendations": [{"action": "r", "supports": ["ev_ok"], "rationale": "李小红需要加强"}],
            "limitations": [],
            "scope_snapshot": {},
            "schema_version": "1.0.0",
        }
        result = self._validate(mapper, answer)
        assert not result.valid
        assert any("身份信息" in e for e in result.errors)

    def test_raw_text_empty_but_structured_leaks(self):
        mapper = _mapper()
        result = self._validate(mapper, {
            "answer_type": "exam_analysis",
            "summary": "李小红在期中考试表现突出",
            "findings": [{"title": "f1", "evidence_ids": ["ev_ok"]}],
            "recommendations": [],
            "limitations": [],
            "scope_snapshot": {},
            "schema_version": "1.0.0",
        }, raw_text="")
        assert not result.valid
        assert any("身份信息" in e for e in result.errors)

    def test_school_leak_rejected(self):
        mapper = _mapper()
        result = self._validate(mapper, {
            "answer_type": "exam_analysis",
            "summary": "总",
            "findings": [{"title": "f1", "evidence_ids": ["ev_ok"],
                          "description": "数据来自杭州市城实验中学"}],
            "recommendations": [],
            "limitations": [],
            "scope_snapshot": {},
            "schema_version": "1.0.0",
        })
        assert not result.valid

    def test_teacher_name_leak_rejected(self):
        mapper = _mapper()
        result = self._validate(mapper, {
            "answer_type": "exam_analysis",
            "summary": "冯老师负责本次阅卷",
            "findings": [],
            "recommendations": [],
            "limitations": [],
            "scope_snapshot": {},
            "schema_version": "1.0.0",
        })
        assert not result.valid

    def test_student_no_leak_rejected_without_value_in_errors(self):
        # 真实学号在身份词典中 → 输出带出即拒绝（错误信息不含学号明文）
        mapper = _mapper()
        mapper.register_protected_terms(["20240101"])
        result = self._validate(mapper, {
            "answer_type": "exam_analysis",
            "summary": "学号20240101",
            "findings": [],
            "recommendations": [],
            "limitations": [],
            "scope_snapshot": {},
            "schema_version": "1.0.0",
        })
        assert not result.valid
        assert any("学号" in e or "身份" in e for e in result.errors)
        assert "20240101" not in "".join(result.errors)

    def test_bare_number_not_rejected_as_student_no(self):
        # 词典外的普通数字（分数/日期/序号）不应误判为学号拒绝
        mapper = _mapper()
        result = self._validate(mapper, {
            "answer_type": "exam_analysis",
            "summary": "平均分 77.5，来自 20260401",
            "findings": [],
            "recommendations": [],
            "limitations": [],
            "scope_snapshot": {},
            "schema_version": "1.0.0",
        })
        assert result.valid, result.errors

    def test_errors_do_not_contain_real_names(self):
        mapper = _mapper()
        result = self._validate(mapper, {
            "answer_type": "exam_analysis",
            "summary": "陈小明同学表现良好",
            "findings": [],
            "recommendations": [],
            "limitations": [],
            "scope_snapshot": {},
            "schema_version": "1.0.0",
        })
        joined = "".join(result.errors)
        assert "陈小明" not in joined
        assert "李小红" not in joined


class _DummyLedger:
    def __init__(self):
        self._known = ["ev_ok"]

    def exists(self, eid):
        return eid in self._known

    def get(self, eid):
        return None

    def all_ids(self):
        return list(self._known)

class TestIdentityDictionarySchool:
    """学校名称必须从配置填充进运行内身份词典（此前从未填充）。"""

    def test_school_name_filled_from_app_setting(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session
        from backend.app.database import Base
        from backend.app.models.entities import AppSetting
        from backend.app.services.agent_analysis.identity_dict import (
            build_run_identity_dictionary,
        )

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = Session(bind=engine)
        try:
            db.add(AppSetting(key="school_name", value_json="杭州市城实验中学"))
            db.add(AppSetting(key="teacher_name", value_json="王老师"))
            db.commit()

            identity = build_run_identity_dictionary(db)
            assert "杭州市城实验中学" in identity.school_names
            assert "王老师" in identity.protected_terms
        finally:
            db.close()
            engine.dispose()

    def test_school_name_registered_into_mapper(self, tmp_path):
        from backend.app.agent.privacy import PrivacyMapper
        from backend.app.services.agent_analysis.identity_dict import RuntimeIdentity

        identity = RuntimeIdentity(school_names=["南城实验中学"])
        from backend.app.services.agent_analysis.identity_dict import register_identity_into_mapper
        mapper = PrivacyMapper()
        register_identity_into_mapper(mapper, identity)
        # 词典注册后，文本中的学校名会被脱敏
        sanitized = mapper.sanitize_text("数据来自南城实验中学")
        assert "南城实验中学" not in sanitized
