"""工具注册表

管理 Agent 可调用的只读工具。每个工具声明名称、描述、参数 Schema，
在 Agent 循环中供模型选择调用。

设计原则：
- 所有工具均为只读（不修改数据库）
- 工具返回结构化数据，由 Agent 循环组织为证据
- 工具参数使用 JSON Schema 描述，供模型理解
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

import jsonschema

logger = logging.getLogger(__name__)


class ToolError(Exception):
    """工具执行错误。"""

    def __init__(self, tool_name: str, message: str, retryable: bool = False) -> None:
        self.tool_name = tool_name
        self.message = message
        self.retryable = retryable
        super().__init__(f"[{tool_name}] {message}")


class ToolHandler(Protocol):
    """工具处理函数协议。"""

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        """执行工具，返回结构化结果。

        :return: 包含 ``data`` 键的字典，可选 ``error`` 键
        """
        ...


@dataclass
class ToolDefinition:
    """工具定义。

    :param name: 工具唯一名称（如 ``get_exam_statistics``）
    :param description: 工具描述，供模型理解工具用途
    :param parameters_schema: JSON Schema 描述工具参数
    :param handler: 实际执行函数
    :param category: 工具分类（exam / student / risk / error / attachment）
    :param requires_scope: 需要的作用域（如 ``exam_id`` / ``class_id``）
    :param cost_hint: 成本提示（用于预估）
    :param contains_personal_data: 返回值是否包含个人数据（需要脱敏）
    :param concurrency_safe: 是否并发安全
    :param timeout_seconds: 执行超时秒数
    """

    name: str
    description: str
    parameters_schema: dict[str, Any]
    handler: ToolHandler
    category: str
    requires_scope: list[str] = field(default_factory=list)
    cost_hint: float = 0.0  # 预计单次调用成本（元）
    contains_personal_data: bool = True
    concurrency_safe: bool = False
    timeout_seconds: float = 30.0
    output_schema: dict[str, Any] | None = None


# 服务器拥有的作用域字段 — 模型不得在工具参数中提交这些字段
SCOPE_OWNED_FIELDS: frozenset = frozenset({
    "exam_id", "class_id", "student_id", "term_id", "run_id",
})


class ToolRegistry:
    """工具注册表，管理所有可用的 Agent 工具。

    使用方式::

        registry = ToolRegistry()
        registry.register(ToolDefinition(...))
        tool = registry.get("get_exam_statistics")
        tools_for_model = registry.for_model(scope={"exam_id": 123})
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        """注册一个工具。"""
        if tool.name in self._tools:
            logger.warning("工具 '%s' 已注册，将被覆盖", tool.name)
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition | None:
        """按名称获取工具定义。"""
        return self._tools.get(name)

    def list_all(self) -> list[ToolDefinition]:
        """列出所有已注册工具。"""
        return list(self._tools.values())

    def for_model(
        self,
        scope: dict[str, Any] | None = None,
        available_tools: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """生成供模型使用的工具列表（OpenAI function-calling 格式）。

        只返回当前作用域可用的工具，且只返回 capability 白名单中的工具。

        :param scope: 当前作用域（如 ``{"exam_id": 123}``）
        :param available_tools: 当前能力允许的工具名白名单。若为 None 则返回所有满足作用域的工具。
        """
        scope = scope or {}
        available_set = set(available_tools) if available_tools is not None else None
        result: list[dict[str, Any]] = []
        for tool in self._tools.values():
            # 检查作用域要求是否满足
            if not all(k in scope for k in tool.requires_scope):
                continue
            # 检查 capability 白名单
            if available_set is not None and tool.name not in available_set:
                continue
            result.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters_schema,
                },
            })
        return result

    def execute(self, name: str, **kwargs: Any) -> dict[str, Any]:
        """执行指定工具。

        执行前校验：
        1. 工具已注册
        2. 模型参数不含服务器拥有的作用域字段
        3. 参数通过 JSON Schema 校验
        4. AgentLoop 在异步边界执行超时保护

        执行后校验：
        5. 输出通过 output_schema 校验（如果定义了）

        :param name: 工具名称
        :param kwargs: 工具参数
        :return: 工具返回的结构化数据
        :raises ToolError: 工具不存在、参数包含作用域字段、Schema 校验失败或执行失败
        """
        tool = self._tools.get(name)
        if tool is None:
            raise ToolError(name, f"工具 '{name}' 未注册")

        # 1. 检测模型是否试图提交服务器拥有的作用域字段
        injected = SCOPE_OWNED_FIELDS & set(kwargs.keys())
        if injected:
            raise ToolError(
                name,
                f"参数包含服务器拥有的作用域字段: {sorted(injected)}，"
                f"这些字段由服务器从会话上下文注入，模型不可提交",
                retryable=False,
            )

        # 2. 参数 JSON Schema 校验
        if tool.parameters_schema:
            try:
                jsonschema.validate(instance=kwargs, schema=tool.parameters_schema)
            except jsonschema.ValidationError as ve:
                raise ToolError(
                    name,
                    f"参数 Schema 校验失败: {ve.message}",
                    retryable=False,
                ) from ve
            except jsonschema.SchemaError as exc:
                raise ToolError(name, f"工具参数 Schema 无效: {exc.message}") from exc

        # 3. 执行工具。生产 AgentLoop 会在线程隔离的异步边界施加超时，
        # 避免使用进程级 SIGALRM 干扰并发请求。
        try:
            # P0-4: 执行前检查协作式取消标志
            from ..tools.tool_context import get_tool_context
            ctx = get_tool_context()
            if ctx is not None:
                ctx.check_cancelled()

            result = tool.handler(**kwargs)
        except ToolError:
            raise
        except TimeoutError as exc:
            # P0-4: 协作式取消抛出的 TimeoutError 转为 ToolError
            raise ToolError(name, str(exc), retryable=False) from exc
        except Exception as exc:
            raise ToolError(name, str(exc), retryable=False) from exc

        # 4. 输出 Schema 校验
        if tool.output_schema and isinstance(result, dict):
            try:
                jsonschema.validate(instance=result, schema=tool.output_schema)
            except jsonschema.ValidationError as ve:
                raise ToolError(
                    name,
                    f"输出 Schema 校验失败: {ve.message}",
                    retryable=False,
                ) from ve
            except jsonschema.SchemaError as exc:
                raise ToolError(name, f"工具输出 Schema 无效: {exc.message}") from exc

        return result

def create_default_registry() -> ToolRegistry:
    """创建默认工具注册表，注册所有内置教学分析工具。

    工具列表（17 个工具）：
      - exam: get_exam_statistics, get_question_list, get_question_difficulty,
              get_score_distribution, get_knowledge_coverage
      - student: get_student_list, get_student_scores, get_class_ranking
      - risk: get_at_risk_students, get_risk_factors
      - error: get_error_causes, get_common_mistakes
      - attachment: get_exam_paper_image, get_student_answer_image
      - report: submit_teaching_report
      - profile: get_student_profile, propose_student_profile_update（自动合并并保留审计记录）
    """
    registry = ToolRegistry()

    # 注册所有内置工具
    from ..tools import register_all_tools
    register_all_tools(registry)

    logger.info("工具注册表已创建（当前 %d 个工具）", len(registry.list_all()))
    return registry
