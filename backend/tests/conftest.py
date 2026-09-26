"""Agent 测试共享夹具

提供 Mock Provider、Mock Tool 和最小 Agent 数据库夹具。
为 R0 门禁建立基线，为 R1-R7 改造提供安全网。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from backend.app.agent.providers.base import (
    ModelCapabilities,
    ModelResponse,
    ModelUsage,
    TextModelProvider,
    ToolCall,
)
from backend.app.agent.config import AgentConfig
from backend.app.agent.context import TeachingContext
from backend.app.agent.evidence import EvidenceLedger
from backend.app.agent.privacy import PrivacyMapper
from backend.app.agent.registry.tools import ToolRegistry, ToolDefinition


# ---------------------------------------------------------------------------
# MockTextProvider
# ---------------------------------------------------------------------------


class MockTextProvider(TextModelProvider):
    """可控响应的文本模型 mock。每次 complete() 消费一个预设响应。"""

    def __init__(self) -> None:
        self._responses: list[ModelResponse] = []
        self._call_log: list[dict[str, Any]] = []

    def set_response(
        self,
        content: str = "",
        tool_calls: list[ToolCall] | None = None,
        finish_reason: str = "stop",
        reasoning_content: str | None = None,
        input_tokens: int = 100,
        output_tokens: int = 200,
    ) -> None:
        self._responses.append(ModelResponse(
            content=content,
            reasoning_content=reasoning_content,
            tool_calls=tool_calls or [],
            finish_reason=finish_reason,
            usage=ModelUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                provider_request_id=f"mock-{len(self._responses)}",
            ),
        ))

    async def complete(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        response_format: dict | None = None,
        tools: list[dict] | None = None,
    ) -> ModelResponse:
        self._call_log.append({
            "messages_count": len(messages),
            "messages": messages,
            "tools_count": len(tools) if tools else 0,
            "tools_disabled": tools is None,
            "temperature": temperature,
        })
        if self._responses:
            return self._responses.pop(0)
        return ModelResponse(
            content="",
            finish_reason="stop",
            usage=ModelUsage(input_tokens=10, output_tokens=5),
        )

    async def tool_loop(
        self,
        messages: list[dict[str, str]],
        tools: list[dict],
        max_iterations: int = 4,
    ) -> ModelResponse:
        if self._responses:
            return self._responses.pop(0)
        return ModelResponse(content="", finish_reason="stop")

    async def estimate_cost(self, model: str, ei: int, eo: int) -> float:
        return 0.001

    def get_capabilities(self, model: str) -> ModelCapabilities:
        return ModelCapabilities(
            supports_json=True,
            supports_tool_calls=True,
            supports_vision=False,
            context_length=128_000,
            max_output_tokens=4_096,
        )

    async def list_models(self) -> list[str]:
        return ["mock-model"]

    @property
    def call_count(self) -> int:
        return len(self._call_log)

    @property
    def call_log(self) -> list[dict[str, Any]]:
        return list(self._call_log)


# ---------------------------------------------------------------------------
# Mock tool helpers
# ---------------------------------------------------------------------------


def make_mock_tool(
    name: str,
    data: dict[str, Any] | None = None,
    description: str = "",
    category: str = "mock",
    requires_scope: list[str] | None = None,
) -> ToolDefinition:
    """创建返回固定数据的 mock 工具。返回格式：{"data": data}"""
    fixed_data = data or {}

    def handler(**kwargs: Any) -> dict[str, Any]:
        return {"data": fixed_data}

    return ToolDefinition(
        name=name,
        description=description or f"Mock tool: {name}",
        parameters_schema={"type": "object", "properties": {}, "required": []},
        handler=handler,
        category=category,
        requires_scope=requires_scope or [],
    )


def make_mock_registry(scope: dict[str, Any] | None = None) -> ToolRegistry:
    """创建包含全部 13 个 mock 工具的注册表。不访问真实数据库。"""
    registry = ToolRegistry()
    s = scope or {"exam_id": 1, "class_id": 1, "student_id": 1}
    eid = s.get("exam_id", 1)

    # exam tools (4)
    registry.register(make_mock_tool("get_exam_statistics", data={
        "exam_id": eid, "exam_name": "Mock考试", "full_score": 100.0,
        "participant_count": 30, "average_score": 75.5, "max_score": 98.0,
        "min_score": 42.0, "std_dev": 12.3, "pass_rate": 0.8, "excellent_rate": 0.2,
    }, requires_scope=["exam_id"]))
    registry.register(make_mock_tool("get_question_list", data={
        "exam_id": eid, "questions": [
            {"question_no": "1", "question_type": "choice", "max_score": 2},
            {"question_no": "2", "question_type": "reading", "max_score": 10},
        ],
    }, requires_scope=["exam_id"]))
    registry.register(make_mock_tool("get_score_distribution", data={
        "exam_id": eid, "bin_size": 10,
        "distribution": [{"range": "70-80", "count": 8}],
    }, requires_scope=["exam_id"]))
    registry.register(make_mock_tool("get_knowledge_coverage", data={
        "exam_id": eid, "knowledge_points": [
            {"name": "时态", "code": "grammar-tense", "question_count": 5, "total_score": 10},
        ],
    }, requires_scope=["exam_id"]))

    # student tools (3)
    registry.register(make_mock_tool("get_student_list", data={
        "exam_id": eid, "students": [
            {"student_id": 1, "score": 85.0, "name": "张三"},
            {"student_id": 2, "score": 72.0, "name": "李四"},
        ],
    }, requires_scope=["exam_id"]))
    registry.register(make_mock_tool("get_student_scores", data={
        "exam_id": eid, "student_id": s.get("student_id", 1), "student_name": "张三",
        "exam_name": "Mock考试", "total_score": 85.0, "class_rank": 3,
        "attendance": "present",
        "item_scores": [{"question_no": "1", "score": 2, "max_score": 2}],
    }, requires_scope=["exam_id"]))
    registry.register(make_mock_tool("get_class_ranking", data={
        "exam_id": eid, "class_id": s.get("class_id", 1),
        "ranking": [
            {"rank": 1, "student_id": 1, "score": 98.0, "name": "张三"},
            {"rank": 2, "student_id": 2, "score": 95.0, "name": "李四"},
        ],
    }, requires_scope=["exam_id", "class_id"]))

    # risk tools (2)
    registry.register(make_mock_tool("get_at_risk_students", data={
        "exam_id": eid, "threshold": 60,
        "at_risk_students": [{"student_id": 5, "score": 42.0, "name": "王五", "gap": 18.0}],
    }, requires_scope=["exam_id"]))
    registry.register(make_mock_tool("get_risk_factors", data={
        "exam_id": eid, "student_id": s.get("student_id", 1), "score": 42.0,
        "risk_level": "high",
        "frequent_error_causes": [{"cause": "词汇", "count": 3}],
    }, requires_scope=["exam_id"]))

    # error tools (2)
    registry.register(make_mock_tool("get_error_causes", data={
        "exam_id": eid,
        "cause_distribution": [{"cause": "审题", "count": 5, "percentage": 0.35}],
        "possible_causes": ["审题", "词汇", "语法", "定位", "推断", "表达"],
    }, requires_scope=["exam_id"]))
    registry.register(make_mock_tool("get_common_mistakes", data={
        "exam_id": eid,
        "common_mistakes": [{"question_no": "3", "error_count": 8, "top_cause": "审题"}],
    }, requires_scope=["exam_id"]))

    # attachment tools (2)
    registry.register(make_mock_tool("get_exam_paper_image", data={
        "exam_id": eid, "total_pages": 2, "has_paper_version": True,
        "images": [{"page": 1, "url": f"/api/v1/files/attachments/1"}],
    }, requires_scope=["exam_id"]))
    registry.register(make_mock_tool("get_student_answer_image", data={
        "exam_id": eid, "student_id": s.get("student_id", 1),
        "images": [{"question_no": "1", "ocr_status": "completed"}],
    }, requires_scope=["exam_id", "student_id"]))

    return registry


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_provider() -> MockTextProvider:
    return MockTextProvider()


@pytest.fixture
def mock_registry() -> ToolRegistry:
    return make_mock_registry()


@pytest.fixture
def agent_config() -> AgentConfig:
    return AgentConfig()


@pytest.fixture
def teaching_context() -> TeachingContext:
    return TeachingContext(
        scope={"exam_id": 1, "class_id": 1, "student_id": 1},
        capability="exam_analysis",
        available_tools=["get_exam_statistics", "get_question_list"],
        privacy_mapper=PrivacyMapper(),
        evidence_ledger=EvidenceLedger(),
        budget_limit_yuan=0.5,
    )


@pytest.fixture
def exam_scope() -> dict[str, Any]:
    """仅考试作用域。"""
    return {"exam_id": 1}


@pytest.fixture
def exam_class_scope() -> dict[str, Any]:
    """考试 + 班级作用域。"""
    return {"exam_id": 1, "class_id": 1}


@pytest.fixture
def exam_student_scope() -> dict[str, Any]:
    """考试 + 学生作用域。"""
    return {"exam_id": 1, "student_id": 1}
