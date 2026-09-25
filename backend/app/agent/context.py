"""
统一教学上下文 (TeachingContext)

每个对话轮次建立统一上下文，由服务器构建。
模型和客户端不能自行改变学期、班级、考试和学生作用域。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..evidence import EvidenceLedger
    from ..privacy import PrivacyMapper


@dataclass
class TeachingContext:
    """统一教学上下文。

    由服务器在每轮对话开始时构建，注入到 Agent 循环中。
    模型不能提升作用域或修改上下文。
    """

    # 作用域
    scope: dict[str, Any] = field(default_factory=dict)
    # 如 {"exam_id": 123, "class_id": 5, "student_id": 42}

    # 能力与工具
    capability: str = ""
    available_tools: list[str] = field(default_factory=list)

    # 隐私映射（本地持有，不发送到云端）
    privacy_mapper: "PrivacyMapper | None" = None

    # 证据账本
    evidence_ledger: "EvidenceLedger | None" = None

    # 预算
    budget_limit_yuan: float = 0.5

    # 对话摘要
    conversation_summary: str = ""

    # 规则版本
    rules_version: str = "v1.0.0"

    # 会话信息（可选）
    session_id: int | None = None
    message_id: int | None = None
    teacher_request: str = ""
    term_id: int | None = None

    @property
    def exam_id(self) -> int | None:
        return self.scope.get("exam_id")

    @property
    def class_id(self) -> int | None:
        return self.scope.get("class_id")

    @property
    def student_id(self) -> int | None:
        return self.scope.get("student_id")

    def to_model_context(self) -> dict[str, Any]:
        """生成发送给模型的安全上下文（不含真实身份和本地 ID）。"""
        return {
            "capability": self.capability,
            "has_exam_scope": self.exam_id is not None,
            "has_class_scope": self.class_id is not None,
            "has_student_scope": self.student_id is not None,
            "budget_limit_yuan": self.budget_limit_yuan,
            "rules_version": self.rules_version,
            "conversation_summary": self.conversation_summary,
        }

    def validate_scope(
        self, term_id: int | None = None,
        class_id: int | None = None,
        exam_id: int | None = None,
        student_id: int | None = None,
    ) -> bool:
        """验证请求的作用域是否在上下文允许范围内。"""
        if term_id is not None and self.term_id is not None and term_id != self.term_id:
            return False
        if class_id is not None and self.class_id is not None and class_id != self.class_id:
            return False
        if exam_id is not None and self.exam_id is not None and exam_id != self.exam_id:
            return False
        if student_id is not None and self.student_id is not None and student_id != self.student_id:
            return False
        return True
