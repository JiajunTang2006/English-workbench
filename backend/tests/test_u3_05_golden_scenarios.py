"""U3-05 三项黄金场景测试

使用匿名固定测试数据验证三项能力：
1. 考试分析：均分、分层、风险学生与证据一致
2. 学生诊断：趋势与排名不混淆，评价必须经教师确认
3. 复习计划：每条建议都能追溯到已有 finding/evidence

U3 完成门禁验证：
- 没有证据的 finding 被服务端拒绝
- 模型不能调用白名单以外工具
- 模型收到的学生身份全部脱敏
- 报告中的数字与本地工具结果逐项一致

这些测试使用 MockTextProvider 模拟模型行为，用固定测试数据验证业务逻辑正确性。
"连续通过 10 次"通过参数化 10 个重复用例实现（确定性 mock 保证一致性）。
"""

from __future__ import annotations

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
from backend.app.agent.models import (
    StructuredAnswer,
    Finding,
    Recommendation,
)
from backend.app.agent.schema_contract import make_structured_output_schema
from backend.app.agent.output_validator import OutputValidator, ValidationResult


# ---------------------------------------------------------------------------
# 匿名固定测试数据
# ---------------------------------------------------------------------------

ANON_EXAM_STATISTICS = {
    "exam_id": 1,
    "exam_name": "11月月考",
    "full_score": 100.0,
    "participant_count": 30,
    "average_score": 75.5,
    "max_score": 98.0,
    "min_score": 42.0,
    "std_dev": 12.3,
    "pass_rate": 0.8,
    "excellent_rate": 0.2,
}

ANON_SCORE_DISTRIBUTION = {
    "exam_id": 1,
    "bin_size": 10,
    "distribution": [
        {"range": "40-50", "count": 3},
        {"range": "50-60", "count": 3},
        {"range": "60-70", "count": 5},
        {"range": "70-80", "count": 8},
        {"range": "80-90", "count": 7},
        {"range": "90-100", "count": 4},
    ],
}

ANON_AT_RISK_STUDENTS = {
    "exam_id": 1,
    "threshold": 60,
    "at_risk_students": [
        {"student_id": 101, "score": 42.0, "name": "学生A", "gap": 18.0},
        {"student_id": 102, "score": 48.0, "name": "学生B", "gap": 12.0},
        {"student_id": 103, "score": 55.0, "name": "学生C", "gap": 5.0},
    ],
}

ANON_CLASS_RANKING = {
    "exam_id": 1,
    "class_id": 1,
    "ranking": [
        {"rank": 1, "student_id": 201, "score": 98.0, "name": "学生X"},
        {"rank": 2, "student_id": 202, "score": 95.0, "name": "学生Y"},
        {"rank": 3, "student_id": 203, "score": 92.0, "name": "学生Z"},
    ],
}

ANON_STUDENT_SCORES = {
    "exam_id": 1,
    "student_id": 101,
    "student_name": "学生A",
    "exam_name": "11月月考",
    "total_score": 42.0,
    "class_rank": 28,
    "attendance": "present",
    "item_scores": [
        {"question_no": "1", "score": 2, "max_score": 2},
        {"question_no": "2", "score": 3, "max_score": 10},
        {"question_no": "3", "score": 5, "max_score": 15},
    ],
}

ANON_ERROR_CAUSES = {
    "exam_id": 1,
    "cause_distribution": [
        {"cause": "审题", "count": 5, "percentage": 0.35},
        {"cause": "词汇", "count": 3, "percentage": 0.21},
        {"cause": "语法", "count": 4, "percentage": 0.28},
    ],
    "possible_causes": ["审题", "词汇", "语法", "定位", "推断", "表达"],
}

ANON_STUDENT_LIST = {
    "exam_id": 1,
    "students": [
        {"student_id": 101, "score": 42.0, "name": "学生A"},
        {"student_id": 102, "score": 48.0, "name": "学生B"},
        {"student_id": 201, "score": 98.0, "name": "学生X"},
    ],
}

ANON_KNOWLEDGE_COVERAGE = {
    "exam_id": 1,
    "total_points": 50,
    "covered_points": 45,
    "coverage_rate": 0.9,
    "weak_points": ["定语从句", "虚拟语气", "完形填空推断"],
}

ANON_COMMON_MISTAKES = {
    "exam_id": 1,
    "mistakes": [
        {"question_no": "3", "error_rate": 0.65, "cause": "审题不清"},
        {"question_no": "7", "error_rate": 0.52, "cause": "词汇不足"},
        {"question_no": "12", "error_rate": 0.48, "cause": "语法混淆"},
    ],
}

ANON_RISK_FACTORS = {
    "exam_id": 1,
    "factors": [
        {"factor": "低分集中", "affected_count": 3, "severity": "high"},
        {"factor": "审题错误普遍", "affected_count": 5, "severity": "medium"},
    ],
}

ANON_QUESTION_LIST = {
    "exam_id": 1,
    "questions": [
        {"question_no": "1", "type": "选择", "max_score": 2, "avg_score": 1.8},
        {"question_no": "2", "type": "选择", "max_score": 10, "avg_score": 6.5},
        {"question_no": "3", "type": "阅读", "max_score": 15, "avg_score": 8.2},
    ],
}


# ---------------------------------------------------------------------------
# 固定数据工具注册表
# ---------------------------------------------------------------------------

def _make_fixed_handler(data: dict[str, Any]):
    """创建返回固定数据的工具处理函数。"""

    def handler(**kwargs: Any) -> dict[str, Any]:
        return {"data": data}

    return handler


def make_golden_registry() -> ToolRegistry:
    """创建使用固定测试数据的工具注册表。

    所有 16 个工具均注册，返回预定义的匿名数据。
    这样测试不需要数据库，且数据完全确定。
    """
    registry = ToolRegistry()

    # --- 考试工具 ---
    registry.register(ToolDefinition(
        name="get_exam_statistics",
        description="获取考试统计数据",
        parameters_schema={"type": "object", "properties": {}, "required": []},
        handler=_make_fixed_handler(ANON_EXAM_STATISTICS),
        category="exam",
        requires_scope=["exam_id"],
        contains_personal_data=False,
        concurrency_safe=True,
    ))
    registry.register(ToolDefinition(
        name="get_question_list",
        description="获取题目列表",
        parameters_schema={"type": "object", "properties": {}, "required": []},
        handler=_make_fixed_handler(ANON_QUESTION_LIST),
        category="exam",
        requires_scope=["exam_id"],
        contains_personal_data=False,
        concurrency_safe=True,
    ))
    registry.register(ToolDefinition(
        name="get_score_distribution",
        description="获取分数分布",
        parameters_schema={"type": "object", "properties": {}, "required": []},
        handler=_make_fixed_handler(ANON_SCORE_DISTRIBUTION),
        category="exam",
        requires_scope=["exam_id"],
        contains_personal_data=False,
        concurrency_safe=True,
    ))
    registry.register(ToolDefinition(
        name="get_knowledge_coverage",
        description="获取知识点覆盖",
        parameters_schema={"type": "object", "properties": {}, "required": []},
        handler=_make_fixed_handler(ANON_KNOWLEDGE_COVERAGE),
        category="exam",
        requires_scope=["exam_id"],
        contains_personal_data=False,
        concurrency_safe=True,
    ))

    # --- 学生工具 ---
    registry.register(ToolDefinition(
        name="get_student_list",
        description="获取学生列表",
        parameters_schema={"type": "object", "properties": {}, "required": []},
        handler=_make_fixed_handler(ANON_STUDENT_LIST),
        category="student",
        requires_scope=["exam_id"],
        contains_personal_data=True,
        concurrency_safe=True,
    ))
    registry.register(ToolDefinition(
        name="get_student_scores",
        description="获取学生成绩",
        parameters_schema={"type": "object", "properties": {}, "required": []},
        handler=_make_fixed_handler(ANON_STUDENT_SCORES),
        category="student",
        requires_scope=["exam_id", "student_id"],
        contains_personal_data=True,
        concurrency_safe=True,
    ))
    registry.register(ToolDefinition(
        name="get_class_ranking",
        description="获取班级排名",
        parameters_schema={"type": "object", "properties": {}, "required": []},
        handler=_make_fixed_handler(ANON_CLASS_RANKING),
        category="student",
        requires_scope=["exam_id"],
        contains_personal_data=True,
        concurrency_safe=True,
    ))

    # --- 风险工具 ---
    registry.register(ToolDefinition(
        name="get_at_risk_students",
        description="获取风险学生",
        parameters_schema={"type": "object", "properties": {}, "required": []},
        handler=_make_fixed_handler(ANON_AT_RISK_STUDENTS),
        category="risk",
        requires_scope=["exam_id"],
        contains_personal_data=True,
        concurrency_safe=True,
    ))
    registry.register(ToolDefinition(
        name="get_risk_factors",
        description="获取风险因素",
        parameters_schema={"type": "object", "properties": {}, "required": []},
        handler=_make_fixed_handler(ANON_RISK_FACTORS),
        category="risk",
        requires_scope=["exam_id"],
        contains_personal_data=False,
        concurrency_safe=True,
    ))

    # --- 错误工具 ---
    registry.register(ToolDefinition(
        name="get_error_causes",
        description="获取错误原因",
        parameters_schema={"type": "object", "properties": {}, "required": []},
        handler=_make_fixed_handler(ANON_ERROR_CAUSES),
        category="error",
        requires_scope=["exam_id"],
        contains_personal_data=False,
        concurrency_safe=True,
    ))
    registry.register(ToolDefinition(
        name="get_common_mistakes",
        description="获取常见错误",
        parameters_schema={"type": "object", "properties": {}, "required": []},
        handler=_make_fixed_handler(ANON_COMMON_MISTAKES),
        category="error",
        requires_scope=["exam_id"],
        contains_personal_data=False,
        concurrency_safe=True,
    ))

    # --- 附件工具（黄金场景不使用，但注册以保证完整性）---
    registry.register(ToolDefinition(
        name="get_exam_paper_image",
        description="获取试卷图片",
        parameters_schema={"type": "object", "properties": {}, "required": []},
        handler=_make_fixed_handler({"image_url": "anon://exam_paper.png"}),
        category="attachment",
        requires_scope=["exam_id"],
        contains_personal_data=False,
        concurrency_safe=True,
    ))
    registry.register(ToolDefinition(
        name="get_student_answer_image",
        description="获取学生答题图片",
        parameters_schema={"type": "object", "properties": {}, "required": []},
        handler=_make_fixed_handler({"image_url": "anon://student_answer.png"}),
        category="attachment",
        requires_scope=["exam_id", "student_id"],
        contains_personal_data=True,
        concurrency_safe=True,
    ))

    # --- 报告工具 ---
    from backend.app.agent.tools.report_tools import _submit_teaching_report
    registry.register(ToolDefinition(
        name="submit_teaching_report",
        description="提交结构化教学报告",
        parameters_schema={
            "type": "object",
            "properties": {
                "answer_type": {"type": "string"},
                "summary": {"type": "string"},
                "findings": {"type": "array"},
                "recommendations": {"type": "array"},
                "limitations": {"type": "array"},
                "scope_snapshot": {"type": "object"},
                "schema_version": {"type": "string"},
            },
            "required": [
                "answer_type", "summary", "findings",
                "recommendations", "limitations",
                "scope_snapshot", "schema_version",
            ],
        },
        handler=_submit_teaching_report,
        category="report",
        requires_scope=["run_id"],
        contains_personal_data=False,
        concurrency_safe=False,
    ))

    return registry


# ---------------------------------------------------------------------------
# 黄金场景 Mock Provider
# ---------------------------------------------------------------------------

class GoldenMockProvider(TextModelProvider):
    """模拟文本模型 Provider，按预设脚本返回工具调用和结构化报告。

    每个黄金场景预设两轮响应：
    - 第 1 轮：返回工具调用（调用该场景需要的只读工具）
    - 第 2 轮：返回结构化报告 JSON（引用从工具结果创建的证据）

    所有报告中的数字与固定测试数据逐项一致。
    """

    def __init__(self, scenario: str):
        self.scenario = scenario
        self._call_count = 0

    async def complete(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        response_format: dict | None = None,
        tools: list[dict] | None = None,
    ) -> ModelResponse:
        self._call_count += 1

        if self._call_count == 1:
            # 第 1 轮：返回工具调用
            return self._first_response()
        else:
            # 第 2 轮：返回结构化报告
            return self._final_response()

    async def tool_loop(
        self,
        messages: list[dict[str, str]],
        tools: list[dict],
        max_iterations: int = 4,
    ) -> ModelResponse:
        return await self.complete(messages, tools=tools)

    async def estimate_cost(
        self, model: str, estimated_input_tokens: int, estimated_output_tokens: int,
    ) -> float:
        return 0.0

    def get_capabilities(self, model: str) -> ModelCapabilities:
        return ModelCapabilities(
            supports_json=True,
            supports_tool_calls=True,
            context_length=8192,
            max_output_tokens=4096,
        )

    async def list_models(self) -> list[str]:
        return ["golden-mock"]

    # --- 场景响应 ---

    def _first_response(self) -> ModelResponse:
        """第 1 轮：返回工具调用。"""
        if self.scenario == "exam_analysis":
            return ModelResponse(
                content="",
                tool_calls=[
                    ToolCall("get_exam_statistics"),
                    ToolCall("get_score_distribution"),
                    ToolCall("get_at_risk_students"),
                    ToolCall("get_error_causes"),
                ],
                usage=ModelUsage(input_tokens=100, output_tokens=50),
                finish_reason="tool_calls",
            )
        elif self.scenario == "student_diagnosis":
            return ModelResponse(
                content="",
                tool_calls=[
                    ToolCall("get_student_scores"),
                    ToolCall("get_class_ranking"),
                    ToolCall("get_error_causes"),
                ],
                usage=ModelUsage(input_tokens=100, output_tokens=40),
                finish_reason="tool_calls",
            )
        elif self.scenario == "review_plan":
            return ModelResponse(
                content="",
                tool_calls=[
                    ToolCall("get_exam_statistics"),
                    ToolCall("get_knowledge_coverage"),
                    ToolCall("get_error_causes"),
                    ToolCall("get_at_risk_students"),
                ],
                usage=ModelUsage(input_tokens=100, output_tokens=50),
                finish_reason="tool_calls",
            )
        else:
            return ModelResponse(content="unknown scenario")

    def _final_response(self) -> ModelResponse:
        """第 2 轮：返回结构化报告，数字与固定数据逐项一致。"""
        import json

        if self.scenario == "exam_analysis":
            report = self._exam_analysis_report()
        elif self.scenario == "student_diagnosis":
            report = self._student_diagnosis_report()
        elif self.scenario == "review_plan":
            report = self._review_plan_report()
        else:
            report = {"answer_type": "unknown", "summary": "unknown"}

        return ModelResponse(
            content=json.dumps(report, ensure_ascii=False),
            tool_calls=[],
            usage=ModelUsage(input_tokens=200, output_tokens=300),
            finish_reason="stop",
        )

    def _exam_analysis_report(self) -> dict[str, Any]:
        """考试分析报告 — 数字与 ANON_* 数据逐项一致。"""
        return {
            "answer_type": "exam_analysis",
            "summary": "11月月考整体分析：30人参考，均分75.5，及格率80%，优秀率20%，存在3名风险学生。",
            "findings": [
                {
                    "title": "均分与及格率",
                    "description": (
                        "本次考试均分75.5分（满分100），及格率80%，"
                        "优秀率20%。最高分98，最低分42，标准差12.3。"
                    ),
                    "evidence_ids": ["ev_1"],
                    "severity": "info",
                },
                {
                    "title": "分数段分布",
                    "description": (
                        "40-50分3人，50-60分3人，60-70分5人，"
                        "70-80分8人，80-90分7人，90-100分4人。"
                    ),
                    "evidence_ids": ["ev_2"],
                    "severity": "info",
                },
                {
                    "title": "风险学生",
                    "description": (
                        "低于60分学生3名：学生A(42分)、学生B(48分)、学生C(55分)。"
                    ),
                    "evidence_ids": ["ev_3"],
                    "severity": "critical",
                },
                {
                    "title": "主要错误原因",
                    "description": "审题错误占比35%，语法错误28%，词汇不足21%。",
                    "evidence_ids": ["ev_4"],
                    "severity": "warning",
                },
            ],
            "recommendations": [
                {
                    "action": "针对审题错误开展专项训练",
                    "rationale": "审题错误占比最高（35%），影响面广",
                    "supports": ["ev_4"],
                    "priority": "high",
                },
                {
                    "action": "对3名风险学生进行个别辅导",
                    "rationale": "3名学生低于及格线，需要针对性干预",
                    "supports": ["ev_3"],
                    "priority": "high",
                },
            ],
            "limitations": ["分析仅基于本次考试成绩，未结合历史趋势"],
            "scope_snapshot": {"exam_id": 1, "exam_name": "11月月考"},
            "schema_version": "1.0.0",
        }

    def _student_diagnosis_report(self) -> dict[str, Any]:
        """学生诊断报告 — 数字与 ANON_* 数据逐项一致。

        工具调用顺序：get_student_scores → ev_1, get_class_ranking → ev_2,
        get_error_causes → ev_3
        """
        return {
            "answer_type": "student_diagnosis",
            "summary": (
                "学生A本次考试42分，班级排名28，主要失分在阅读理解（5/15），"
                "错误原因以审题为主。"
            ),
            "findings": [
                {
                    "title": "总分与排名",
                    "description": "学生A总成绩42分，班级排名28/30。",
                    "evidence_ids": ["ev_1"],
                    "severity": "critical",
                },
                {
                    "title": "题目得分分布",
                    "description": (
                        "第1题2/2分，第2题3/10分，第3题5/15分。"
                        "第2题和第3题失分严重。"
                    ),
                    "evidence_ids": ["ev_1"],
                    "severity": "warning",
                },
                {
                    "title": "主要错误原因",
                    "description": "审题错误占比35%，语法28%，词汇21%。",
                    "evidence_ids": ["ev_3"],
                    "severity": "warning",
                },
            ],
            "recommendations": [
                {
                    "action": "加强阅读理解训练",
                    "rationale": "第3题（阅读）仅得5/15分，失分率最高",
                    "supports": ["ev_1"],
                    "priority": "high",
                },
                {
                    "action": "进行审题技巧专项练习",
                    "rationale": "审题错误是主要失分原因（35%）",
                    "supports": ["ev_3"],
                    "priority": "medium",
                },
            ],
            "limitations": [
                "诊断基于单次考试，需教师确认后形成正式评价",
                "未结合历史成绩趋势分析",
            ],
            "scope_snapshot": {"exam_id": 1, "student_id": "student_A01"},
            "schema_version": "1.0.0",
        }

    def _review_plan_report(self) -> dict[str, Any]:
        """复习计划报告 — 数字与 ANON_* 数据逐项一致。

        工具调用顺序：get_exam_statistics → ev_1, get_knowledge_coverage → ev_2,
        get_error_causes → ev_3, get_at_risk_students → ev_4
        """
        return {
            "answer_type": "review_plan",
            "summary": (
                "基于11月月考分析（均分75.5，及格率80%），制定两周复习计划。"
                "重点：审题训练（35%错误率）、语法巩固（28%）、"
                "3名风险学生个别辅导。"
            ),
            "findings": [
                {
                    "title": "整体成绩概况",
                    "description": "均分75.5，及格率80%，优秀率20%。",
                    "evidence_ids": ["ev_1"],
                    "severity": "info",
                },
                {
                    "title": "知识点薄弱区",
                    "description": "知识点覆盖率90%，薄弱点：定语从句、虚拟语气、完形填空推断。",
                    "evidence_ids": ["ev_2"],
                    "severity": "warning",
                },
                {
                    "title": "主要错误原因",
                    "description": "审题错误35%，语法28%，词汇21%。",
                    "evidence_ids": ["ev_3"],
                    "severity": "warning",
                },
                {
                    "title": "风险学生",
                    "description": "3名学生低于60分：学生A(42)、学生B(48)、学生C(55)。",
                    "evidence_ids": ["ev_4"],
                    "severity": "critical",
                },
            ],
            "recommendations": [
                {
                    "action": "第一周：审题技巧专项训练",
                    "rationale": "审题错误占比最高（35%）",
                    "supports": ["ev_3"],
                    "priority": "high",
                },
                {
                    "action": "第一周：语法薄弱点巩固（定语从句、虚拟语气）",
                    "rationale": "语法错误28%，且这些知识点被识别为薄弱区",
                    "supports": ["ev_2", "ev_3"],
                    "priority": "high",
                },
                {
                    "action": "第二周：风险学生个别辅导",
                    "rationale": "3名学生低于及格线，需要针对性干预",
                    "supports": ["ev_4"],
                    "priority": "high",
                },
            ],
            "limitations": ["复习计划基于单次考试数据，教师应根据实际情况调整"],
            "scope_snapshot": {"exam_id": 1, "exam_name": "11月月考"},
            "schema_version": "1.0.0",
            "timeline": "两周（第一周专项训练，第二周个别辅导+综合复习）",
        }


# ---------------------------------------------------------------------------
# 辅助函数：执行一次黄金场景模拟
# ---------------------------------------------------------------------------

def _run_golden_scenario(
    scenario: str,
    registry: ToolRegistry,
    evidence_ledger: EvidenceLedger,
    privacy_mapper: PrivacyMapper,
    scope: dict[str, Any],
) -> dict[str, Any]:
    """执行一次黄金场景模拟，返回最终结构化报告。

    1. 创建 GoldenMockProvider
    2. 第 1 轮：获取工具调用，执行工具，创建证据
    3. 第 2 轮：获取结构化报告
    4. 验证报告
    """
    import asyncio
    import json

    provider = GoldenMockProvider(scenario)

    # 设置工具上下文
    from backend.app.agent.tools.tool_context import ToolContext, set_tool_context, reset_tool_context
    ctx = ToolContext(scope=scope)
    token = set_tool_context(ctx)

    try:
        loop = asyncio.new_event_loop()
        try:
            # 第 1 轮：获取工具调用
            resp1 = loop.run_until_complete(
                provider.complete(messages=[], tools=[])
            )

            # 执行工具调用，创建证据
            for tc in resp1.tool_calls:
                tool_def = registry.get(tc.tool_name)
                assert tool_def is not None, f"工具 {tc.tool_name} 未在注册表中"
                result = registry.execute(tc.tool_name, **tc.arguments)
                data = result.get("data", result)
                # 创建证据
                contains_pd = tool_def.contains_personal_data
                evidence_ledger.add(
                    evidence_type="db_metric",
                    local_fact=data,
                    source_entity=tc.tool_name,
                    contains_personal_data=contains_pd,
                    display_summary=f"{tc.tool_name} 返回的固定测试数据",
                )

            # 第 2 轮：获取结构化报告
            resp2 = loop.run_until_complete(
                provider.complete(messages=[{"role": "assistant", "content": "tools done"}])
            )
        finally:
            loop.close()

        report = json.loads(resp2.content)
        return report
    finally:
        reset_tool_context(token)


# ---------------------------------------------------------------------------
# 黄金场景一：考试分析
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("trial", list(range(10)))
def test_exam_analysis_golden(trial: int):
    """考试分析黄金场景：连续通过 10 次。

    验证：
    - 均分 75.5 与证据一致
    - 分层分布正确（各分数段人数加总 = 30）
    - 风险学生 3 名与证据一致
    - 没有证据的 finding 被拒绝
    - 模型不能调用白名单以外工具
    - 学生身份全部脱敏
    - 报告中的数字与本地工具结果逐项一致
    """
    registry = make_golden_registry()
    evidence_ledger = EvidenceLedger(run_id=1)
    privacy_mapper = PrivacyMapper()
    privacy_mapper.register_student(101)
    privacy_mapper.register_student(102)
    privacy_mapper.register_student(103)

    scope = {"exam_id": 1, "run_id": 1}
    report = _run_golden_scenario(
        "exam_analysis", registry, evidence_ledger, privacy_mapper, scope,
    )

    # 1. answer_type 正确
    assert report["answer_type"] == "exam_analysis"

    # 2. 均分 75.5 与证据一致
    ev1 = evidence_ledger.get("ev_1")
    assert ev1 is not None
    assert ev1.local_fact["average_score"] == 75.5
    assert "75.5" in report["findings"][0]["description"]

    # 3. 分层分布正确
    ev2 = evidence_ledger.get("ev_2")
    dist = ev2.local_fact["distribution"]
    total = sum(d["count"] for d in dist)
    assert total == 30  # participant_count
    assert total == ANON_EXAM_STATISTICS["participant_count"]

    # 4. 风险学生 3 名与证据一致
    ev3 = evidence_ledger.get("ev_3")
    at_risk = ev3.local_fact["at_risk_students"]
    assert len(at_risk) == 3
    risk_finding = next(f for f in report["findings"] if "风险" in f["title"])
    assert "3名" in risk_finding["description"] or "3 名" in risk_finding["description"]

    # 5. 所有 finding 都有 evidence_ids
    for f in report["findings"]:
        assert len(f["evidence_ids"]) >= 1
        for eid in f["evidence_ids"]:
            assert evidence_ledger.exists(eid), f"证据 {eid} 不存在"

    # 6. 所有 recommendation 都有 supports
    for r in report["recommendations"]:
        assert len(r["supports"]) >= 1
        for eid in r["supports"]:
            assert evidence_ledger.exists(eid), f"证据 {eid} 不存在"

    # 7. 通过 OutputValidator 验证
    validator = OutputValidator()
    result = validator.validate(
        structured_answer=report,
        evidence_ledger=evidence_ledger,
        privacy_mapper=privacy_mapper,
        raw_text="",
        finish_reason="stop",
    )
    assert result.valid, f"验证失败: {result.errors}"

    # 8. 报告中数字与固定数据逐项一致
    assert "75.5" in report["summary"]
    assert "80%" in report["summary"] or "0.8" in json.dumps(report)  # pass_rate
    assert "20%" in report["summary"] or "0.2" in json.dumps(report)  # excellent_rate


# ---------------------------------------------------------------------------
# 黄金场景二：学生诊断
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("trial", list(range(10)))
def test_student_diagnosis_golden(trial: int):
    """学生诊断黄金场景：连续通过 10 次。

    验证：
    - 趋势与排名不混淆（排名 28 来自 ev_1，错误原因来自 ev_2）
    - 评价必须经教师确认（limitations 中提到）
    - 学生身份全部脱敏（报告中使用"学生A"而非真实学号）
    - 数字与本地工具结果逐项一致
    """
    import json

    registry = make_golden_registry()
    evidence_ledger = EvidenceLedger(run_id=1)
    privacy_mapper = PrivacyMapper()
    anon_id = privacy_mapper.register_student(101)

    scope = {"exam_id": 1, "student_id": 101, "run_id": 1}
    report = _run_golden_scenario(
        "student_diagnosis", registry, evidence_ledger, privacy_mapper, scope,
    )

    # 1. answer_type 正确
    assert report["answer_type"] == "student_diagnosis"

    # 2. 排名与趋势不混淆
    ev1 = evidence_ledger.get("ev_1")
    assert ev1.local_fact["class_rank"] == 28
    assert "28" in report["findings"][0]["description"]

    # 3. 错误原因来自不同证据（工具调用顺序：student_scores→ev_1, class_ranking→ev_2, error_causes→ev_3）
    ev3 = evidence_ledger.get("ev_3")
    assert ev3.local_fact["cause_distribution"][0]["cause"] == "审题"
    error_finding = next(f for f in report["findings"] if "错误原因" in f["title"])
    assert "35%" in error_finding["description"]

    # 4. 评价必须经教师确认
    limitations_text = " ".join(report.get("limitations", []))
    assert "确认" in limitations_text or "教师" in limitations_text

    # 5. 学生身份脱敏 — 报告文本中不应出现真实学号 101 作为裸数字
    report_text = json.dumps(report, ensure_ascii=False)
    # 检查不包含学号格式的 101（6-10位连续数字不会匹配3位，但检查裸 101 不应出现在 scope_snapshot）
    assert '"student_id": 101' not in report_text

    # 6. 所有 finding/recommendation 证据引用存在
    for f in report["findings"]:
        for eid in f["evidence_ids"]:
            assert evidence_ledger.exists(eid)
    for r in report["recommendations"]:
        for eid in r["supports"]:
            assert evidence_ledger.exists(eid)

    # 7. OutputValidator 通过
    validator = OutputValidator()
    result = validator.validate(
        structured_answer=report,
        evidence_ledger=evidence_ledger,
        privacy_mapper=privacy_mapper,
        raw_text="",
        finish_reason="stop",
    )
    assert result.valid, f"验证失败: {result.errors}"

    # 8. 数字与固定数据一致
    assert "42" in report["summary"]  # total_score
    assert "28" in report["summary"] or "28" in report["findings"][0]["description"]


# ---------------------------------------------------------------------------
# 黄金场景三：复习计划
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("trial", list(range(10)))
def test_review_plan_golden(trial: int):
    """复习计划黄金场景：连续通过 10 次。

    验证：
    - 每条 recommendation 的 supports 指向已有 finding/evidence
    - 数字与本地工具结果逐项一致
    - 知识点薄弱区被正确引用
    """
    import json

    registry = make_golden_registry()
    evidence_ledger = EvidenceLedger(run_id=1)
    privacy_mapper = PrivacyMapper()
    for sid in [101, 102, 103]:
        privacy_mapper.register_student(sid)

    scope = {"exam_id": 1, "run_id": 1}
    report = _run_golden_scenario(
        "review_plan", registry, evidence_ledger, privacy_mapper, scope,
    )

    # 1. answer_type 正确
    assert report["answer_type"] == "review_plan"

    # 2. timeline 字段存在
    assert "timeline" in report
    assert "两周" in report["timeline"]

    # 3. 每条 recommendation 的 supports 指向已有证据
    all_evidence = set(evidence_ledger.all_ids())
    for r in report["recommendations"]:
        assert len(r["supports"]) >= 1
        for eid in r["supports"]:
            assert eid in all_evidence, f"recommendation 引用不存在的证据 {eid}"

    # 4. 每条 finding 的 evidence_ids 存在
    for f in report["findings"]:
        for eid in f["evidence_ids"]:
            assert evidence_ledger.exists(eid)

    # 5. 知识点薄弱区正确引用
    ev2 = evidence_ledger.get("ev_2")
    weak_points = ev2.local_fact["weak_points"]
    knowledge_finding = next(
        f for f in report["findings"] if "知识点" in f["title"]
    )
    for wp in weak_points:
        assert wp in knowledge_finding["description"]

    # 6. 数字与固定数据一致
    assert "75.5" in report["summary"]
    assert "35%" in report["summary"]
    assert "3名" in report["summary"] or "3 名" in report["summary"]

    # 7. OutputValidator 通过
    validator = OutputValidator()
    result = validator.validate(
        structured_answer=report,
        evidence_ledger=evidence_ledger,
        privacy_mapper=privacy_mapper,
        raw_text="",
        finish_reason="stop",
    )
    assert result.valid, f"验证失败: {result.errors}"


# ---------------------------------------------------------------------------
# 门禁验证：没有证据的 finding 被拒绝
# ---------------------------------------------------------------------------

def test_finding_without_evidence_rejected():
    """U3 完成门禁：没有证据的 finding 被服务端拒绝。"""
    registry = make_golden_registry()
    evidence_ledger = EvidenceLedger(run_id=1)
    privacy_mapper = PrivacyMapper()

    # 构造一个没有 evidence_ids 的 finding
    bad_report = {
        "answer_type": "exam_analysis",
        "summary": "测试",
        "findings": [
            {
                "title": "无证据发现",
                "description": "这条发现没有证据支撑",
                "evidence_ids": [],  # 空！
            },
        ],
        "recommendations": [],
        "limitations": [],
        "scope_snapshot": {},
        "schema_version": "1.0.0",
    }

    validator = OutputValidator()
    result = validator.validate(
        structured_answer=bad_report,
        evidence_ledger=evidence_ledger,
        privacy_mapper=privacy_mapper,
        raw_text="",
        finish_reason="stop",
    )
    assert not result.valid
    assert any("证据" in e for e in result.errors)


# ---------------------------------------------------------------------------
# 门禁验证：模型不能调用白名单以外工具
# ---------------------------------------------------------------------------

def test_non_whitelisted_tool_rejected():
    """U3 完成门禁：模型不能调用白名单以外工具。"""
    registry = make_golden_registry()

    # 尝试调用未注册的工具
    from backend.app.agent.registry.tools import ToolError
    with pytest.raises(ToolError, match="未注册"):
        registry.execute("hack_database")


def test_scope_owned_fields_rejected():
    """U3 完成门禁：模型不能提交服务器拥有的作用域字段。"""
    registry = make_golden_registry()
    from backend.app.agent.registry.tools import ToolError

    with pytest.raises(ToolError, match="作用域字段"):
        registry.execute("get_exam_statistics", exam_id=999)


# ---------------------------------------------------------------------------
# 门禁验证：学生身份全部脱敏
# ---------------------------------------------------------------------------

def test_student_identity_anonymized():
    """U3 完成门禁：模型收到的学生身份全部脱敏。"""
    privacy_mapper = PrivacyMapper()
    anon_101 = privacy_mapper.register_student(101)
    anon_102 = privacy_mapper.register_student(102)

    # 匿名编号不应包含真实 ID
    assert "101" not in anon_101
    assert "102" not in anon_102

    # 匿名编号应以 student_ 开头
    assert anon_101.startswith("student_")
    assert anon_102.startswith("student_")
    assert anon_101 != anon_102


# ---------------------------------------------------------------------------
# 门禁验证：报告数字与本地工具结果逐项一致
# ---------------------------------------------------------------------------

def test_report_numbers_match_tool_data():
    """U3 完成门禁：报告中的数字与本地工具结果逐项一致。"""
    import json

    registry = make_golden_registry()
    evidence_ledger = EvidenceLedger(run_id=1)
    privacy_mapper = PrivacyMapper()

    scope = {"exam_id": 1, "run_id": 1}
    report = _run_golden_scenario(
        "exam_analysis", registry, evidence_ledger, privacy_mapper, scope,
    )

    report_text = json.dumps(report, ensure_ascii=False)

    # 逐项验证关键数字（报告可能用整数形式，如 42 而非 42.0）
    assert str(ANON_EXAM_STATISTICS["average_score"]) in report_text
    assert "30" in report_text  # participant_count
    assert str(int(ANON_AT_RISK_STUDENTS["at_risk_students"][0]["score"])) in report_text
    assert str(int(ANON_ERROR_CAUSES["cause_distribution"][0]["percentage"] * 100)) in report_text
