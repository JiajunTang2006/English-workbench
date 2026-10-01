"""工具包：所有 Agent 只读工具的注册入口。"""

from .exam_tools import register_exam_tools
from .student_tools import register_student_tools
from .risk_tools import register_risk_tools
from .error_tools import register_error_tools
from .attachment_tools import register_attachment_tools
from .report_tools import register_report_tools
from .profile_tools import register_profile_tools

__all__ = [
    "register_exam_tools",
    "register_student_tools",
    "register_risk_tools",
    "register_error_tools",
    "register_attachment_tools",
    "register_report_tools",
    "register_profile_tools",
    "register_all_tools",
]


def register_all_tools(registry) -> None:
    """将所有内置工具注册到指定注册表。"""
    register_exam_tools(registry)
    register_student_tools(registry)
    register_risk_tools(registry)
    register_error_tools(registry)
    register_attachment_tools(registry)
    register_report_tools(registry)
    register_profile_tools(registry)
    from .dialogue_tools import register_dialogue_tools
    register_dialogue_tools(registry)
