"""Codex 插件的 MCP 工具定义（TeachMatePluginAPI v1）。

仅描述稳定契约：名称、描述、输入 Schema、敏感等级注释。
业务逻辑全在服务端，本文件只做协议映射。
"""
from __future__ import annotations

API_VERSION = "1.0"

# 工具 → 路由映射：(http_method, path 模板, 路径参数键, 查询参数键)
TOOL_ROUTES = {
    "get_teachmate_status": ("GET", "/api/v1/plugin/status", [], []),
    "list_teaching_scopes": ("GET", "/api/v1/plugin/scopes", [], ["term_id"]),
    "get_exam_snapshot": (
        "GET", "/api/v1/plugin/exams/{exam_id}/snapshot", ["exam_id"],
        ["term_id", "class_id"],
    ),
    "list_analysis_reports": (
        "GET", "/api/v1/plugin/analysis-reports", [],
        ["term_id", "class_id", "exam_id", "limit"],
    ),
    "get_latest_analysis_report": (
        "GET", "/api/v1/plugin/analysis-reports/latest", [],
        ["exam_id", "term_id", "class_id", "identify", "run_id"],
    ),
    "get_student_learning_profile": (
        "GET", "/api/v1/plugin/students/{student_id}/profile", ["student_id"],
        ["term_id", "identify"],
    ),
    "search_students": (
        "GET", "/api/v1/plugin/students/search", [],
        ["term_id", "class_id", "keyword", "identify", "limit"],
    ),
    "get_student_practice_context": (
        "GET", "/api/v1/plugin/students/{student_id}/practice-context", ["student_id"],
        ["term_id", "exam_id", "limit"],
    ),
    "get_review_plan_facts": (
        "GET", "/api/v1/plugin/review-plans/facts", [],
        ["exam_id", "term_id", "class_id"],
    ),
    "list_formal_materials": ("GET", "/api/v1/plugin/materials", [], ["term_id"]),
    "read_formal_material": (
        "GET", "/api/v1/plugin/materials/{material_id}", ["material_id"],
        ["term_id", "page", "page_size"],
    ),
    "get_evidence": ("GET", "/api/v1/plugin/evidence/{evidence_id}", ["evidence_id"], []),
}

TOOL_SPECS = [
    {
        "name": "get_teachmate_status",
        "description": "查询 TeachMate 插件状态、版本、健康与可用工具列表。用于连接自检。",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "sensitivity": "low"},
    },
    {
        "name": "list_teaching_scopes",
        "description": "列出可选教学作用域：学期、班级与考试。必须先确定 term_id 再调用其他工具。",
        "inputSchema": {
            "type": "object",
            "properties": {"term_id": {"type": "integer", "description": "学期 ID；缺省用当前学期"}},
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "sensitivity": "low"},
    },
    {
        "name": "get_exam_snapshot",
        "description": "获取考试的确定性统计快照与数据质量；传入 class_id 后只返回该班级数据。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "exam_id": {"type": "integer", "description": "考试 ID"},
                "term_id": {"type": "integer", "description": "学期 ID（越学期将被拒绝）"},
                "class_id": {"type": "integer", "description": "可选：严格限定班级"},
            },
            "required": ["exam_id"],
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "sensitivity": "medium"},
    },
    {
        "name": "list_analysis_reports",
        "description": "列出 TeachMate 中已经生成的考试分析报告。先用本工具发现真实的报告和作用域，再读取具体报告，避免猜测 exam_id 或误把未生成报告当成空报告。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "term_id": {"type": "integer", "description": "学期 ID；缺省用当前学期"},
                "class_id": {"type": "integer", "description": "可选：只列出该班级报告"},
                "exam_id": {"type": "integer", "description": "可选：只列出该考试报告"},
                "limit": {"type": "integer", "description": "最多返回数量，默认 50，最大 100"},
            },
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "sensitivity": "medium"},
    },
    {
        "name": "get_latest_analysis_report",
        "description": "读取 TeachMate 已生成的最新考试分析报告。报告严格绑定学期、班级和考试，不会重新调用模型；默认匿名化学生信息，生成正式教师材料时可显式设置 identify=true。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "exam_id": {"type": "integer", "description": "考试 ID"},
                "term_id": {"type": "integer", "description": "学期 ID"},
                "class_id": {"type": "integer", "description": "可选：班级 ID；不传表示全体班级分析"},
                "identify": {"type": "boolean", "description": "是否保留学生姓名；默认 false"},
                "run_id": {"type": "integer", "description": "可选：指定某次已完成分析运行"},
            },
            "required": ["exam_id"],
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "sensitivity": "high"},
    },
    {
        "name": "get_student_learning_profile",
        "description": "获取学生匿名化趋势与薄弱项（默认匿名；identify=true 才返回可识别信息）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "student_id": {"type": "integer"},
                "term_id": {"type": "integer"},
                "identify": {"type": "boolean", "description": "是否返回可识别姓名/学号，默认 false"},
            },
            "required": ["student_id"],
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "sensitivity": "high"},
    },
    {
        "name": "search_students",
        "description": "在指定学期和班级内检索学生。默认不返回姓名；教师明确按姓名选择学生时才传 identify=true。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "term_id": {"type": "integer"},
                "class_id": {"type": "integer"},
                "keyword": {"type": "string", "description": "姓名或学号片段"},
                "identify": {"type": "boolean", "description": "是否返回姓名，默认 false"},
                "limit": {"type": "integer", "description": "最多返回 100 条，默认 50"},
            },
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "sensitivity": "high"},
    },
    {
        "name": "get_student_practice_context",
        "description": "获取个性化出题上下文：已确认学生画像、薄弱知识点、错因分布和真实错题。用于生成同类题、变式题和迁移题；不会修改数据库。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "student_id": {"type": "integer"},
                "term_id": {"type": "integer"},
                "exam_id": {"type": "integer", "description": "可选：只参考某场考试"},
                "limit": {"type": "integer", "description": "最多返回 50 道错题，默认 20"},
            },
            "required": ["student_id"],
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "sensitivity": "high"},
    },
    {
        "name": "get_review_plan_facts",
        "description": "获取复习计划事实集合：题型均分、共性错题维度、薄弱知识点与覆盖统计。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "exam_id": {"type": "integer"},
                "term_id": {"type": "integer"},
                "class_id": {"type": "integer", "description": "可选：限定班级"},
            },
            "required": ["exam_id"],
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "sensitivity": "medium"},
    },
    {
        "name": "list_formal_materials",
        "description": "列出本学期已确认正式资料元数据（不含正文）。",
        "inputSchema": {
            "type": "object",
            "properties": {"term_id": {"type": "integer"}},
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "sensitivity": "medium"},
    },
    {
        "name": "read_formal_material",
        "description": "分页读取单个已确认正式资料正文（仅 confirmed 内容）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "material_id": {"type": "integer"},
                "term_id": {"type": "integer"},
                "page": {"type": "integer", "description": "页码，从 1 开始"},
                "page_size": {"type": "integer", "description": "每页字符数，默认 4000"},
            },
            "required": ["material_id"],
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "sensitivity": "high"},
    },
    {
        "name": "get_evidence",
        "description": "展开某条证据及其计算来源（来源实体/字段/文件/页码/公式/分子分母）。",
        "inputSchema": {
            "type": "object",
            "properties": {"evidence_id": {"type": "string"}},
            "required": ["evidence_id"],
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "sensitivity": "medium"},
    },
]
