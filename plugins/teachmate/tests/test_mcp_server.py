"""MCP 服务器调度测试（无网络，用 FakeClient 注入）。

覆盖：12 个工具的工具名/路由映射、结果格式化、错误码映射、未知工具拒绝、
令牌存取往返。真实 HTTP 配对由后端 test_l2_plugin_api.py 覆盖。
"""
from __future__ import annotations

import os
import sys

import pytest

from server.client import PluginClientError  # noqa: E402
from server.schemas import TOOL_SPECS, TOOL_ROUTES  # noqa: E402
from server.tools import dispatch  # noqa: E402
from server import error  # noqa: E402
from server import auth  # noqa: E402


class FakeClient:
    def __init__(self, *, fail_with=None):
        self.fail_with = fail_with
        self.calls = []

    def call(self, method, path, params=None):
        self.calls.append((method, path, params))
        if self.fail_with:
            raise self.fail_with
        if path.endswith("/status"):
            return {"health": "ok", "app_version": "0.9.0-beta.2",
                    "schema_revision": "20260817_0016", "api_version": "1.0",
                    "capabilities": ["get_exam_snapshot", "list_teaching_scopes"]}
        if path.startswith("/api/v1/plugin/exams/") and path.endswith("/snapshot"):
            return {"term_id": 1, "exam": {"exam_id": 5, "exam_name": "摸底考",
                                            "full_score": 120, "present_count": 1,
                                            "absent_count": 0, "average": 96.0,
                                            "highest": 96, "lowest": 96},
                    "class_metrics": [], "data_quality": {"missing_scores": 0}}
        if path.startswith("/api/v1/plugin/students/"):
            return {"student_anon_id": "abc", "term_id": 1, "class_id": 2,
                    "weak_tags": ["时态"], "identifiable": False, "exams": [],
                    "latest_score": None}
        if path.startswith("/api/v1/plugin/review-plans/facts"):
            return {"term_id": 1, "exam_id": 5, "dimension_averages": [
                        {"dimension_id": 1, "dimension_name": "写作", "average_score_rate": 83.0}],
                    "common_error_dimensions": [], "weak_knowledge_points": ["时态"],
                    "coverage": {"present_count": 1}}
        if path.startswith("/api/v1/plugin/materials/"):
            return {"material_id": 9, "title": "考点", "page": 1, "page_size": 4000,
                    "total_pages": 1, "total_chars": 12, "text": "已确认的正文内容。",
                    "truncated": False}
        if path.startswith("/api/v1/plugin/materials"):
            return [{"material_id": 9, "title": "考点", "char_count": 12}]
        if path.startswith("/api/v1/plugin/evidence/"):
            return {"evidence_id": "ev-1", "evidence_type": "stat",
                    "local_fact": {"average": 96}, "source": {"entity": "exam_scores"},
                    "calculation": {"numerator": 96, "denominator": 1},
                    "contains_personal_data": False}
        if path.endswith("/scopes"):
            return {"term_id": 1, "terms": [{"id": 1, "name": "T"}], "classes": [],
                    "exams": [{"id": 5, "name": "摸底考"}]}
        return {"ok": True}


def _text(result):
    return result["content"][0]["text"]


class TestToolRegistry:
    def test_twelve_tools_registered(self):
        assert len(TOOL_SPECS) == 12
        names = {t["name"] for t in TOOL_SPECS}
        assert names == set(TOOL_ROUTES.keys())

    def test_each_tool_has_input_schema_and_sensitivity(self):
        for spec in TOOL_SPECS:
            assert "inputSchema" in spec
            assert spec["annotations"]["sensitivity"] in {"low", "medium", "high"}


class TestDispatch:
    def test_status_formatted(self):
        c = FakeClient()
        res = dispatch("get_teachmate_status", {}, c)
        assert res["isError"] is False
        assert "get_exam_snapshot" in _text(res)
        assert c.calls[0][0] == "GET"

    def test_scopes_uses_term_query(self):
        c = FakeClient()
        dispatch("list_teaching_scopes", {"term_id": 7}, c)
        assert c.calls[0][2] == {"term_id": 7}

    def test_snapshot_path_substitution(self):
        c = FakeClient()
        dispatch("get_exam_snapshot", {"exam_id": 5, "term_id": 1, "class_id": 2}, c)
        assert c.calls[0][1] == "/api/v1/plugin/exams/5/snapshot"
        assert c.calls[0][2] == {"term_id": 1, "class_id": 2}

    def test_report_catalog_passes_filters(self):
        c = FakeClient()
        dispatch("list_analysis_reports", {
            "term_id": 1, "class_id": 2, "exam_id": 5, "limit": 20,
        }, c)
        assert c.calls[0][1] == "/api/v1/plugin/analysis-reports"
        assert c.calls[0][2] == {
            "term_id": 1, "class_id": 2, "exam_id": 5, "limit": 20,
        }

    def test_latest_report_passes_exact_scope(self):
        c = FakeClient()
        res = dispatch("get_latest_analysis_report", {
            "exam_id": 5, "term_id": 1, "class_id": 2, "identify": False,
        }, c)
        assert res["isError"] is False
        assert c.calls[0][1] == "/api/v1/plugin/analysis-reports/latest"
        assert c.calls[0][2] == {
            "exam_id": 5, "term_id": 1, "class_id": 2, "identify": False,
        }

    def test_student_profile_identify_flag(self):
        c = FakeClient()
        dispatch("get_student_learning_profile", {"student_id": 3, "identify": True}, c)
        assert c.calls[0][1] == "/api/v1/plugin/students/3/profile"
        assert c.calls[0][2]["identify"] is True

    def test_student_search_and_practice_context_routes(self):
        c = FakeClient()
        dispatch("search_students", {"term_id": 1, "keyword": "张", "identify": True}, c)
        assert c.calls[0][1] == "/api/v1/plugin/students/search"
        assert c.calls[0][2]["identify"] is True
        dispatch("get_student_practice_context", {"student_id": 3, "term_id": 1, "limit": 10}, c)
        assert c.calls[1][1] == "/api/v1/plugin/students/3/practice-context"
        assert c.calls[1][2] == {"term_id": 1, "limit": 10}

    def test_review_facts_required_exam_id_in_path(self):
        c = FakeClient()
        res = dispatch("get_review_plan_facts", {"exam_id": 5}, c)
        assert c.calls[0][1] == "/api/v1/plugin/review-plans/facts"
        assert "时态" in _text(res)

    def test_materials_list_and_read(self):
        c = FakeClient()
        lst = dispatch("list_formal_materials", {"term_id": 1}, c)
        assert "material_id" in _text(lst)
        c2 = FakeClient()
        rd = dispatch("read_formal_material", {"material_id": 9, "page": 1}, c2)
        assert c2.calls[0][1] == "/api/v1/plugin/materials/9"

    def test_evidence_view(self):
        c = FakeClient()
        res = dispatch("get_evidence", {"evidence_id": "ev-1"}, c)
        assert "ev-1" in _text(res)

    def test_unknown_tool_rejected(self):
        c = FakeClient()
        res = dispatch("hack_db", {}, c)
        assert res["isError"] is True
        assert "unknown_tool" in _text(res)

    def test_error_mapping_unauthenticated(self):
        c = FakeClient(fail_with=PluginClientError("unauthenticated", "令牌无效", 401))
        res = dispatch("get_exam_snapshot", {"exam_id": 5}, c)
        assert res["isError"] is True
        assert "unauthenticated" in _text(res)

    def test_error_mapping_service_unavailable(self):
        c = FakeClient(fail_with=PluginClientError("service_unavailable", "无法连接", None))
        res = dispatch("list_teaching_scopes", {}, c)
        assert res["isError"] is True
        assert "service_unavailable" in _text(res)


class TestErrorLabels:
    def test_known_and_unknown(self):
        assert error.label_for("plugin_disabled") == "插件未启用"
        assert error.label_for(None) == "未知错误"


class TestTokenPersistence:
    def test_save_and_load_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TEACHMATE_PLUGIN_DATA_DIR", str(tmp_path))
        auth.save_token("tm_plugin_xyz")
        assert auth.load_token() == "tm_plugin_xyz"

    def test_load_missing_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TEACHMATE_PLUGIN_DATA_DIR", str(tmp_path))
        assert auth.load_token() is None

    def test_environment_token_takes_precedence(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TEACHMATE_PLUGIN_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("TEACHMATE_PLUGIN_TOKEN", "tm_plugin_from_workbuddy")
        assert auth.load_token() == "tm_plugin_from_workbuddy"
