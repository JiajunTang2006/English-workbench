"""Expose the same bounded read contracts to both conversation runtimes."""
from ..registry.tools import ToolDefinition
from .tool_context import get_tool_context

SCHEMAS = {
    "get_student_learning_evidence": {"student_ref": {"type": "string"}, "horizon": {"type": "integer", "minimum": 1, "maximum": 6}},
    "get_original_question": {"question_ref": {"type": "string", "pattern": "^question_[0-9]+$"}, "student_ref": {"type": "string"}},
    "get_practice_context": {"student_ref": {"type": "string", "pattern": "^student_[0-9]+$"}},
    "resolve_student": {"query": {"type": "string", "minLength": 1, "maxLength": 100}},
    "get_exam_overview": {},
    "get_wrong_questions": {"top_n": {"type": "integer", "minimum": 1, "maximum": 20}},
    "get_student_trend": {"horizon": {"type": "integer", "minimum": 1, "maximum": 10}},
}


def register_dialogue_tools(registry):
    for name, properties in SCHEMAS.items():
        def handler(_tool=name, **args):
            import contextlib
            import io
            import json
            from ..education_bridge.bridge_cli import run_tool
            ctx = get_tool_context()
            if ctx is None or ctx.db_session is None:
                return {"error": "数据库不可用"}
            try:
                with contextlib.redirect_stdout(io.StringIO()) as captured:
                    result = run_tool(_tool, ctx.db_session, ctx.scope, args)
                return result["data"] if "data" in result["data"] else {"data": result["data"]}
            except SystemExit:
                try:
                    return {"error": json.loads(captured.getvalue()).get("error", "查询失败")}
                except ValueError:
                    return {"error": "查询失败"}
        registry.register(ToolDefinition(name=name, description={
            "get_practice_context": "按需读取当前教学任务的练习、提示使用及新题复测表现。",
            "get_student_learning_evidence": "查询已确认学生最近考试的小分、真实失分题引用和教师确认画像；无需选择考试或创建任务。",
            "get_original_question": "按学习证据的 question_ref 读取该学生已确认原错题、答案和已提取来源页。",
            "resolve_student": "在授权名单中确认学生，返回引用或歧义候选；候选需确认。",
            "get_exam_overview": "本地核算当前考试概览。",
            "get_wrong_questions": "查询真实知识点表现；数据缺失不编造错题率。",
            "get_student_trend": "查询当前学生或班级最近考试趋势。",
        }[name], parameters_schema={"type": "object", "properties": properties,
            "required": ["query"] if name == "resolve_student" else ["question_ref"] if name == "get_original_question" else [], "additionalProperties": False},
            output_schema={"type": "object", "properties": {"data": {"type": "object"}, "error": {"type": "string"}},
                "anyOf": [{"required": ["data"]}, {"required": ["error"]}]},
            handler=handler, category="student" if name == "resolve_student" else "exam",
            requires_scope=["term_id"] if name in {"resolve_student", "get_student_trend", "get_practice_context", "get_student_learning_evidence", "get_original_question"} else ["exam_id"]))
