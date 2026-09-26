"""本地教师展示层的身份恢复回归测试。"""

from backend.app.agent.privacy import PrivacyMapper


def _mapper() -> PrivacyMapper:
    mapper = PrivacyMapper()
    mapper.register_student(42)
    mapper.register_name("张三", student_id=42)
    mapper.register_student(99)
    mapper.register_name("李四", student_id=99)
    return mapper


def test_restore_text_for_display_rehydrates_anonymous_refs() -> None:
    mapper = _mapper()

    assert mapper.sanitize_text("请分析张三和李四") == "请分析student_01和student_02"
    assert mapper.restore_text_for_display(
        "student_01需要继续练习；student_02需要复习"
    ) == "张三需要继续练习；李四需要复习"


def test_restore_for_display_rehydrates_nested_structured_report() -> None:
    mapper = _mapper()
    report = {
        "summary": "student_01的语法薄弱",
        "findings": [{
            "student_ref": "student_01",
            "description": "student_01在时态题中失分",
        }],
    }

    restored = mapper.restore_for_display(report)

    assert restored["summary"] == "张三的语法薄弱"
    assert restored["findings"][0]["description"] == "张三在时态题中失分"
    assert restored["findings"][0]["student_ref"] == "student_01"
    assert restored["findings"][0]["student_name"] == "张三"
    assert restored["findings"][0]["student_id"] == 42


def test_bridge_compatibility_uses_shared_student_reference() -> None:
    from backend.app.agent.education_bridge.bridge_cli import _Anonymizer

    mapper = _mapper()
    anon = _Anonymizer(mapper)

    assert anon.id_for(42) == "student_01"
    assert anon.id_for(99) == "student_02"
    assert anon.id_for(42) != "S1"

