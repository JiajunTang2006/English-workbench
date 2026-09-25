"""能力注册表

管理 Agent 支持的分析能力。每个能力对应一类教学分析场景，
声明所需的工具集、提示词模板、输出 Schema 和预算限制。

能力（Capability）vs 工具（Tool）的区别：
- 工具是原子化的数据查询操作（只读）
- 能力是面向教师需求的分析流程，组合多个工具 + 模型推理
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .tools import ToolRegistry

logger = logging.getLogger(__name__)

# Managed Harness（RAG v3）使用的统一工具契约。旧版 AgentLoop 仍保留
# required_tools，以便已存在的调用方平滑迁移；新流程只从这里派生策略。
MANAGED_REQUIRED_TOOLS = ("submit_report",)
MANAGED_OPTIONAL_TOOLS = ("get_formal_attachment", "get_teaching_guidance")
MANAGED_OPTIONAL_TOOLS_BY_CAPABILITY = {
    "exam_analysis": ("get_formal_attachment", "get_teaching_guidance"),
    "student_diagnosis": ("get_teaching_guidance",),
    "review_plan": ("get_teaching_guidance",),
}

# 普通对话的“自主查数”白名单：仅包含聚合/题目层面的只读查询，不包含
# 学生名单、个人画像、写入工具或批量执行工具。模型可以选择调用，也可以
# 直接回答；当前会话没有 exam_id 时 ToolRegistry 会自动过滤这些工具。
GENERAL_CHAT_READ_TOOLS = (
    "get_exam_statistics",
    "get_question_list",
    "get_question_difficulty",
    "get_score_distribution",
    "get_knowledge_coverage",
    "get_error_causes",
    "get_common_mistakes",
)

# Harness Education Bridge 使用的等价只读工具名。
GENERAL_CHAT_MANAGED_READ_TOOLS = (
    "get_exam_overview",
    "get_score_distribution",
    "get_question_list",
    "get_wrong_questions",
    "get_student_scores",
)


@dataclass
class CapabilityDefinition:
    """能力定义。

    :param name: 能力唯一名称（如 ``exam_analysis``）
    :param display_name: 面向教师的中文名称
    :param description: 能力描述
    :param required_tools: 该能力需要的工具名称列表
    :param prompt_template: 提示词模板文件路径
    :param output_schema: 输出 JSON Schema（用于验证模型输出）
    :param max_iterations: Agent 循环最大迭代次数
    :param max_parallel_tools: 单次迭代最大并行工具数
    :param budget_limit_yuan: 该能力的单次执行预算上限（元）
    :param requires_vision: 是否需要视觉模型
    :param scope_requirements: 必须存在的作用域参数
    :param optional_scope: 可选的增强作用域参数；缺失时能力仍可运行
    :param requires_evidence: 是否必须先调用工具并登记证据
    """

    name: str
    display_name: str
    description: str
    required_tools: list[str] = field(default_factory=list)
    # Managed Harness 的工具契约，与旧 AgentLoop 的 required_tools 分开，
    # 避免旧版数据查询白名单继续渗入分析包路径。
    managed_required_tools: list[str] = field(default_factory=lambda: list(MANAGED_REQUIRED_TOOLS))
    managed_optional_tools: list[str] = field(default_factory=lambda: list(MANAGED_OPTIONAL_TOOLS))
    prompt_template: str = ""
    output_schema: dict[str, Any] = field(default_factory=dict)
    max_iterations: int = 4
    max_parallel_tools: int = 4
    budget_limit_yuan: float = 0.5
    requires_vision: bool = False
    scope_requirements: list[str] = field(default_factory=list)
    optional_scope: list[str] = field(default_factory=list)
    requires_evidence: bool = True


_GENERAL_CHAT_CAPABILITY = CapabilityDefinition(
    name="general_chat",
    display_name="普通对话",
    description="回答教师的一般问题和教学交流；当问题明确涉及当前会话的真实教学数据时，可自主调用安全的只读查询工具辅助判断。",
    required_tools=[],
    managed_required_tools=[],
    managed_optional_tools=[],
    # 允许一轮“模型决定查数”+一轮“依据结果回答”，但仍限制总轮数。
    max_iterations=3,
    max_parallel_tools=1,
    budget_limit_yuan=0.1,
    requires_evidence=False,
)


class CapabilityRegistry:
    """能力注册表，管理所有可用的分析能力。

    使用方式::

        registry = CapabilityRegistry()
        registry.register(CapabilityDefinition(...))
        cap = registry.get("exam_analysis")
        all_caps = registry.list_for_display()
    """

    def __init__(self) -> None:
        self._capabilities: dict[str, CapabilityDefinition] = {}

    def register(self, cap: CapabilityDefinition) -> None:
        """注册一个能力。"""
        if cap.name in self._capabilities:
            logger.warning("能力 '%s' 已注册，将被覆盖", cap.name)
        self._capabilities[cap.name] = cap

    def get(self, name: str) -> CapabilityDefinition | None:
        """按名称获取能力定义。"""
        if name == _GENERAL_CHAT_CAPABILITY.name:
            return _GENERAL_CHAT_CAPABILITY
        return self._capabilities.get(name)

    def list_all(self) -> list[CapabilityDefinition]:
        """列出所有已注册能力。"""
        return list(self._capabilities.values())

    def list_for_display(self) -> list[dict[str, str]]:
        """生成面向教师的能力列表。"""
        return [
            {
                "name": cap.name,
                "display_name": cap.display_name,
                "description": cap.description,
            }
            for cap in self._capabilities.values()
        ]

    def validate_tools(self, tool_registry: ToolRegistry) -> list[str]:
        """验证所有能力声明的工具是否已注册。

        :return: 缺失工具的错误消息列表
        """
        # 前向引用，避免循环导入
        from .tools import ToolRegistry  # noqa: F811

        errors: list[str] = []
        for cap in self._capabilities.values():
            for tool_name in cap.required_tools:
                if tool_registry.get(tool_name) is None:
                    errors.append(
                        f"能力 '{cap.name}' 需要工具 '{tool_name}'，但该工具未注册"
                    )
        return errors


def create_default_capability_registry() -> CapabilityRegistry:
    """创建默认能力注册表，注册所有内置分析能力。

    能力列表（4 个首批能力）：
      1. exam_analysis  — 考试整体分析
      2. student_diagnosis — 学生诊断
      3. review_plan    — 复习计划生成
      4. exam_ingestion — 试卷录入（需视觉模型）

    P0-3: 所有 output_schema 使用 schema_contract 统一生成，
    与 Pydantic StructuredAnswer 模型和前端渲染逻辑对齐。
    """
    from ..schema_contract import (
        EXAM_ANALYSIS_OUTPUT_SCHEMA,
        STUDENT_DIAGNOSIS_OUTPUT_SCHEMA,
        REVIEW_PLAN_OUTPUT_SCHEMA,
        EXAM_INGESTION_OUTPUT_SCHEMA,
    )

    registry = CapabilityRegistry()

    # --- 1. 考试整体分析 ---
    registry.register(CapabilityDefinition(
        name="exam_analysis",
        display_name="考试整体分析",
        description="分析指定考试的整体表现；可选择绑定数据库考试，也可仅基于教师提供的 PDF、Word、Excel 或文本资料进行分析。",
        required_tools=[
            "get_exam_statistics",
            "get_question_list",
            "get_question_difficulty",
            "get_score_distribution",
            "get_knowledge_coverage",
            "get_error_causes",
            "get_common_mistakes",
            "submit_teaching_report",
        ],
        prompt_template="exam_report.md",
        output_schema=EXAM_ANALYSIS_OUTPUT_SCHEMA,
        # 7 read-only exam tools may be selected one at a time by the model,
        # followed by submit_teaching_report and a final answer turn.
        max_iterations=8,
        max_parallel_tools=4,
        budget_limit_yuan=0.5,
        requires_vision=False,
        scope_requirements=[],
        optional_scope=["exam_id"],
        managed_required_tools=["submit_report"],
        managed_optional_tools=list(MANAGED_OPTIONAL_TOOLS_BY_CAPABILITY["exam_analysis"]),
    ))

    # --- 2. 学生诊断 ---
    registry.register(CapabilityDefinition(
        name="student_diagnosis",
        display_name="学生诊断",
        description="支持按班级批量或按姓名、学号诊断学生；全班方案经教师确认后执行，识别薄弱知识点和错误原因，给出针对性建议，并将有证据支持的画像变更自动写入学生管理。",
        required_tools=[
            "get_student_scores",
            "get_student_list",
            "get_error_causes",
            "get_knowledge_coverage",
            "get_student_profile",
            "propose_student_profile_update",
            "submit_teaching_report",
        ],
        prompt_template="student_diagnosis.md",
        output_schema=STUDENT_DIAGNOSIS_OUTPUT_SCHEMA,
        # 4 tool phases (成绩、错误/覆盖、画像读取/建议、报告提交) + 最终回答。
        max_iterations=6,
        max_parallel_tools=3,
        budget_limit_yuan=0.3,
        requires_vision=False,
        scope_requirements=["exam_id", "student_id"],
        managed_required_tools=["submit_report"],
        managed_optional_tools=list(MANAGED_OPTIONAL_TOOLS_BY_CAPABILITY["student_diagnosis"]),
    ))

    # --- 3. 复习计划生成 ---
    registry.register(CapabilityDefinition(
        name="review_plan",
        display_name="复习计划生成",
        description="根据考试分析结果，生成针对性的复习计划，包括知识点优先级和练习建议。",
        required_tools=[
            "get_exam_statistics",
            "get_knowledge_coverage",
            "get_error_causes",
            "get_at_risk_students",
            "submit_teaching_report",
        ],
        prompt_template="review_plan.md",
        output_schema=REVIEW_PLAN_OUTPUT_SCHEMA,
        # 4 read-only planning tools may be selected sequentially, followed
        # by report submission and the final answer turn.
        max_iterations=6,
        max_parallel_tools=4,
        budget_limit_yuan=0.4,
        requires_vision=False,
        scope_requirements=["exam_id"],
        managed_required_tools=["submit_report"],
        managed_optional_tools=list(MANAGED_OPTIONAL_TOOLS_BY_CAPABILITY["review_plan"]),
    ))

    # --- 4. 试卷录入（需视觉模型）---
    registry.register(CapabilityDefinition(
        name="exam_ingestion",
        display_name="试卷录入",
        description="通过 OCR 识别试卷图片，自动提取题目、选项和答案，生成结构化试卷数据。",
        required_tools=[
            "get_exam_paper_image",
        ],
        prompt_template="exam_extraction.md",
        output_schema=EXAM_INGESTION_OUTPUT_SCHEMA,
        max_iterations=2,
        max_parallel_tools=2,
        budget_limit_yuan=1.0,
        requires_vision=True,
        managed_required_tools=[],
        managed_optional_tools=[],
        scope_requirements=["exam_id"],
    ))

    logger.info("能力注册表已创建（当前 %d 个能力）", len(registry.list_all()))
    return registry
