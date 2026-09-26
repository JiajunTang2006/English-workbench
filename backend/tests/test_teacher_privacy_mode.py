from backend.app.agent.outbound_observer import OutboundObserver
from backend.app.agent.output_validator import OutputValidator
from backend.app.agent.privacy import PrivacyMapper


def test_teacher_mode_keeps_student_name_but_removes_other_identity_fields():
    mapper = PrivacyMapper(allow_student_names=True)
    mapper.register_student(7)
    mapper.register_name("张三", student_id=7)
    payload = mapper.sanitize_for_model({
        "student_id": 7,
        "student_name": "张三",
        "student_no": "2025090501",
        "parent_phone": "13800000000",
        "score": 86,
    })
    assert payload == {"anonymous_id": "student_01", "student_name": "张三", "score": 86}
    assert mapper.sanitize_text("张三考了86分，电话13800000000") == "张三考了86分，电话[电话已脱敏]"


def test_teacher_mode_observer_and_output_validator_allow_student_name():
    mapper = PrivacyMapper(allow_student_names=True)
    mapper.register_name("张三", student_id=7)
    observer = OutboundObserver()
    entry = observer.capture(prompt_text="请分析张三的成绩", privacy_mapper=mapper)
    assert "张三" in entry["content"]
    errors: list[str] = []
    warnings: list[str] = []
    OutputValidator._validate_privacy(OutputValidator.__new__(OutputValidator), "建议张三重点复习阅读", {}, mapper, errors, warnings)
    assert errors == []

