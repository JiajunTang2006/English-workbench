"""Agent 编排器

编排器是 Agent 系统的入口，负责：
1. 接收教师请求，解析意图和能力
2. 创建 Agent 会话和上下文
3. 加载对应的提示词模板和工具集
4. 调用 Agent 循环执行分析
5. 验证输出并持久化结果
6. 返回结构化响应

编排流程::

    教师请求 -> 意图识别 -> 创建上下文 -> 成本预估 -> [教师确认]
        -> Agent 循环 -> 输出验证 -> 持久化 -> 返回结果
"""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .context import TeachingContext
from .cost import CostEstimator
from .evidence import EvidenceLedger
from .privacy import PrivacyMapper
from .registry.capabilities import CapabilityRegistry, CapabilityDefinition
from .registry.tools import ToolRegistry
from .loop import AgentLoop, LoopResult, LoopStep
from .output_validator import OutputValidator, ValidationResult

if TYPE_CHECKING:
    from .config import AgentConfig
    from .providers.base import TextModelProvider, VisionModelProvider

logger = logging.getLogger(__name__)


def _resolve_prompts_dir() -> Path:
    """定位 prompts 资源目录。

    PyInstaller 打包后 ``__file__`` 位于 PYZ 归档内、并非真实文件系统路径，
    直接用 ``Path(__file__).parent`` 会找不到模板，导致模型只能靠兜底 prompt。
    frozen 模式下改为以 ``sys._MEIPASS`` 为根解析到打包进 bundle 的资源目录。
    """
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root) / "backend" / "app" / "agent" / "prompts"
    return Path(__file__).parent / "prompts"


_PROMPTS_DIR = _resolve_prompts_dir()


@dataclass
class OrchestratorRequest:
    """编排器请求。"""
    teacher_id: int
    capability_name: str
    scope: dict
    user_message: str
    session_id: str | None = None
    # P0-7: 数据库中的 AgentSession ID，用于多轮上下文
    db_session_id: int | None = None
    # P1-11: 教师确认的预算金额；非 None 表示已确认，跳过预算门禁
    confirmed_budget_yuan: float | None = None
    # TeachMate 运行过程事件回调（仅用于实时展示，不影响分析结果）。
    event_sink: object | None = None


@dataclass
class OrchestratorResponse:
    """编排器响应。"""
    success: bool
    session_id: str
    answer: str
    structured_answer: dict | None = None
    evidence: list[dict] | None = None
    cost_yuan: float = 0.0
    tokens_used: int = 0
    elapsed_ms: int = 0
    validation: ValidationResult | None = None
    error: str | None = None
    needs_confirmation: bool = False
    stop_reason: str = ""
    request_ids: list[str] = field(default_factory=list)
    # P0-6: 工具轨迹（用于持久化）
    steps: list[LoopStep] = field(default_factory=list)


class AgentOrchestrator:
    """Agent 编排器。"""

    def __init__(
        self,
        *,
        config: "AgentConfig",
        text_provider: "TextModelProvider",
        vision_provider: "VisionModelProvider | None" = None,
        tool_registry: ToolRegistry,
        capability_registry: CapabilityRegistry,
    ) -> None:
        self._config = config
        self._text_provider = text_provider
        self._vision_provider = vision_provider
        self._tool_registry = tool_registry
        self._capability_registry = capability_registry
        self._loop = AgentLoop(text_provider, tool_registry, config)
        self._validator = OutputValidator()
        self._cost_estimator = CostEstimator()

    async def close(self) -> None:
        """关闭编排器，释放 provider 资源（如 httpx client）。

        通过此公开方法完成生命周期管理，
        调用方无需访问 _text_provider 等私有字段。
        """
        # 关闭 AgentLoop 的工具线程池
        if hasattr(self._loop, "shutdown"):
            self._loop.shutdown()

        provider = self._text_provider
        if hasattr(provider, "close"):
            close_method = provider.close
            if asyncio.iscoroutinefunction(close_method):
                await close_method()
            else:
                close_method()

    async def run(self, request: OrchestratorRequest, db_session=None) -> OrchestratorResponse:
        """执行编排流程（异步）。

        :param db_session: 数据库会话，传递给 AgentLoop 用于工具查询。
        """
        import uuid

        session_id = request.session_id or str(uuid.uuid4())

        # 1. 查找能力定义
        capability = self._capability_registry.get(request.capability_name)
        if capability is None:
            return OrchestratorResponse(
                success=False, session_id=session_id, answer="",
                error=f"未知的能力: {request.capability_name}",
            )

        # 2. 验证作用域
        missing = [r for r in capability.scope_requirements if r not in request.scope]
        if missing:
            return OrchestratorResponse(
                success=False, session_id=session_id, answer="",
                error=f"缺少必要的作用域参数: {missing}",
            )

        # 3. 创建教学上下文
        context = self._create_context(request, capability)

        # 无数据库考试时，教师仍可通过已确认附件进行考试分析。
        # 将正式附件登记为 file_page 证据，使“附件正文 + 证据账本 + 结构化报告”
        # 走完整闭环；该路径只读，不会创建或修改考试事实。
        if (
            request.capability_name == "exam_analysis"
            and request.scope.get("exam_id") is None
            and request.db_session_id is not None
            and db_session is not None
        ):
            self._seed_formal_attachment_evidence(
                context,
                request.db_session_id,
                db_session,
                user_message=request.user_message,
            )

        # 3.5 预注册学生匿名映射（fail-closed 隐私保护）
        if db_session is not None and request.scope.get("run_id"):
            from ..services.agent_analysis.conversation import mapper_for_run
            context.privacy_mapper = mapper_for_run(db_session, request.scope)
        self._pre_register_students(context, db_session)

        # 4. 构建工具列表（按 capability 白名单 + 作用域过滤）
        available_tool_names = capability.required_tools
        if capability.name == "general_chat":
            # 普通对话也可自主选择安全只读查询；ToolRegistry 仍会按当前
            # 会话 scope 过滤无 exam_id 的工具，模型无法提交 scope 参数。
            from .registry.capabilities import GENERAL_CHAT_READ_TOOLS
            available_tool_names = list(GENERAL_CHAT_READ_TOOLS)
        if capability.requires_evidence and request.scope.get("exam_id"):
            from .registry.capabilities import GENERAL_CHAT_READ_TOOLS
            available_tool_names = list(dict.fromkeys(available_tool_names + list(GENERAL_CHAT_READ_TOOLS)))
        if capability.name == "exam_analysis" and request.scope.get("exam_id") is None:
            # 未绑定数据库考试时，只允许提交报告；数据库考试查询工具会因
            # 缺少 exam_id 被过滤。正式资料正文由 FormalContextProvider 注入。
            available_tool_names = ["submit_teaching_report"]
        tools = self._tool_registry.for_model(
            scope=request.scope,
            available_tools=available_tool_names,
        )
        if not tools and capability.requires_evidence:
            return OrchestratorResponse(
                success=False, session_id=session_id, answer="",
                error="当前作用域下无可用工具",
            )
        provider_capabilities = self._text_provider.get_capabilities(self._config.text_model_name)
        if not provider_capabilities.supports_tool_calls:
            tools = []
            if capability.requires_evidence:
                return OrchestratorResponse(success=False, session_id=session_id, answer="",
                    error="该模型不支持工具调用，无法执行正式分析。")

        # 5. 成本预估（粗略）
        from .token_budget import resolve_token_budget
        token_budget = resolve_token_budget(self._config)
        estimate = self._cost_estimator.estimate_text(
            model_name=self._config.text_model_name,
            estimated_input_tokens=2000,
            estimated_output_tokens=token_budget.output_limit,
        )
        # P1-11: 如果教师已确认预算，跳过门禁
        if request.confirmed_budget_yuan is None and estimate.estimated_cost_yuan > capability.budget_limit_yuan:
            return OrchestratorResponse(
                success=False, session_id=session_id,
                answer=f"预估成本 {estimate.estimated_cost_yuan:.2f} 元，"
                       f"超过预算 {capability.budget_limit_yuan:.2f} 元，请确认是否继续。",
                needs_confirmation=True,
            )

        # 6. 构建系统提示词
        system_prompt = self._build_system_prompt(capability, context, db_session=db_session)
        if not provider_capabilities.supports_tool_calls:
            system_prompt += "\n当前模型为无工具对话模式，只使用已提供事实；没有提供的数据不得声称已查询。"
            if db_session is not None and context.student_id and capability.name == "general_chat":
                from ..services.student_learning import learning_evidence
                import json
                facts = learning_evidence(db_session, context.scope, context.privacy_mapper)
                system_prompt += "\n【本地预查学习证据】\n" + context.privacy_mapper.sanitize_text(
                    json.dumps(context.privacy_mapper.sanitize_for_model(facts), ensure_ascii=False, default=str))
            elif db_session is not None and context.exam_id:
                from .tools.tool_context import ToolContext, set_tool_context, reset_tool_context
                from .tools.student_tools import _get_student_scores
                from .tools.exam_tools import _get_exam_statistics
                token = set_tool_context(ToolContext(db_session=db_session, scope=context.scope))
                try:
                    facts = _get_student_scores() if context.student_id else _get_exam_statistics()
                finally:
                    reset_tool_context(token)
                import json
                system_prompt += "\n【本地预查事实】\n" + context.privacy_mapper.sanitize_text(
                    json.dumps(context.privacy_mapper.sanitize_for_model(facts), ensure_ascii=False, default=str))
        if db_session is not None and request.scope.get("run_id"):
            from ..models.agent_entities import AnalysisRun
            current_run = db_session.get(AnalysisRun, request.scope["run_id"])
            turn_context = (current_run.input_summary_json or {}).get("conversation_context") if current_run else None
            if turn_context:
                import json
                system_prompt += "\n【当前讨论对象与教师纠正】\n" + context.privacy_mapper.sanitize_text(
                    json.dumps(turn_context, ensure_ascii=False))


        # P0-7: 构建多轮上下文消息（如果存在数据库会话）
        multi_turn_messages: list[dict] | None = None
        if request.db_session_id is not None and db_session is not None:
            try:
                from ..services.agent_analysis.sessions import SessionService
                session_svc = SessionService(db_session)
                multi_turn_messages = session_svc.build_multi_turn_messages(
                    session_id=request.db_session_id,
                    system_prompt=system_prompt,
                    user_message=request.user_message,
                    privacy_mapper=context.privacy_mapper,
                    current_run_id=request.scope.get("run_id"),
                    formal_context_token_budget=token_budget.formal_context_limit,
                    history_token_budget=token_budget.history_limit,
                )
                logger.info(
                    "多轮上下文: db_session_id=%d, 消息数=%d",
                    request.db_session_id, len(multi_turn_messages),
                )
            except Exception as e:
                logger.warning("构建多轮上下文失败，降级为单轮: %s", e)
                multi_turn_messages = None

        # 7. 执行 Agent 循环
        loop_result = await self._loop.run(
            system_prompt=system_prompt,
            user_message=request.user_message,
            context=context,
            tools=tools,
            max_iterations=capability.max_iterations,
            max_parallel_tools=capability.max_parallel_tools,
            budget_limit_yuan=context.budget_limit_yuan,
            db_session=db_session,
            messages=multi_turn_messages,
            require_evidence=capability.requires_evidence,
            event_sink=request.event_sink,
        )

        if not loop_result.success and loop_result.stop_reason == "error":
            return OrchestratorResponse(
                success=False, session_id=session_id, answer="",
                error=loop_result.error,
                cost_yuan=loop_result.total_cost_yuan,
                tokens_used=loop_result.total_tokens,
                elapsed_ms=loop_result.elapsed_ms,
            )

        # P0-5: 数据不足时直接返回明确错误，不进入修复重试（无证据无法修复）。
        if not loop_result.success and loop_result.stop_reason == "data_insufficient":
            return OrchestratorResponse(
                success=False,
                session_id=session_id,
                answer=loop_result.final_answer,
                error="模型未调用任何工具就给出回答，缺乏数据支撑，无法完成分析。",
                cost_yuan=loop_result.total_cost_yuan,
                tokens_used=loop_result.total_tokens,
                elapsed_ms=loop_result.elapsed_ms,
                stop_reason="data_insufficient",
            )

        # 普通对话保留自然语言输出，不套用教学分析 JSON/evidence 契约。
        if not capability.requires_evidence:
            return OrchestratorResponse(
                success=loop_result.success,
                session_id=session_id,
                answer=loop_result.final_answer,
                structured_answer=None,
                evidence=[],
                cost_yuan=loop_result.total_cost_yuan,
                tokens_used=loop_result.total_tokens,
                elapsed_ms=loop_result.elapsed_ms,
                stop_reason=loop_result.stop_reason,
                request_ids=loop_result.request_ids,
                steps=loop_result.steps,
                error=loop_result.error,
            )

        # 8. 验证输出（含 finish_reason）
        last_finish_reason = ""
        if loop_result.steps:
            last_step = loop_result.steps[-1]
            if last_step.model_response:
                last_finish_reason = last_step.model_response.finish_reason

        validation = self._validator.validate(
            structured_answer=loop_result.structured_answer,
            evidence_ledger=context.evidence_ledger,
            privacy_mapper=context.privacy_mapper,
            output_schema=capability.output_schema,
            raw_text=loop_result.final_answer,
            finish_reason=last_finish_reason,
            db_session=db_session,
            run_id=request.scope.get("run_id"),
        )

        # 8a. 安全降级：第一次验证失败时，一次低温修复重试（tools-disabled, temperature=0）
        if not validation.valid:
            logger.warning("首次输出验证失败，尝试低温修复: %s", validation.errors)
            repair_result = await self._loop.repair_call(
                system_prompt=system_prompt,
                user_message=request.user_message,
                invalid_answer=loop_result.final_answer,
                validation_errors=validation.errors,
                evidence_ids=context.evidence_ledger.all_ids(),
                context=context,
                budget_limit_yuan=context.budget_limit_yuan,
                cost_already_spent=loop_result.total_cost_yuan,
                db_session=db_session,
                event_sink=request.event_sink,
            )

            # 合并 usage 和 request IDs
            loop_result.total_tokens += repair_result.total_tokens
            loop_result.total_cost_yuan += repair_result.total_cost_yuan
            loop_result.request_ids.extend(repair_result.request_ids)
            loop_result.steps.extend(repair_result.steps)
            loop_result.elapsed_ms += repair_result.elapsed_ms

            if repair_result.success and repair_result.structured_answer is not None:
                retry_finish_reason = ""
                if repair_result.steps:
                    last_retry_step = repair_result.steps[-1]
                    if last_retry_step.model_response:
                        retry_finish_reason = last_retry_step.model_response.finish_reason

                validation = self._validator.validate(
                    structured_answer=repair_result.structured_answer,
                    evidence_ledger=context.evidence_ledger,
                    privacy_mapper=context.privacy_mapper,
                    output_schema=capability.output_schema,
                    raw_text=repair_result.final_answer,
                    finish_reason=retry_finish_reason,
                    db_session=db_session,
                    run_id=request.scope.get("run_id"),
                )

                if validation.valid:
                    loop_result.final_answer = repair_result.final_answer
                    loop_result.structured_answer = repair_result.structured_answer
                    loop_result.stop_reason = "completed"

            # 第二次仍失败 → 安全降级
            if not validation.valid:
                logger.error("低温修复仍失败，执行安全降级: %s", validation.errors)
                fallback = self._validator.build_fallback_summary(
                    context.evidence_ledger, context.privacy_mapper
                )
                validation = ValidationResult(
                    valid=False,
                    errors=validation.errors,
                    warnings=validation.warnings,
                    degraded=True,
                    fallback_summary=fallback,
                )
                loop_result = LoopResult(
                    success=True,
                    final_answer="报告生成失败，已保留可靠统计摘要",
                    structured_answer=fallback,
                    steps=loop_result.steps,
                    total_tokens=loop_result.total_tokens,
                    total_cost_yuan=loop_result.total_cost_yuan,
                    elapsed_ms=loop_result.elapsed_ms,
                    stop_reason="degraded",
                    request_ids=loop_result.request_ids,
                )

        # 9. 恢复真实身份用于内部存储
        evidence_for_display = context.evidence_ledger.for_display(
            privacy_mapper=context.privacy_mapper
        )

        return OrchestratorResponse(
            success=loop_result.success,
            session_id=session_id,
            answer=loop_result.final_answer,
            structured_answer=loop_result.structured_answer,
            evidence=evidence_for_display,
            cost_yuan=loop_result.total_cost_yuan,
            tokens_used=loop_result.total_tokens,
            elapsed_ms=loop_result.elapsed_ms,
            validation=validation,
            stop_reason=loop_result.stop_reason,
            request_ids=loop_result.request_ids,
            steps=loop_result.steps,
        )

    def _create_context(self, request: OrchestratorRequest, capability: CapabilityDefinition) -> TeachingContext:
        """创建教学上下文。"""
        effective_budget = max(
            capability.budget_limit_yuan,
            request.confirmed_budget_yuan or 0.0,
        )
        return TeachingContext(
            scope=request.scope,
            capability=capability.name,
            available_tools=capability.required_tools,
            privacy_mapper=PrivacyMapper(allow_student_names=True),
            evidence_ledger=EvidenceLedger(),
            budget_limit_yuan=effective_budget,
        )

    def _seed_formal_attachment_evidence(
        self,
        context: TeachingContext,
        session_id: int,
        db_session,
        *,
        user_message: str = "",
    ) -> None:
        """为未绑定数据库考试的附件分析登记可追溯的文件证据。

        正文仍由 SessionService/FormalContextProvider 注入模型上下文；这里仅把
        已确认附件的元数据登记进本轮证据账本，避免该能力因没有数据库工具而被
        判定为“无证据”。不读取或写入考试、成绩等结构化事实表。
        """
        try:
            from ..services.agent_analysis.formal_context import FormalContextProvider
            from ..config import get_settings

            provider = FormalContextProvider(
                db_session, data_dir=get_settings().data_dir,
            )
            attachments = provider.list_formal_attachments(session_id)
            for attachment in attachments:
                context.evidence_ledger.add(
                    evidence_type="file_page",
                    local_fact={
                        "attachment_id": attachment["attachment_id"],
                        "title": attachment["title"],
                        "original_name": attachment["original_name"],
                    },
                    source_file=attachment["original_name"],
                    contains_personal_data=True,
                    display_summary=f"已确认资料：{attachment['original_name']}",
                )
            if user_message.strip():
                context.evidence_ledger.add(
                    evidence_type="rule_signal",
                    local_fact={"source": "teacher_message"},
                    source_entity="teacher_message",
                    contains_personal_data=False,
                    display_summary="教师本轮提供的分析要求",
                )
        except Exception as exc:
            logger.warning("登记正式附件证据失败: %s", exc)

    def _pre_register_students(self, context: TeachingContext, db_session=None) -> None:
        """构建并注册运行内身份词典（B2-03）。

        从数据库查询当前作用域内学生（按 term/class/student 过滤），
        把姓名、学号、电话、教师姓名注册进 PrivacyMapper：
        - 匿名编号映射（sanitize_for_model 使用）；
        - 文本脱敏词典（sanitize_text 使用，姓名替换为编号、学校/教师脱敏）。
        """
        if context.privacy_mapper is None:
            return

        from ..services.agent_analysis.identity_dict import (
            build_run_identity_dictionary,
            register_identity_into_mapper,
        )

        try:
            identity = build_run_identity_dictionary(
                db_session,
                term_id=context.term_id,
                class_id=context.class_id,
                student_id=context.student_id,
            )
        except Exception as exc:
            logger.warning("构建身份词典失败，仅注册 scope 学生: %s", exc)
            identity = None

        if identity is not None and db_session is not None:
            register_identity_into_mapper(context.privacy_mapper, identity)
            return

        # 兜底：至少注册单个学生
        if context.student_id is not None:
            context.privacy_mapper.register_student(context.student_id)

    def _build_system_prompt(
        self,
        capability: CapabilityDefinition,
        context: TeachingContext,
        *,
        db_session=None,
    ) -> str:
        """构建系统提示词，从 prompts/ 目录加载模板。"""
        evidence_rules = (
            [
                "1. 必须先调用工具获取数据，再给出分析结论",
                "2. 所有结论必须有数据支撑，引用证据 ID",
            ]
            if capability.requires_evidence
            else [
                "1. 普通对话默认直接回答；如果问题涉及当前班级/考试的分组、分层、复习或教学建议，应先自主选择合适的只读查询工具获取真实统计，再结合结果回答。",
                "2. 一般教学问题可直接回答，不要求考试数据；缺数据只限制相关事实判断，仍可提供一般建议。必要时简短追问，不反复尝试相同失败查询。",
                "3. 只读查询结果仅用于本轮回答，不得修改学生画像、考试或其他数据库事实。",
            ]
        )
        prompt_parts = [
            f"你是一个专业的教学分析助手。当前能力：{capability.display_name}。",
            f"分析目标：{capability.description}",
            "",
            "重要规则：",
            *evidence_rules,
            "4. 沿用输入与工具提供的学生称呼或引用，不猜测身份对应关系",
            "5. 如果数据不足，明确说明而非臆测",
            "6. 按问题选择长度与形式；简短追问直接回应，不重复套用报告章节；区分事实、解释和建议",
            "教师明确要求只查某题或只比较某项时，就在该范围回答；不附加未请求的总分、能力标签或继续提问。",
            "7. 若当前能力要求输出结构化 JSON，严格只输出 JSON；JSON 字段中的自然语言仍保持短句、可读和分层",
            "",
            f"当前作用域: {context.to_model_context()}",
        ]
        if db_session is not None:
            from ..services.subjects import get_selected_subject
            subject = get_selected_subject(db_session)
            prompt_parts.extend([
                "",
                f"当前任教学科是「{subject.label}」（{subject.key}）。可参考的分析维度："
                f"{'、'.join(subject.analysis_dimensions)}。错误归因候选："
                f"{'、'.join(subject.error_causes)}。只能在证据支持时使用，"
                "证据不足时标为待观察；英语专用知识点和课标不得套用于其他学科。",
            ])

        # exam_analysis 支持不绑定数据库考试的“资料分析”模式。
        # 明确提示模型不要把上传资料臆称为数据库事实，也不要尝试写入考试数据。
        if capability.name == "exam_analysis" and context.exam_id is None:
            evidence_ids = context.evidence_ledger.all_ids() if context.evidence_ledger else []
            prompt_parts.extend([
                "",
                "当前未绑定数据库中的具体考试。请仅基于教师已确认的正式资料、"
                "用户文字和本轮可用证据进行分析；不得声称读取了数据库考试成绩，"
                "不得创建、修改或补写数据库考试事实。",
                "当前正式资料证据 ID：" + ("、".join(evidence_ids) if evidence_ids else "暂无"),
                "请直接使用上述证据 ID 生成并调用 submit_teaching_report；"
                "不要尝试调用需要数据库考试的统计工具。",
                "如果资料不足，请在 limitations 中明确说明，不要编造统计数字。",
            ])

        # 加载提示词模板文件
        if capability.prompt_template:
            prompt_file = _PROMPTS_DIR / capability.prompt_template
            if prompt_file.exists():
                template = prompt_file.read_text(encoding="utf-8")
                prompt_parts.append("")
                prompt_parts.append(template)

        from ..services.personalization import (
            build_personalization_prompt,
            read_personalization_settings,
        )
        prompt_parts.extend([
            "",
            build_personalization_prompt(read_personalization_settings(db_session)),
        ])

        return "\n".join(prompt_parts)
