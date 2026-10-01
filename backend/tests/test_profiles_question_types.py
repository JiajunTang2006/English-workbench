from backend.app.services.profiles import _question_type_category
from backend.app.services.subjects import get_subject


def test_explicit_non_english_question_type_wins_over_non_choice_section():
    categories = tuple(get_subject("math").question_types)
    assert _question_type_category("二、非选择题", "填空题", categories) == "填空题"


def test_question_type_category_does_not_treat_container_as_fill_in():
    categories = tuple(get_subject("math").question_types)
    assert _question_type_category("二、非选择题", None, categories) is None


def test_numbered_section_and_parenthesized_question_type():
    categories = tuple(get_subject("math").question_types)
    assert _question_type_category("一、选择题", None, categories) == "选择题"
    assert _question_type_category("二、非选择题", "非选择题（填空）", categories) == "填空题"
    assert _question_type_category("填空题", "计算题（简算）", categories) == "计算题"
