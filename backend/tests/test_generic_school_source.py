"""补丁 B：通用 MCP 学校数据源。

覆盖四件事：
1. 配置式字段映射（候选路径、枚举归一、缺失不补 0）；
2. 配置解析与校验（保留标识、令牌档案绑定、遮蔽回显）；
3. 自定义数据源端到端同步（取数 → 统一 payload → 复用既有入库路径）；
4. 路由泛化（``sources/{key}/*``）与 ``moni/*`` 兼容别名。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.auth import TOKEN
from backend.app.config import Settings
from backend.app.database import create_session_factory
from backend.app.factory import create_app
from backend.app.models import (
    Exam,
    ExamQuestion,
    ExamScore,
    Student,
    StudentItemResult,
    Term,
)
from backend.app.services.plugin_manager import PluginManager
from backend.app.services.school_sources import (
    MONI_SOURCE_KEY,
    SourceConfigError,
    get_source_row,
    normalize_source_key,
    parse_config,
)
from backend.app.services.school_sources.config import display_config
from backend.app.services.school_sources.fields import FieldMap, FieldMapError, pick
from backend.app.services.school_sources.generic_mcp import GenericMcpSource
from backend.app.services.school_sources.mcp_text import mcp_rows, mcp_text


# --------------------------------------------------------------------------
# 1. 字段映射
# --------------------------------------------------------------------------


def test_field_map_takes_first_non_blank_candidate():
    spec = {"path": ["studentId", "student_no", "id"]}
    assert pick({"student_no": "20260001"}, spec) == "20260001"
    assert pick({"studentId": "s-1", "student_no": "20260001"}, spec) == "s-1"
    assert pick({}, spec) is None


def test_field_map_enum_normalises_tenant_specific_values():
    spec = {"path": "tier", "enum": {"甲": "A", "乙": "B"}}
    assert pick({"tier": "甲"}, spec) == "A"
    # 未命中枚举时保留原值，不做猜测性映射。
    assert pick({"tier": "C"}, spec) == "C"


def test_field_map_blank_is_missing_but_zero_is_a_real_value():
    spec = "score"
    assert pick({"score": 0}, spec) == 0
    assert pick({"score": ""}, spec) is None
    assert pick({"score": "  "}, spec) is None
    assert pick({"score": []}, spec) is None


def test_field_map_required_field_raises_with_field_name():
    spec = {"path": ["examId"], "required": True}
    with pytest.raises(FieldMapError) as excinfo:
        pick({}, spec, label="exam.external_id")
    assert "exam.external_id" in str(excinfo.value)


def test_field_map_supports_index_and_quoted_paths():
    assert pick({"items": [{"score": 4}]}, "items[0].score") == 4
    assert pick({"a": {"b c": 7}}, 'a["b c"]') == 7
    assert pick({"a": {"b": 1}}, "$.a.b") == 1


def test_field_map_value_returns_default_for_unconfigured_field():
    field_map = FieldMap({"student.name": "name"})
    assert field_map.has("student.name") is True
    assert field_map.has("student.gender") is False
    assert field_map.value({"gender": "女"}, "student.gender") is None


# --------------------------------------------------------------------------
# 2. 配置解析与校验
# --------------------------------------------------------------------------


def _base_config(**overrides):
    config = {
        "endpoint": "https://mcp.example.edu/api/mcp",
        "headers": {"X-Tenant": "hz-001", "X-Api-Key": "plain-secret"},
        "auth": {"type": "bearer", "token_profile": "school-hz-001"},
        "paths": {
            "term": "/school/current-term.json",
            "classes": "/classes/.list.jsonl",
            "roster": "/classes/{class_id}/students/.list.jsonl",
            "exams": "/classes/{class_id}/exams/.list.jsonl",
            "questions": "/classes/{class_id}/exams/{exam_id}/questions/.list.jsonl",
            "students": "/classes/{class_id}/exams/{exam_id}/students/.list.jsonl",
        },
        "term": {"external_id": "2026-S1", "name": "2026 学年第一学期", "code": "2026-S1"},
        "subject_filter": {"field": "subjectName", "any_of": ["英语"]},
        "field_map": {
            "term.external_id": ["termId", "term_id"],
            "term.name": "termName",
            "term.code": "termCode",
            "class.external_id": ["classId", "id"],
            "class.name": ["className", "name"],
            "exam.external_id": ["examId", "id"],
            "exam.name": ["examName", "name"],
            "exam.date": "examDate",
            "exam.full_score": "fullScore",
            "question.external_id": ["questionId", "id"],
            "question.no": "questionNo",
            "question.max_score": "maxScore",
            "item.list": "items",
            "item.question_external_id": "questionId",
            "item.score": "score",
            "student.external_id": ["studentId", "id"],
            "student.name": "name",
            "student.student_no": ["studentNo", "studentId"],
            "student.total_score": "totalScore",
            "student.class_rank": "classRank",
        },
    }
    config.update(overrides)
    return config


def test_reserved_source_keys_are_rejected():
    for key in ("moni", "mock"):
        with pytest.raises(SourceConfigError):
            normalize_source_key(key)


def test_normalize_source_key_rejects_uppercase_and_symbols():
    assert normalize_source_key("HZ-001") == "hz-001"
    for key in ("hz 001", "hz/001", "-hz001", "a"):
        with pytest.raises(SourceConfigError):
            normalize_source_key(key)


def test_parse_config_requires_https_endpoint_and_class_path():
    with pytest.raises(SourceConfigError):
        parse_config(_base_config(endpoint="ftp://example.edu"), source_key="hz-001")
    with pytest.raises(SourceConfigError):
        parse_config(_base_config(paths={"exams": "/e.jsonl"}), source_key="hz-001")
    with pytest.raises(SourceConfigError):
        parse_config(_base_config(paths={"classes": "/c.jsonl"}), source_key="hz-001")


def test_parse_config_binds_token_profile_to_source_key():
    """令牌档案名与数据源标识绑定，避免两个数据源互相覆盖令牌。"""
    parse_config(_base_config(), source_key="hz-001")
    with pytest.raises(SourceConfigError) as excinfo:
        parse_config(_base_config(), source_key="hz-002")
    assert "school-hz-002" in str(excinfo.value)


def test_parse_config_rejects_unknown_placeholders_early():
    config = _base_config(paths={"classes": "/c.jsonl", "exams": "/e/{school}.jsonl"})
    parsed = parse_config(config, source_key="hz-001")
    with pytest.raises(SourceConfigError):
        parsed.expand(parsed.path("exams"), class_id="c-1")


def test_display_config_masks_secret_headers_but_keeps_others():
    shown = display_config(_base_config(), source_key="hz-001")
    assert shown["headers"]["X-Api-Key"] == "********"
    assert shown["headers"]["X-Tenant"] == "hz-001"
    assert shown["auth"] == {"type": "bearer", "token_profile": "school-hz-001"}
    assert "plain-secret" not in json.dumps(shown, ensure_ascii=False)


# --------------------------------------------------------------------------
# 3. MCP 返回解码
# --------------------------------------------------------------------------


def test_mcp_rows_accepts_json_jsonl_and_wrapped_payloads():
    assert mcp_rows('[{"a": 1}]') == [{"a": 1}]
    assert mcp_rows('{"a": 1}\n{"a": 2}') == [{"a": 1}, {"a": 2}]
    assert mcp_rows('{"rows": [{"a": 1}]}') == [{"a": 1}]
    assert mcp_rows("") == []
    assert mcp_rows("not json") == []


def test_mcp_text_joins_text_chunks_and_ignores_other_types():
    result = {"content": [
        {"type": "text", "text": "a"},
        {"type": "image", "data": "x"},
        {"type": "text", "text": "b"},
    ]}
    assert mcp_text(result) == "a\nb"
    assert mcp_text({}) == ""


# --------------------------------------------------------------------------
# 4. 自定义数据源端到端同步
# --------------------------------------------------------------------------

TERM_ROW = {"termId": "2026-S1", "termName": "2026 学年第一学期", "termCode": "2026-S1"}
CLASS_ROWS = [{"classId": "c-1", "className": "九年级1班"}]
ROSTER_ROWS = [{"studentId": "s-1", "name": "学生甲", "studentNo": "20260001"}]
EXAM_ROWS = [
    {"examId": "e-1", "examName": "第一次月考", "subjectName": "英语",
     "examDate": "2026-10-08", "fullScore": 100},
    {"examId": "e-2", "examName": "数学月考", "subjectName": "数学", "fullScore": 100},
]
QUESTION_ROWS = [{"questionId": "q-1", "questionNo": "1", "maxScore": 5}]
SCORE_ROWS = [
    {"studentId": "s-1", "name": "学生甲", "studentNo": "20260001",
     "totalScore": 88, "classRank": 3, "items": [{"questionId": "q-1", "score": 4}]},
    {"studentId": "s-2", "studentNo": "20260002"},  # 缺姓名：应跳过并记警告
]

SERVER = {
    "/school/current-term.json": [TERM_ROW],
    "/classes/.list.jsonl": CLASS_ROWS,
    "/classes/c-1/students/.list.jsonl": ROSTER_ROWS,
    "/classes/c-1/exams/.list.jsonl": EXAM_ROWS,
    "/classes/c-1/exams/e-1/questions/.list.jsonl": QUESTION_ROWS,
    "/classes/c-1/exams/e-1/students/.list.jsonl": SCORE_ROWS,
}


def _install_fake_mcp(monkeypatch, server=None):
    """把 MCP 宿主换成按路径返回 JSONL 的假服务，取数逻辑仍走真实适配器。"""
    files = dict(SERVER if server is None else server)

    def call_tool(self, plugin_id, tool_name, arguments):
        path = arguments.get("file_path") or arguments.get("path") or ""
        rows = files.get(path)
        if rows is None:
            return {"content": []}
        text = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
        return {"content": [{"type": "text", "text": text}]}

    def health_check(self, plugin_id):
        return {"health": "ready", "tools": [{"name": "vfs_list"}]}

    monkeypatch.setattr(PluginManager, "call_tool", call_tool)
    monkeypatch.setattr(PluginManager, "health_check", health_check)


def _source(settings, *, config=None, name="杭州某校英语数据", enabled=True):
    return GenericMcpSource(
        settings, source_key="hz-001", name=name,
        raw_config=config or _base_config(), enabled=enabled)


def test_generic_source_sync_writes_same_payload_shape_as_moni(tmp_path, monkeypatch):
    _install_fake_mcp(monkeypatch)
    settings = Settings(data_dir=tmp_path)
    summary = _source(settings).sync()

    assert summary["completed"] is True
    assert summary["exams"] == 1  # 只同步英语那一场
    assert summary["students"] == 1
    assert summary["questions"] == 1
    assert summary["roster_students"] == 1
    assert any("缺少姓名" in item for item in summary["warnings"])

    factory = create_session_factory(settings.database_url)
    with factory() as session:
        term = session.scalar(select(Term).where(Term.code == "2026-S1"))
        assert term is not None
        exam = session.scalar(select(Exam))
        assert exam is not None and exam.name == "第一次月考"
        student = session.scalar(select(Student))
        assert student is not None and student.name == "学生甲"
        assert session.scalar(select(ExamScore)) is not None
        item = session.scalar(select(StudentItemResult))
        assert item is not None and item.score == 4


def test_generic_source_dry_run_writes_nothing(tmp_path, monkeypatch):
    _install_fake_mcp(monkeypatch)
    settings = Settings(data_dir=tmp_path)
    summary = _source(settings).sync(dry_run=True)

    assert summary["dry_run"] is True
    assert "completed" not in summary
    from backend.app.database import run_migrations

    run_migrations(settings.database_url)
    factory = create_session_factory(settings.database_url)
    with factory() as session:
        # 迁移会建一个内置的「初始学期」，这里只断言同步没有写入任何来源数据。
        assert session.scalar(select(Term).where(Term.code == "2026-S1")) is None
        assert session.scalar(select(Exam)) is None
        assert session.scalar(select(Student)) is None


def test_generic_source_skips_exam_without_full_score(tmp_path, monkeypatch):
    server = dict(SERVER)
    server["/classes/c-1/exams/.list.jsonl"] = [
        {"examId": "e-9", "examName": "无满分考试", "subjectName": "英语"},
    ]
    server["/classes/c-1/exams/e-9/students/.list.jsonl"] = SCORE_ROWS[:1]
    _install_fake_mcp(monkeypatch, server)

    summary = _source(Settings(data_dir=tmp_path)).sync(dry_run=True)
    assert summary["exams"] == 0
    assert any("缺少满分" in item for item in summary["warnings"])


def test_generic_source_requires_term_before_importing(tmp_path, monkeypatch):
    """没有学期归属时宁可报错，也不把数据挂到「当前学期」上。"""
    server = dict(SERVER)
    server["/school/current-term.json"] = []
    _install_fake_mcp(monkeypatch, server)
    config = _base_config()
    config.pop("term")
    with pytest.raises(SourceConfigError) as excinfo:
        _source(Settings(data_dir=tmp_path), config=config).sync(dry_run=True)
    assert "无法确定学期" in str(excinfo.value)


def test_generic_source_refuses_when_disabled(tmp_path, monkeypatch):
    _install_fake_mcp(monkeypatch)
    from backend.app.services.school_sources.generic_mcp import GenericSourceError

    with pytest.raises(GenericSourceError):
        _source(Settings(data_dir=tmp_path), enabled=False).sync()


# --------------------------------------------------------------------------
# 5. 路由：sources/{key}/* 与 moni/* 兼容别名
# --------------------------------------------------------------------------


def _client(tmp_path):
    return TestClient(create_app(Settings(data_dir=tmp_path)))


def _headers():
    return {"Authorization": f"Bearer {TOKEN}"}


def test_sources_list_includes_builtin_moni_and_masks_secrets(tmp_path, monkeypatch):
    _install_fake_mcp(monkeypatch)
    with _client(tmp_path) as client:
        created = client.post(
            "/api/v1/school-sync/sources", headers=_headers(),
            json={"source_key": "hz-001", "name": "杭州某校英语数据",
                  "config": _base_config(), "bearer_token": "Bearer school-token"},
        )
        assert created.status_code == 201, created.text
        assert created.json()["token_configured"] is True
        assert created.json()["key_hint"].endswith("oken")

        listed = client.get("/api/v1/school-sync/sources", headers=_headers())
        assert listed.status_code == 200, listed.text
        body = listed.json()
        keys = {item["source_key"] for item in body}
        assert keys == {MONI_SOURCE_KEY, "hz-001"}
        moni = next(item for item in body if item["source_key"] == MONI_SOURCE_KEY)
        assert moni["builtin"] is True

        text = json.dumps(body, ensure_ascii=False)
        assert "plain-secret" not in text
        assert "school-token" not in text


def test_source_config_round_trip_materialises_plugin(tmp_path, monkeypatch):
    _install_fake_mcp(monkeypatch)
    with _client(tmp_path) as client:
        client.post(
            "/api/v1/school-sync/sources", headers=_headers(),
            json={"source_key": "hz-001", "name": "杭州某校英语数据", "config": _base_config()},
        )
        manifest = tmp_path / "plugins" / "installed" / "school-hz-001" / ".teachmate-plugin" / "plugin.json"
        assert manifest.is_file()
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        assert payload["id"] == "school-hz-001"
        assert payload["runtime"]["url"] == "https://mcp.example.edu/api/mcp"

        fetched = client.get("/api/v1/school-sync/sources/hz-001/config", headers=_headers())
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["config"]["headers"]["X-Api-Key"] == "********"

        tested = client.post("/api/v1/school-sync/sources/hz-001/test", headers=_headers())
        assert tested.status_code == 200, tested.text
        assert tested.json()["status"] == "ok"


def test_invalid_config_is_rejected_and_previous_one_survives(tmp_path, monkeypatch):
    _install_fake_mcp(monkeypatch)
    with _client(tmp_path) as client:
        client.post(
            "/api/v1/school-sync/sources", headers=_headers(),
            json={"source_key": "hz-001", "name": "杭州某校英语数据", "config": _base_config()},
        )
        bad = _base_config(endpoint="ftp://example.edu")
        response = client.put(
            "/api/v1/school-sync/sources/hz-001/config", headers=_headers(),
            json={"name": "改坏了", "config": bad},
        )
        assert response.status_code == 422

        kept = client.get("/api/v1/school-sync/sources/hz-001/config", headers=_headers()).json()
        assert kept["name"] == "杭州某校英语数据"
        assert kept["config"]["endpoint"] == "https://mcp.example.edu/api/mcp"


def test_missing_required_field_mapping_is_rejected_before_saving(tmp_path, monkeypatch):
    _install_fake_mcp(monkeypatch)
    config = _base_config()
    config["field_map"].pop("student.name")
    with _client(tmp_path) as client:
        response = client.post(
            "/api/v1/school-sync/sources", headers=_headers(),
            json={"source_key": "hz-001", "name": "缺字段", "config": config},
        )
        assert response.status_code == 422
        assert "student.name" in response.json()["detail"]


def test_reserved_key_cannot_be_created_or_deleted(tmp_path, monkeypatch):
    _install_fake_mcp(monkeypatch)
    with _client(tmp_path) as client:
        for key in (MONI_SOURCE_KEY, "mock"):
            created = client.post(
                "/api/v1/school-sync/sources", headers=_headers(),
                json={"source_key": key, "name": key, "config": _base_config()},
            )
            assert created.status_code == 422
            assert "保留标识" in created.json()["detail"]

        # 内置 MONI 不能被删掉。
        assert client.delete(
            "/api/v1/school-sync/sources/moni", headers=_headers()).status_code == 422


def test_deleting_custom_source_removes_row_plugin_and_token(tmp_path, monkeypatch):
    _install_fake_mcp(monkeypatch)
    from backend.app.agent.keyvault import load_api_key

    with _client(tmp_path) as client:
        client.post(
            "/api/v1/school-sync/sources", headers=_headers(),
            json={"source_key": "hz-001", "name": "杭州某校英语数据",
                  "config": _base_config(), "bearer_token": "school-token"},
        )
        plugin_dir = tmp_path / "plugins" / "installed" / "school-hz-001"
        assert plugin_dir.exists()
        assert load_api_key("school-hz-001", data_dir=tmp_path) == "school-token"

        removed = client.delete("/api/v1/school-sync/sources/hz-001", headers=_headers())
        assert removed.status_code == 200, removed.text
        assert not plugin_dir.exists()
        assert load_api_key("school-hz-001", data_dir=tmp_path) is None
        with client.app.state.session_factory() as session:
            assert get_source_row(session, "hz-001") is None

        assert client.delete(
            "/api/v1/school-sync/sources/hz-001", headers=_headers()).status_code == 404


def test_source_sync_route_dry_run_and_unknown_key(tmp_path, monkeypatch):
    _install_fake_mcp(monkeypatch)
    with _client(tmp_path) as client:
        client.post(
            "/api/v1/school-sync/sources", headers=_headers(),
            json={"source_key": "hz-001", "name": "杭州某校英语数据", "config": _base_config()},
        )
        dry = client.post(
            "/api/v1/school-sync/sources/hz-001/sync?dry_run=true", headers=_headers())
        assert dry.status_code == 200, dry.text
        assert dry.json()["summary"]["dry_run"] is True

        unknown = client.post("/api/v1/school-sync/sources/nope/sync", headers=_headers())
        assert unknown.status_code == 404

        missing = client.get("/api/v1/school-sync/sources/nope/config", headers=_headers())
        assert missing.status_code == 404


def test_source_sync_route_keeps_registered_config_and_can_run_twice(tmp_path, monkeypatch):
    """导入路径不得改动已登记数据源的 kind/config，否则第二次同步就解析不到适配器。"""
    _install_fake_mcp(monkeypatch)
    with _client(tmp_path) as client:
        client.post(
            "/api/v1/school-sync/sources", headers=_headers(),
            json={"source_key": "hz-001", "name": "杭州某校英语数据", "config": _base_config()},
        )
        first = client.post("/api/v1/school-sync/sources/hz-001/sync", headers=_headers())
        assert first.status_code == 200, first.text
        summary = first.json()["summary"]
        assert summary["completed"] is True
        assert summary["exams"] == 1 and summary["roster_students"] == 1

        with client.app.state.session_factory() as session:
            row = get_source_row(session, "hz-001")
            assert row.kind == "generic_mcp"
            assert row.config_json["endpoint"] == "https://mcp.example.edu/api/mcp"
            assert "paths" in row.config_json and "field_map" in row.config_json
            assert session.scalar(select(Term).where(Term.code == "2026-S1")) is not None
            assert session.scalar(select(Exam)) is not None
            assert session.scalar(select(ExamScore)) is not None

        second = client.post("/api/v1/school-sync/sources/hz-001/sync", headers=_headers())
        assert second.status_code == 200, second.text
        assert second.json()["summary"]["exams"] == 1


def test_auto_registered_sources_are_listed_without_config_errors(tmp_path, monkeypatch):
    """mock / 旧版 mcp 登记行没有字段映射配置，不应在列表里报「配置损坏」。"""
    _install_fake_mcp(monkeypatch)
    with _client(tmp_path) as client:
        payload = client.get("/api/v1/school-sync/mock/payload", headers=_headers()).json()
        assert client.post("/api/v1/school-sync/apply", headers=_headers(), json=payload).status_code == 200

        body = client.get("/api/v1/school-sync/sources", headers=_headers()).json()
        auto = [item for item in body if not item["builtin"] and item["source_key"] != MONI_SOURCE_KEY]
        assert auto, body
        for item in auto:
            assert item["configurable"] is False
            assert "config_error" not in item
            assert item["config"] is None


def test_moni_routes_remain_available_as_compatibility_aliases(tmp_path):
    with _client(tmp_path) as client:
        response = client.get("/api/v1/school-sync/moni/config", headers=_headers())
        assert response.status_code == 200, response.text
        assert "mcpServers" in response.json()
        assert client.post("/api/v1/school-sync/moni/test", headers=_headers()).status_code in (200, 422)


# --------------------------------------------------------------------------
# 6. 值域归一：分类取值口径不同时的映射能力
# --------------------------------------------------------------------------


def test_enum_lookup_ignores_case_and_fullwidth():
    """上游把同一个值写成 elite / ELITE / ＥＬＩＴＥ 都应命中同一条别名。"""
    spec = {"path": "tier", "enum": {"ELITE": "A"}}
    for raw in ("ELITE", "elite", "Elite", "ＥＬＩＴＥ", " elite "):
        assert pick({"tier": raw}, spec) == "A"


def test_enum_keeps_unmapped_value_by_default():
    """默认策略是保留原值：不假装已归一，漏配的别名要能被发现。"""
    spec = {"path": "tier", "enum": {"ELITE": "A"}}
    assert pick({"tier": "KEY"}, spec) == "KEY"


def test_enum_drop_treats_unmapped_as_missing():
    """drop 策略下认不出的取值按缺失处理，继续走下一个候选路径。"""
    spec = {"path": ["tier", "level"], "enum": {"ELITE": "A"}, "enum_missing": "drop"}
    assert pick({"tier": "KEY", "level": "ELITE"}, spec) == "A"
    assert pick({"tier": "KEY"}, spec) is None


def test_enum_bucket_collects_unmapped_values():
    spec = {"path": "tier", "enum": {"ELITE": "A"}, "enum_missing": "bucket",
            "enum_bucket": "未分层"}
    assert pick({"tier": "KEY"}, spec) == "未分层"
    assert pick({"tier": "ELITE"}, spec) == "A"


def test_enum_rejects_unknown_missing_policy():
    with pytest.raises(FieldMapError):
        pick({"tier": "A"}, {"path": "tier", "enum": {"A": "A"}, "enum_missing": "guess"})


def test_list_enum_maps_each_element_and_dedupes():
    """列表字段逐元素查表：整串 str() 去匹配是永远命不中的。"""
    spec = {"path": "tags", "enum": {"甲": "A", "A": "A", "乙": "B"}, "list": True}
    assert pick({"tags": ["甲", "乙"]}, spec) == ["A", "B"]
    # 甲 与 A 都归一到 A，去重后只留一个
    assert pick({"tags": ["甲", "A", "乙"]}, spec) == ["A", "B"]


def test_list_enum_splits_delimited_string_before_mapping():
    """学校用「；」分隔一个字符串传标签时，必须先拆开再逐个归一。"""
    spec = {"path": "tags", "enum": {"宾语从句(that)": "宾语从句"}, "list": True}
    assert pick({"tags": "宾语从句(that)；Object Clause"}, spec) == ["宾语从句", "Object Clause"]


def test_list_enum_drop_marks_empty_list_as_missing():
    spec = {"path": ["tags", "other"], "enum": {"甲": "A"}, "list": True, "enum_missing": "drop"}
    assert pick({"tags": ["乙"], "other": ["甲"]}, spec) == ["A"]
    assert pick({"tags": ["乙"]}, spec) is None


def test_list_enum_passes_nested_structures_through():
    """嵌套结构不做猜测，原样保留——别名表只描述标量取值。

    （打开 ``list`` 时标签会先被规整成字符串列表，那是 ``as_str_list`` 的职责，
    与归一无关；这里只验证不打开 ``list`` 时不会去猜嵌套结构的语义。）
    """
    spec = {"path": "tags", "enum": {"甲": "A"}}
    assert pick({"tags": [{"k": "甲"}, "甲"]}, spec) == [{"k": "甲"}, "A"]


def test_pick_does_not_mutate_the_spec():
    spec = {"path": "tier", "enum": {"ELITE": "A"}}
    pick({"tier": "ELITE"}, spec)
    assert spec == {"path": "tier", "enum": {"ELITE": "A"}}


# --------------------------------------------------------------------------
# 7. 分类值对照表（value_aliases）与分层别名覆盖（tier_aliases）
# --------------------------------------------------------------------------


def test_value_aliases_are_inverted_into_enum_table():
    """教师填的是「统一叫法 → 上游叫法」，解析时反转成引擎使用的 enum 表。"""
    parsed = parse_config(
        _base_config(
            field_map={"question.knowledge": ["knowledgeNodes"]},
            value_aliases={
                "question.knowledge": {"宾语从句": ["宾语从句(that)", "Object Clause"]},
            },
        ),
        source_key="hz-001",
    )
    entry = parsed.field_map.spec["question.knowledge"]
    assert entry["enum"] == {"宾语从句(that)": "宾语从句", "Object Clause": "宾语从句"}
    # 标签字段自动打开逐元素归一，否则逗号分隔的字符串整串去查表必然落空
    assert entry["list"] is True
    assert parsed.value_aliases["question.knowledge"]["宾语从句"] == ["宾语从句(that)", "Object Clause"]


def test_value_aliases_require_field_configured_in_field_map():
    with pytest.raises(SourceConfigError) as excinfo:
        parse_config(
            _base_config(value_aliases={"question.knowledge": {"宾语从句": ["从句"]}}),
            source_key="hz-001",
        )
    assert "还没有在 field_map 里说明读哪一列" in str(excinfo.value)


def test_value_aliases_reject_unknown_field_name():
    """字段名写错时必须报错，否则配了等于没配。"""
    with pytest.raises(SourceConfigError) as excinfo:
        parse_config(
            _base_config(value_aliases={"question.knowlege": {"宾语从句": ["从句"]}}),
            source_key="hz-001",
        )
    assert "不是可用的字段名" in str(excinfo.value)


def test_value_aliases_reject_conflicting_targets():
    """同一个上游叫法被映射成两个统一值时直接报错，不做静默取舍。"""
    with pytest.raises(SourceConfigError) as excinfo:
        parse_config(
            _base_config(
                field_map={"question.knowledge": ["knowledgeNodes"]},
                value_aliases={"question.knowledge": {
                    "宾语从句": ["从句"],
                    "定语从句": ["从句"],
                }},
            ),
            source_key="hz-001",
        )
    assert "只保留一个" in str(excinfo.value)


def test_value_aliases_reject_empty_upstream_list():
    with pytest.raises(SourceConfigError):
        parse_config(
            _base_config(
                field_map={"question.knowledge": ["knowledgeNodes"]},
                value_aliases={"question.knowledge": {"宾语从句": []}},
            ),
            source_key="hz-001",
        )


def test_tier_aliases_only_accept_abcd_targets():
    parsed = parse_config(_base_config(tier_aliases={"甲等": "A"}), source_key="hz-001")
    assert parsed.tier_aliases == {"甲等": "A"}
    with pytest.raises(SourceConfigError) as excinfo:
        parse_config(_base_config(tier_aliases={"甲等": "优"}), source_key="hz-001")
    assert "只能是 A/B/C/D" in str(excinfo.value)


def test_display_config_echoes_aliases_without_secrets():
    shown = display_config(
        _base_config(
            field_map={"question.knowledge": ["knowledgeNodes"]},
            value_aliases={"question.knowledge": {"宾语从句": ["从句"]}},
            tier_aliases={"甲等": "A"},
        ),
        source_key="hz-001",
    )
    assert shown["value_aliases"]["question.knowledge"] == {"宾语从句": ["从句"]}
    assert shown["tier_aliases"] == {"甲等": "A"}


def test_display_config_echoes_raw_field_map_without_alias_enum():
    """回显给前端的 field_map 必须是教师原样填的那一份。

    否则保存一次、读一次，对照表反推出的 enum 就会写进字段映射里，
    教师在设置页看到两份重复配置，越存越乱。
    """
    shown = display_config(
        _base_config(
            field_map={"question.knowledge": ["knowledgeNodes"]},
            value_aliases={"question.knowledge": {"宾语从句": ["从句"]}},
        ),
        source_key="hz-001",
    )
    assert shown["field_map"] == {"question.knowledge": ["knowledgeNodes"]}
    # 引擎内部仍然拿到了归一表，只是不出现在回显里。
    parsed = parse_config(
        _base_config(
            field_map={"question.knowledge": ["knowledgeNodes"]},
            value_aliases={"question.knowledge": {"宾语从句": ["从句"]}},
        ),
        source_key="hz-001",
    )
    assert parsed.field_map.spec["question.knowledge"]["enum"] == {"从句": "宾语从句"}
    assert parsed.field_map.spec["question.knowledge"]["list"] is True


# --------------------------------------------------------------------------
# 8. 分层别名与分层线推导（与 MONI 同口径）
# --------------------------------------------------------------------------


def test_normalize_tier_uses_default_table_and_config_override():
    from backend.app.services.school_sources.tiers import normalize_tier

    assert normalize_tier("ELITE") == "A"
    assert normalize_tier("good") == "C"
    assert normalize_tier("A 层") == "A"
    assert normalize_tier("甲等") is None
    assert normalize_tier("甲等", {"甲等": "A"}) == "A"
    assert normalize_tier(None) is None


def test_derive_tier_cutoffs_snaps_and_keeps_missing_levels_empty():
    from backend.app.services.school_sources.tiers import derive_tier_cutoffs

    result = derive_tier_cutoffs(
        [("A", 92.3), ("A", 88.0), ("B", 78.26), (None, 60.0), ("C", None)],
        full_score=100,
    )
    # 取该层最低分并吸附 0.5 分；认不出层与缺分的记录不参与
    assert result == {"tier_a_cutoff": 88.0, "tier_b_cutoff": 78.5, "tier_c_cutoff": None}


def test_derive_tier_cutoffs_enforces_monotonic_lines():
    from backend.app.services.school_sources.tiers import derive_tier_cutoffs

    result = derive_tier_cutoffs([("A", 50.0), ("B", 90.0)], full_score=100)
    assert result["tier_a_cutoff"] == 90.0
    assert result["tier_b_cutoff"] == 90.0


# --------------------------------------------------------------------------
# 9. 分类字段端到端：换学校不再丢分析维度
# --------------------------------------------------------------------------


def _category_config(**overrides):
    """在基础配置上补分类字段映射、分类值对照表与分层别名。"""
    config = _base_config()
    field_map = dict(config["field_map"])
    field_map.update({
        "question.difficulty": "difficulty",
        "question.cognitive": "cognitive",
        "question.knowledge": ["knowledgeNodes"],
        "question.ability": ["abilityNodes"],
        "question.pitfall": ["pitfallTags"],
        "question.teaching_block": ["teachingBlocks"],
        "item.selected_option": "selectedOption",
        "item.time_spent_ms": "timeSpentMs",
        "item.modify_count": "modifyCount",
        "item.hesitation_time_ms": "hesitationTimeMs",
        "item.pitfall": "pitfallTags",
        "item.teaching_block": "teachingBlocks",
        "exam.kind": "examKind",
        "student.tier": "tier",
    })
    config["field_map"] = field_map
    config["value_aliases"] = {
        "question.knowledge": {"宾语从句": ["宾语从句(that)", "Object Clause"]},
    }
    config["tier_aliases"] = {"甲等": "A", "乙等": "B", "丙等": "C"}
    config.update(overrides)
    return config


def _category_server():
    server = dict(SERVER)
    server["/classes/c-1/exams/.list.jsonl"] = [
        {"examId": "e-1", "examName": "入学分班考", "subjectName": "英语",
         "examDate": "2026-09-01", "fullScore": 100, "examKind": "入学考"},
    ]
    server["/classes/c-1/exams/e-1/questions/.list.jsonl"] = [
        {"questionId": "q-1", "questionNo": "1", "maxScore": 5,
         "difficulty": "中档", "cognitive": "理解",
         "knowledgeNodes": "宾语从句(that)；Object Clause",
         "abilityNodes": ["语言运用"], "pitfallTags": ["漏 that"],
         "teachingBlocks": ["宾语从句"]},
    ]
    server["/classes/c-1/exams/e-1/students/.list.jsonl"] = [
        {"studentId": "s-1", "name": "学生甲", "studentNo": "20260001",
         "totalScore": 92, "tier": "甲等",
         "items": [{"questionId": "q-1", "score": 4, "selectedOption": "B",
                    "timeSpentMs": 45000, "modifyCount": 1, "hesitationTimeMs": 8000,
                    "pitfallTags": ["时态混淆"], "teachingBlocks": ["宾语从句"]}]},
        {"studentId": "s-2", "name": "学生乙", "studentNo": "20260002",
         "totalScore": 78, "tier": "乙等"},
        {"studentId": "s-3", "name": "学生丙", "studentNo": "20260003",
         "totalScore": 65, "tier": "丙等"},
    ]
    return server


def test_generic_source_writes_question_category_fields(tmp_path, monkeypatch):
    """题目的难度/认知层级/知识点/能力点/易错/教学模块都要能同步进来。"""
    _install_fake_mcp(monkeypatch, _category_server())
    settings = Settings(data_dir=tmp_path)
    summary = _source(settings, config=_category_config()).sync()
    assert summary["completed"] is True
    assert summary["exams"] == 1

    factory = create_session_factory(settings.database_url)
    with factory() as session:
        question = session.scalar(select(ExamQuestion))
        assert question is not None
        assert question.difficulty_level == "中档"
        assert question.cognitive_level == "理解"
        assert question.ability_nodes_json == ["语言运用"]
        assert question.pitfall_tags_json == ["漏 that"]
        assert question.teaching_blocks_json == ["宾语从句"]
        # 两种上游叫法都归一到同一个知识点：跨校才聚合得起来
        assert question.knowledge_nodes_json == ["宾语从句"]


def test_generic_source_maps_exam_kind_and_derives_tier_cutoffs(tmp_path, monkeypatch):
    """入学考不能被当成普通月考；分层线按 A/B/C 各层最低分推导。"""
    _install_fake_mcp(monkeypatch, _category_server())
    settings = Settings(data_dir=tmp_path)
    _source(settings, config=_category_config()).sync()

    factory = create_session_factory(settings.database_url)
    with factory() as session:
        exam = session.scalar(select(Exam))
        assert exam is not None
        assert exam.exam_kind == "entrance"
        assert exam.tier_a_cutoff == 92.0
        assert exam.tier_b_cutoff == 78.0
        assert exam.tier_c_cutoff == 65.0


def test_generic_source_writes_item_detail_fields(tmp_path, monkeypatch):
    """逐题作答的选项、耗时、修改次数、犹豫时间与标签也要落库。"""
    _install_fake_mcp(monkeypatch, _category_server())
    settings = Settings(data_dir=tmp_path)
    _source(settings, config=_category_config()).sync()

    factory = create_session_factory(settings.database_url)
    with factory() as session:
        item = session.scalar(select(StudentItemResult))
        assert item is not None
        assert item.score == 4
        assert item.selected_option == "B"
        assert item.time_spent_ms == 45000
        assert item.modify_count == 1
        assert item.hesitation_time_ms == 8000
        assert item.pitfall_tags_json == ["时态混淆"]
        assert item.teaching_blocks_json == ["宾语从句"]


def test_generic_source_warns_when_tiers_cannot_be_normalized(tmp_path, monkeypatch):
    """分层字段有值但一个都认不出时，必须提示而不是悄悄留空。"""
    config = _category_config()
    config.pop("tier_aliases")
    _install_fake_mcp(monkeypatch, _category_server())
    settings = Settings(data_dir=tmp_path)
    summary = _source(settings, config=config).sync(dry_run=True)

    assert any("没有一个能归一到 A/B/C/D" in item for item in summary["warnings"])


def test_explicit_tier_cutoffs_win_over_derived(tmp_path, monkeypatch):
    """上游直接给了分层线就按上游的来，推导只补空缺。"""
    server = _category_server()
    server["/classes/c-1/exams/.list.jsonl"] = [
        {"examId": "e-1", "examName": "入学分班考", "subjectName": "英语",
         "examDate": "2026-09-01", "fullScore": 100, "examKind": "入学考",
         "tierACutoff": 95, "tierBCutoff": 80, "tierCCutoff": 60},
    ]
    config = _category_config()
    config["field_map"] = dict(config["field_map"])
    config["field_map"].update({
        "exam.tier_a_cutoff": "tierACutoff",
        "exam.tier_b_cutoff": "tierBCutoff",
        "exam.tier_c_cutoff": "tierCCutoff",
    })
    _install_fake_mcp(monkeypatch, server)
    settings = Settings(data_dir=tmp_path)
    _source(settings, config=config).sync()

    factory = create_session_factory(settings.database_url)
    with factory() as session:
        exam = session.scalar(select(Exam))
        assert exam is not None
        assert (exam.tier_a_cutoff, exam.tier_b_cutoff, exam.tier_c_cutoff) == (95.0, 80.0, 60.0)
