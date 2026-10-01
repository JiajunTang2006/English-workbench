"""学科注册表与学科设置接口测试。

锁定四件事：

1. 注册表本身完整（11 个学科、key 唯一、文案键齐全、模块 key 合法）；
2. 读路径容错（脏值/旧值回退默认学科），写路径严格（非法 key 直接拒绝）；
3. ``/api/v1/subjects`` 的 ``chosen`` 语义（老师是否显式选过学科）；
4. **英语行为逐位不变**——引入学科不能改动任何既有文案与数据键。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.auth import TOKEN
from backend.app.config import Settings
from backend.app.factory import create_app
from backend.app.schemas import SettingsPatch, SettingsRead
from backend.app.services.subjects import (
    CORE_MODULE_KEYS,
    DEFAULT_SUBJECT_KEY,
    LABEL_KEYS,
    LANGUAGE_MODULE_KEYS,
    SUBJECTS,
    SUBJECT_KEYS,
    get_subject,
    is_valid_subject_key,
    list_subjects,
    normalize_subject_key,
)

# 引入学科前「英语」在前端的既有文案，必须一字不改。
ENGLISH_LABELS = {
    "score_total": "英语总分",
    "entrance_score": "入学英语",
    "score_column": "英语成绩",
    "score_short": "英语分数",
    "score_single": "英语单科成绩",
    "score_trend": "英语分数趋势",
    "score_ranking": "英语排名",
    "exam_default": "英语考试",
    "ability_disclaimer": "不代表英语水平",
}
# 前端 MODULES 里的题型下拉，顺序也是契约的一部分。
ENGLISH_QUESTION_TYPES = (
    "听力",
    "阅读理解",
    "完形填空",
    "选词填空",
    "单词拼写",
    "语法填空",
    "作文",
)
VALID_MODULE_KEYS = set(CORE_MODULE_KEYS) | set(LANGUAGE_MODULE_KEYS)


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def _client(tmp_path) -> TestClient:
    return TestClient(create_app(Settings(data_dir=tmp_path)))


# 1. 注册表完整性


def test_subject_registry_covers_junior_high_subjects():
    assert len(SUBJECTS) == 11
    assert SUBJECT_KEYS == (
        "chinese",
        "math",
        "english",
        "physics",
        "chemistry",
        "biology",
        "politics",
        "history",
        "geography",
        "it",
        "other",
    )
    assert len(set(SUBJECT_KEYS)) == len(SUBJECT_KEYS)


def test_every_subject_declares_full_label_set_and_legal_modules():
    for item in SUBJECTS:
        assert set(item.labels) == set(LABEL_KEYS), item.key
        assert all(str(value).strip() for value in item.labels.values()), item.key
        assert item.question_types, item.key
        assert set(item.modules) <= VALID_MODULE_KEYS, item.key
        # 核心模块一个都不能少，被裁剪的只能是默写/背诵/写作。
        assert set(CORE_MODULE_KEYS) <= set(item.modules), item.key
        assert set(item.module_labels) <= set(item.modules), item.key
        assert item.teacher_subject_default.strip()


def test_language_modules_only_default_on_for_chinese_and_english():
    """默写 / 背诵 / 写作默认只对语文、英语开启（其余学科搬代码不带这三个入口）。"""
    enabled = {item.key: set(item.modules) for item in SUBJECTS}
    for key in ("chinese", "english"):
        assert set(LANGUAGE_MODULE_KEYS) <= enabled[key], key
    for item in SUBJECTS:
        if item.key in {"chinese", "english"}:
            continue
        assert not (set(LANGUAGE_MODULE_KEYS) & enabled[item.key]), item.key


def test_subject_module_labels_are_declared_inside_enabled_modules():
    chinese = get_subject("chinese")
    assert chinese.module_labels == {
        "dictation": "古诗文默写",
        "recite": "课文背诵",
        "writing": "作文训练",
    }
    assert get_subject("english").module_labels == {}


# 2. 归一与校验


def test_normalize_subject_key_falls_back_to_default():
    assert normalize_subject_key(None) == DEFAULT_SUBJECT_KEY
    assert normalize_subject_key("") == DEFAULT_SUBJECT_KEY
    assert normalize_subject_key("   ") == DEFAULT_SUBJECT_KEY
    assert normalize_subject_key("astrology") == DEFAULT_SUBJECT_KEY
    assert normalize_subject_key(DEFAULT_SUBJECT_KEY) == DEFAULT_SUBJECT_KEY


def test_normalize_subject_key_accepts_key_case_and_subject_name():
    assert normalize_subject_key("MATH") == "math"
    assert normalize_subject_key(" Chinese ") == "chinese"
    # 老师直接填学科名或「初中语文」也能识别，读路径不该因此打空界面。
    assert normalize_subject_key("语文") == "chinese"
    assert normalize_subject_key("初中语文") == "chinese"
    assert normalize_subject_key("道德与法治") == "politics"


def test_is_valid_subject_key_is_strict():
    assert is_valid_subject_key("math") is True
    assert is_valid_subject_key(" MATH ") is True
    # 学科名不算合法 key，写路径只认注册表 key。
    assert is_valid_subject_key("语文") is False
    assert is_valid_subject_key("astrology") is False
    assert is_valid_subject_key(None) is False


def test_list_subjects_returns_independent_copies():
    options = list_subjects()
    assert len(options) == 11
    options[0]["labels"]["score_total"] = "被改坏了"
    options[0]["modules"].append("ghost")
    assert get_subject("chinese").labels["score_total"] == "语文总分"
    assert "ghost" not in get_subject("chinese").modules


def test_settings_patch_rejects_unknown_subject_key():
    assert SettingsPatch(subject_key=" Chinese ").subject_key == "chinese"
    assert SettingsPatch(subject_key=None).subject_key is None
    try:
        SettingsPatch(subject_key="astrology")
    except ValueError as error:
        assert "学科必须是内置学科之一" in str(error)
        assert "chinese" in str(error)
    else:  # pragma: no cover - 走到这里说明校验没生效
        raise AssertionError("非法学科 key 应当被拒绝")


def test_settings_read_normalizes_dirty_subject_key():
    assert SettingsRead().subject_key == DEFAULT_SUBJECT_KEY
    assert SettingsRead(subject_key="math").subject_key == "math"
    # 库里若存了旧值/脏值，读出来回退默认学科而不是报错。
    assert SettingsRead(subject_key="astrology").subject_key == DEFAULT_SUBJECT_KEY


# 3. 接口行为


def test_subjects_endpoint_reports_not_chosen_on_fresh_install(tmp_path):
    with _client(tmp_path) as client:
        response = client.get("/api/v1/subjects", headers=_headers())
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["chosen"] is False
        assert payload["current"] == DEFAULT_SUBJECT_KEY
        assert payload["default"] == DEFAULT_SUBJECT_KEY
        assert [item["key"] for item in payload["subjects"]] == list(SUBJECT_KEYS)
        chinese = next(item for item in payload["subjects"] if item["key"] == "chinese")
        assert chinese["label"] == "语文"
        assert chinese["teacher_subject_default"] == "初中语文"
        assert chinese["module_labels"]["dictation"] == "古诗文默写"
        assert set(chinese["labels"]) == set(LABEL_KEYS)


def test_saving_subject_key_marks_install_as_chosen(tmp_path):
    with _client(tmp_path) as client:
        saved = client.patch("/api/v1/settings", headers=_headers(), json={"subject_key": "chinese"})
        assert saved.status_code == 200, saved.text
        assert saved.json()["subject_key"] == "chinese"
        assert client.get("/api/v1/settings", headers=_headers()).json()["subject_key"] == "chinese"

        payload = client.get("/api/v1/subjects", headers=_headers()).json()
        assert payload["chosen"] is True
        assert payload["current"] == "chinese"


def test_unrelated_setting_patch_does_not_mark_subject_as_chosen(tmp_path):
    """只改称呼不该算「选过学科」，否则首次进入的选科弹窗会被跳过。"""
    with _client(tmp_path) as client:
        client.patch("/api/v1/settings", headers=_headers(), json={"user_address": "冯老师"})
        payload = client.get("/api/v1/subjects", headers=_headers()).json()
        assert payload["chosen"] is False
        assert payload["current"] == DEFAULT_SUBJECT_KEY


def test_unknown_subject_key_is_rejected_over_http(tmp_path):
    with _client(tmp_path) as client:
        response = client.patch("/api/v1/settings", headers=_headers(), json={"subject_key": "astrology"})
        assert response.status_code == 422
        assert "学科必须是内置学科之一" in response.text
        assert client.get("/api/v1/subjects", headers=_headers()).json()["chosen"] is False


def test_dirty_subject_key_in_database_falls_back(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    with TestClient(app) as client:
        from backend.app.models import AppSetting

        with app.state.session_factory() as db:
            db.add(AppSetting(key="subject_key", value_json="astrology"))
            db.commit()
        assert client.get("/api/v1/settings", headers=_headers()).json()["subject_key"] == DEFAULT_SUBJECT_KEY
        payload = client.get("/api/v1/subjects", headers=_headers()).json()
        assert payload["current"] == DEFAULT_SUBJECT_KEY
        # 库里写过就算老师选过，不再弹窗；只是取值被归一为默认学科。
        assert payload["chosen"] is True


# 4. 英语行为逐位不变


def test_english_labels_and_question_types_unchanged():
    english = get_subject("english")
    assert english.labels == ENGLISH_LABELS
    assert english.question_types == ENGLISH_QUESTION_TYPES
    assert english.teacher_subject_default == "初中英语"
    assert set(english.modules) == VALID_MODULE_KEYS


def test_default_settings_keep_legacy_subject_text_and_data_keys(tmp_path):
    """数据层键名不动：subject 仍是「英语」，只是多了一个 subject_key。"""
    assert SettingsRead().subject == "英语"
    with _client(tmp_path) as client:
        payload = client.get("/api/v1/settings", headers=_headers()).json()
        assert payload["subject"] == "英语"
        assert payload["subject_key"] == DEFAULT_SUBJECT_KEY
