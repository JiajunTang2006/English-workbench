"""输出验证器

验证 Agent 循环的最终输出是否符合预期结构和质量标准。

验证维度：
1. 结构验证：输出是否包含必需字段（answer_type / summary / findings / recommendations / limitations）
2. 证据引用验证：所有结论是否都有证据支撑，引用的 evidence ID 必须存在
3. 隐私检查：输出中不应包含真实学生姓名等敏感信息
4. 格式验证：输出是否符合能力声明的 output_schema
5. finish_reason 检查：length/error 不得标记成功
6. 安全降级：第一次验证失败时低温修复重试，第二次失败返回本地确定性统计摘要
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .evidence import EvidenceLedger
from .models import StructuredAnswer
from .privacy import PrivacyMapper

logger = logging.getLogger(__name__)

# 学号模式：6-10 位连续数字，前后不得是数字（兼容中文紧邻上下文）
_STUDENT_NO_PATTERN = re.compile(r"(?<!\d)\d{6,10}(?!\d)")
# 电话模式
_PHONE_PATTERN = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
# HTML/脚本注入模式
_HTML_PATTERN = re.compile(r"<(?:script|iframe|img|svg|on\w+)", re.IGNORECASE)


@dataclass
class ValidationResult:
    """验证结果。"""

    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # 降级标志：当返回本地确定性统计摘要时为 True
    degraded: bool = False
    # 降级时的确定性摘要
    fallback_summary: dict | None = None


class OutputValidator:
    """Agent 输出验证器。

    验证流程：
    1. 结构验证（Pydantic StructuredAnswer）
    2. 证据引用验证
    3. 隐私泄露检查
    4. finish_reason 检查
    5. HTML/脚本注入检查

    安全降级：
    - 第一次验证失败 -> 调用方可进行一次低温修复重试
    - 第二次验证失败 -> 返回本地确定性统计摘要（degraded=True）
    """

    def validate(
        self,
        *,
        structured_answer: dict | None,
        evidence_ledger: EvidenceLedger,
        privacy_mapper: PrivacyMapper,
        output_schema: dict[str, Any] | None = None,
        raw_text: str = "",
        finish_reason: str = "",
        db_session: Any | None = None,
        run_id: int | None = None,
    ) -> ValidationResult:
        """验证 Agent 输出。

        :param structured_answer: 解析后的结构化答案
        :param evidence_ledger: 证据账本
        :param privacy_mapper: 隐私映射器
        :param output_schema: 能力声明的输出 Schema（可选）
        :param raw_text: 模型原始输出文本
        :param finish_reason: 模型完成原因（stop/length/error/tool_calls）
        :param db_session: 数据库会话（提供时校验持久化证据归属，B2-06）
        :param run_id: 当前运行 ID（与 db_session 一起使用）
        :return: ``ValidationResult``
        """
        errors: list[str] = []
        warnings: list[str] = []

        # 0. finish_reason 检查
        if finish_reason in ("length", "error"):
            errors.append(
                f"模型输出未正常完成 (finish_reason={finish_reason})，"
                f"输出可能被截断或出错"
            )

        if structured_answer is None:
            errors.append("输出无法解析为结构化 JSON")
            return ValidationResult(valid=False, errors=errors)

        # 1. Pydantic 结构验证
        self._validate_structure(structured_answer, errors, warnings)

        # 2. 证据引用验证（统一契约：非空、存在、归属当前 run、类型允许）
        self._validate_evidence_refs(
            structured_answer, evidence_ledger, errors, warnings,
            db_session=db_session, run_id=run_id,
        )

        # 3. 隐私检查
        self._validate_privacy(raw_text, structured_answer, privacy_mapper, errors, warnings)

        # 4. HTML/脚本注入检查
        self._validate_no_injection(raw_text, errors, warnings)

        # 5. Schema 验证（如果提供了 schema）
        if output_schema:
            self._validate_schema(structured_answer, output_schema, errors, warnings)

        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )

    def _validate_structure(
        self,
        answer: dict,
        errors: list[str],
        warnings: list[str],
    ) -> None:
        """使用 Pydantic 验证输出结构。"""
        try:
            parsed = StructuredAnswer(**answer)
        except Exception as exc:
            errors.append(f"结构验证失败: {exc}")
            return

        # 额外检查：findings 不能为空（至少 1 条）
        if len(parsed.findings) == 0:
            warnings.append("findings 为空，分析可能不充分")

    def _validate_evidence_refs(
        self,
        answer: dict,
        ledger: EvidenceLedger,
        errors: list[str],
        warnings: list[str],
        *,
        db_session: Any | None = None,
        run_id: int | None = None,
    ) -> None:
        """按统一契约验证所有 finding / recommendation 的证据引用（B2-06）。

        与 report_tools 共用 validate_report_evidence_references：
        非空、存在（内存 ledger + 当前 run 持久化证据）、归属当前 run、
        类型允许；重复 ID 规范化去重且不改变展示顺序。
        """
        from .evidence import validate_report_evidence_references

        ref_errors = validate_report_evidence_references(
            answer.get("findings", []),
            answer.get("recommendations", []),
            ledger=ledger,
            db_session=db_session,
            run_id=run_id,
        )
        errors.extend(ref_errors)

    def _validate_privacy(
        self,
        text: str,
        answer: dict,
        mapper: PrivacyMapper,
        errors: list[str],
        warnings: list[str],
    ) -> None:
        """检查输出中是否泄露真实身份信息（B2-03）。

        递归检查 raw_text 与 structured_answer 的所有字符串：
        - 学号、电话格式；
        - 当前运行身份词典中的真实姓名、学校名与受保护文本（fail-closed）。
        错误消息不打印真实姓名或敏感值。
        """
        # 收集所有需要检查的文本
        texts_to_check: list[str] = []
        if text:
            texts_to_check.append(text)

        # 递归提取 structured_answer 中的所有字符串值
        if answer:
            self._collect_text_values(answer, texts_to_check)

        # 当前运行身份词典（真实姓名/学校/受保护词）——仅用于命中判断
        identity_terms: set[str] = set()
        if mapper is not None:
            if not getattr(mapper, "allow_student_names", False):
                identity_terms.update(mapper.known_real_names)
            identity_terms.update(mapper.known_school_names)
            identity_terms.update(mapper.known_protected_terms)
        identity_terms = {t for t in identity_terms if t and len(t) >= 2}
        ordered_terms = sorted(identity_terms, key=len, reverse=True)

        for t in texts_to_check:
            # 学号：只有词典内真实学号才算命中（受保护词匹配已覆盖）。
            # 裸 6-10 位数字仅仅告警，避免把分数/日期/序号误判为学号而拒绝合法报告。
            if _STUDENT_NO_PATTERN.search(t):
                warnings.append("输出包含疑似学号格式数字（已忽略，需词典匹配确认）")

            # 电话格式（11 位手机号形态）→ 硬失败：是确凿 PII
            if _PHONE_PATTERN.search(t):
                errors.append("输出中可能包含电话号码（已拒绝持久化）")

            # 检查身份词典命中（姓名/学校/受保护文本）
            for term in ordered_terms:
                if term in t:
                    errors.append("输出中包含当前运行的真实身份信息（姓名/学校/受保护文本），已拒绝")
                    break

    def _collect_text_values(self, obj: Any, out: list[str]) -> None:
        """递归收集 dict/list 中的所有字符串值。"""
        if isinstance(obj, str):
            out.append(obj)
        elif isinstance(obj, dict):
            for v in obj.values():
                self._collect_text_values(v, out)
        elif isinstance(obj, list):
            for item in obj:
                self._collect_text_values(item, out)

    def _validate_no_injection(
        self,
        text: str,
        errors: list[str],
        warnings: list[str],
    ) -> None:
        """检查输出中是否包含可执行 HTML 或脚本。"""
        if not text:
            return
        if _HTML_PATTERN.search(text):
            errors.append("输出中包含可能的 HTML/脚本注入")

    def _validate_schema(
        self,
        answer: dict,
        schema: dict,
        errors: list[str],
        warnings: list[str],
    ) -> None:
        """验证输出是否符合声明的 JSON Schema。"""
        try:
            import jsonschema
            jsonschema.validate(instance=answer, schema=schema)
        except ImportError:
            required = schema.get("required", [])
            for field_name in required:
                if field_name not in answer:
                    errors.append(f"Schema 验证失败: 缺少必需字段 '{field_name}'")
        except Exception as exc:
            errors.append(f"Schema 验证失败: {exc}")

    def build_fallback_summary(
        self,
        evidence_ledger: EvidenceLedger,
        privacy_mapper: PrivacyMapper,
    ) -> dict:
        """构建本地确定性统计摘要（安全降级时使用）。

        从证据账本中提取已验证的数值型事实，生成最小确定性摘要。
        不包含模型生成的任何分析内容。
        """
        findings: list[dict] = []
        evidence_ids: list[str] = []

        for ev_id in evidence_ledger.all_ids():
            ev = evidence_ledger.get(ev_id)
            if ev is None:
                continue
            evidence_ids.append(ev_id)
            # 从 local_fact 中提取数值型事实
            fact = ev.local_fact
            if isinstance(fact, dict):
                # 提取基本统计数据
                for key in ("average_score", "pass_rate", "excellent_rate",
                            "participant_count", "max_score", "min_score", "std_dev"):
                    if key in fact:
                        findings.append({
                            "title": f"{key}: {fact[key]}",
                            "evidence_ids": [ev_id],
                        })

        return {
            "answer_type": "fallback_summary",
            "summary": "报告生成失败，已保留可靠统计摘要",
            "findings": findings,
            "recommendations": [],
            "limitations": ["本次报告因验证失败而降级，仅包含已验证的统计数据"],
            "scope_snapshot": {},
            "schema_version": "1.0.0",
            "degraded": True,
        }
