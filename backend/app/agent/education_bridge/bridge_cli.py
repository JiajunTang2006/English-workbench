"""Education Bridge CLI（B3-03）

Harness 插件通过 subprocess 调用本 CLI 访问 Workbench 教学数据（唯一入口）。

不可绕过约束：
1. **scope 只来自服务器注入的 scope 文件**；模型参数（--args）中不允许出现
   run_id/term_id/class_id/exam_id/student_id，发现即拒绝（fail-closed）；
2. **只读查询 + 白名单工具**：教学工具 + 每工具 schema 允许的参数键；
3. **输出脱敏**：学生姓名替换为按 student_id 稳定排序的匿名编号（student_01..），
   电话/学号/教师姓名一律隐藏；
4. **evidence 归属**：查询成功即生成 analysis_evidence（run_id=scope.run_id）
   并返回证据 ID；无 run_id 时允许返回事实但不落证据；
5. 跨学期/跨班级/跨学生：查询一律以 scope 内 id 为强制过滤条件，模型无路径提交其它 id。

用法:
    python bridge_cli.py --tool <name> --data-dir <dir> [--db <sqlite>]
        --scope <scope.json> --args <json>
stdout:
    {"ok": true, "data": {...}, "evidence_id": "...", "facts": [...], "notes": [...]}
    {"ok": false, "data": {}, "error": "..."}
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import secrets
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SCOPE_KEYS = ("run_id", "term_id", "class_id", "exam_id", "student_id")

# 白名单工具 + 模型允许提交的参数键（其余一律拒绝）
_TOOL_ARG_SCHEMA: dict[str, tuple[str, ...]] = {
    "get_exam_analysis_bundle": (),
    "get_exam_overview": (),
    "get_score_distribution": ("bin_size",),
    "get_question_list": ("question_type",),
    "get_student_trend": ("horizon",),
    "get_risk_signals": ("threshold",),
    "get_wrong_questions": ("top_n",),
    "get_student_scores": ("question_no",),
    "get_formal_attachment": ("title_keyword", "page", "offset", "limit"),
    "get_teaching_guidance": ("query",),
    "submit_report": ("findings", "recommendations", "limitations", "summary", "profile_summary"),
}

_ALLOWED_TOOLS = frozenset(_TOOL_ARG_SCHEMA)

# 事件/审计用工具标签（不含隐私）
TOOL_LABELS: dict[str, str] = {
    "get_exam_analysis_bundle": "成绩分析数据包",
    "get_exam_overview": "考试概览",
    "get_score_distribution": "分数分布",
    "get_question_list": "题目统计",
    "get_student_trend": "学生趋势",
    "get_risk_signals": "风险信号",
    "get_wrong_questions": "错题统计",
    "get_student_scores": "学生逐题成绩",
    "get_formal_attachment": "正式附件读取",
    "get_teaching_guidance": "教学依据精确查询",
    "submit_report": "报告提交",
}

# 证据类型白名单（写入 analysis_evidence）
_EVIDENCE_TYPES = ("db_metric", "computed_metric", "formal_document", "submission")


def _out(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, default=str))
    sys.exit(0)


def _die(msg: str) -> None:
    # 错误输出也必须满足插件 output.schema 的 required: ['ok', 'data']，
    # 否则 harness 会把真实错误掩盖成 "invalid output: missing required data"，
    # 模型无法获知失败原因并重试。data 用空对象占位，真实原因走 error 字段。
    _out({"ok": False, "data": {}, "error": msg})


def _ok(data: dict[str, Any], *, evidence_id: str | None = None,
        facts: list[dict[str, Any]] | None = None,
        notes: list[str] | None = None) -> None:
    _out({
        "ok": True, "data": data,
        "evidence_id": evidence_id,
        "facts": facts or [],
        "notes": notes or [],
    })


def _load_scope(path: str) -> dict[str, Any]:
    try:
        raw = Path(path).read_text(encoding="utf-8")
        data = json.loads(raw)
    except FileNotFoundError:
        _die(f"scope 文件缺失: {path}（服务器未注入本回合作用域）")
    except (OSError, ValueError) as exc:
        _die(f"scope 文件不可读: {exc}")
    if not isinstance(data, dict):
        _die("scope 文件格式错误")
    scope: dict[str, Any] = {}
    for key in _SCOPE_KEYS:
        val = data.get(key)
        scope[key] = val if (isinstance(val, int) and not isinstance(val, bool) and val > 0) else None
    # v3 路径：服务器按 run 预计算的能力与工具策略。即使读取到旧的
    # scope 文件，也采用最小安全策略，不能因缺字段而恢复全量工具。
    capability = data.get("capability")
    scope["capability"] = capability if isinstance(capability, str) and capability else None
    policy = data.get("tool_policy")
    if isinstance(policy, list):
        scope["tool_policy"] = [str(item) for item in policy]
    elif capability == "general_chat":
        # 普通聊天允许模型自主选择安全只读查询；不包含报告提交/画像写入。
        scope["tool_policy"] = [
            "get_exam_overview", "get_score_distribution",
            "get_question_list", "get_wrong_questions", "get_student_scores",
        ]
    elif capability in {"exam_analysis", "student_diagnosis", "review_plan"}:
        scope["tool_policy"] = (
            ["submit_report", "get_formal_attachment"]
            if capability == "exam_analysis" and scope.get("exam_id") is None
            else ["submit_report"]
        )
    else:
        scope["tool_policy"] = None
    return scope


def _reject_scope_keys(args: dict[str, Any]) -> None:
    for key in _SCOPE_KEYS:
        if key in args:
            _die(f"参数不允许携带 scope 字段: {key}")


def _validate_args(tool: str, args: dict[str, Any]) -> None:
    if not isinstance(args, dict):
        _die("args 必须是 JSON 对象")
    allowed = set(_TOOL_ARG_SCHEMA[tool])
    for key in args:
        if key not in allowed:
            _die(f"工具 {tool} 不允许参数: {key}（允许: {sorted(allowed)}）")


class _Anonymizer:
    """兼容旧工具签名的统一匿名器。

    旧实现按工具调用顺序生成 S1/S2，导致同一学生在不同工具结果中
    可能拥有不同编号。现在直接代理运行级 PrivacyMapper，统一使用
    student_01 等编号，并保留本地反向映射供执行器恢复展示。
    """

    def __init__(self, mapper) -> None:
        self._mapper = mapper

    def id_for(self, sid: int) -> str:
        return self._mapper.register_student(sid)

    def sanitize(self, obj: Any) -> Any:
        if isinstance(obj, list):
            return [self.sanitize(item) for item in obj]
        if not isinstance(obj, dict):
            return obj

        # 工具实现需要在生成 facts 前知道“是哪位匿名学生”。
        # 不能直接调用 sanitize_for_model，因为它会删除 name 字段，
        # 进而把风险信号变成“匿名编号: ?”。
        sid = obj.get("student_id")
        if not isinstance(sid, int):
            sid = obj.get("id") if isinstance(obj.get("id"), int) else None
        anonymous = self.id_for(sid) if sid is not None else None
        out: dict[str, Any] = {}
        for key, value in obj.items():
            key_lower = str(key).lower()
            if key_lower in {"name", "student_name"}:
                out[key] = anonymous or "[姓名已脱敏]"
            elif key_lower == "student_id":
                if anonymous:
                    out["anonymous_id"] = anonymous
            elif key_lower in {
                "id", "student_no", "phone", "parent_phone", "teacher_name",
                "tel", "id_card", "parent",
            }:
                continue
            elif isinstance(value, (dict, list)):
                out[key] = self.sanitize(value)
            else:
                out[key] = value
        return out

    @property
    def mapper(self):
        return self._mapper


def _open_db(data_dir: str, db: str | None):
    path = Path(db) if db else Path(data_dir) / "workbench.db"
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_engine(f"sqlite:///{path}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return factory(), engine


def _set_tool_ctx(db, scope: dict[str, int | None]) -> Any:
    """把 scope 注入 tools 模块使用的全局 ToolContext。"""
    from ..tools.tool_context import ToolContext, set_tool_context
    ctx = ToolContext(
        db_session=db,
        scope={k: v for k, v in scope.items() if v is not None},
    )
    return set_tool_context(ctx)


def _record_evidence(db, *, run_id: int | None, evidence_type: str,
                     facts: list[dict[str, Any]], source_entity: str | None,
                     display_summary: str) -> str | None:
    """生成 analysis_evidence 记录并返回 evidence_id；run_id 缺失时返回 None。"""
    if run_id is None:
        return None
    from ...models.agent_entities import AnalysisEvidence
    evidence_id = f"ev-{secrets.token_hex(8)}"
    db.add(AnalysisEvidence(
        evidence_id=evidence_id,
        run_id=run_id,
        evidence_type=evidence_type,
        local_fact_json={"facts": facts},
        source_entity=source_entity,
        display_summary=display_summary[:500],
        contains_personal_data=False,
    ))
    db.commit()
    return evidence_id


# ---------------------------------------------------------------------------
# 白名单工具实现（强制 scope 过滤 + 输出脱敏 + evidence 落库）
# ---------------------------------------------------------------------------

def _tool_exam_overview(db, scope, anon, args) -> dict[str, Any]:
    from ...agent.tools.exam_tools import _get_exam_statistics
    token = _set_tool_ctx(db, scope)
    try:
        data = _get_exam_statistics()
    finally:
        from ...agent.tools.tool_context import reset_tool_context
        reset_tool_context(token)
    if "error" in data:
        _die(data["error"])
    facts = [{
        "text": (f"考试 {d['exam_id']}（{d.get('exam_name') or ''}）"
                 f"参与 {d['participant_count']} 人，平均 {d['average_score']}，"
                 f"合格率 {d['pass_rate']}，优秀率 {d['excellent_rate']}"),
        "values": {k: d[k] for k in ("exam_id", "participant_count", "average_score",
                                     "pass_rate", "excellent_rate", "full_score")
                   if k in d},
    } for d in [data.get("data", {})] if d.get("exam_id") is not None]
    return {"data": data, "facts": facts, "etype": "db_metric",
            "source": f"exam:{scope.get('exam_id')}"}


def _tool_score_distribution(db, scope, anon, args) -> dict[str, Any]:
    from ...agent.tools.exam_tools import _get_score_distribution
    token = _set_tool_ctx(db, scope)
    try:
        data = _get_score_distribution(bin_size=int(args.get("bin_size", 10)))
    finally:
        from ...agent.tools.tool_context import reset_tool_context
        reset_tool_context(token)
    if "error" in data:
        _die(data["error"])
    dist = data.get("data", {}).get("distribution", [])
    facts = [{
        "type": "分数段分布",
        "text": "；".join(f"{r['range']} 分: {r['count']}人" for r in dist[:12]),
    }]
    return {"data": data, "facts": facts, "evidence_type": "db_metric",
            "source": f"exam:{scope.get('exam_id')}"}


def _tool_student_scores(db, scope, anon, args) -> dict[str, Any]:
    """Read only the student selected by the server-owned run scope."""
    exam_id, student_id = scope.get("exam_id"), scope.get("student_id")
    if exam_id is None or student_id is None:
        _die("请先在当前会话选择考试和学生，才能查询逐题成绩")
    from ...services.student_score_details import get_student_score_details
    try:
        details = get_student_score_details(
            db, exam_id=exam_id, student_id=student_id,
            class_id=scope.get("class_id"),
        )
    except LookupError as exc:
        _die(str(exc))
    question_no = args.get("question_no")
    if question_no is not None:
        if not isinstance(question_no, (str, int)) or len(str(question_no)) > 20:
            _die("question_no 必须是 20 字以内的题号")
        details["item_scores"] = [
            item for item in details["item_scores"]
            if str(item["question_no"]) == str(question_no)
        ]
    facts = [{
        "type": "逐题成绩",
        "text": (f"当前学生在考试 {exam_id} 的逐题成绩："
                 f"已录入 {details['scored_items']}/{details['expected_items']} 项；"
                 f"本次返回 {len(details['item_scores'])} 项"),
    }]
    facts.extend({
        "type": "逐题成绩",
        "text": (f"第{item['question_no']}"
                 + (f"-{item['sub_question_no']}" if item['sub_question_no'] else "")
                 + (f"题得分 {item['score']:g}/{item['max_score']:g}"
                    if item["score"] is not None else "题尚未录入得分")),
    } for item in details["item_scores"])
    return {"data": {"data": details}, "facts": facts,
            "evidence_type": "db_metric",
            "source": f"student:{student_id}:exam:{exam_id}:items"}


def _tool_question_list(db, scope, anon, args) -> dict[str, Any]:
    from ...agent.tools.exam_tools import _get_question_list
    token = _set_tool_ctx(db, scope)
    try:
        data = _get_question_list(question_type=args.get("question_type"))
    finally:
        from ...agent.tools.tool_context import reset_tool_context
        reset_tool_context(token)
    if "error" in data:
        _die(data["error"])
    questions = data.get("data", {}).get("questions", [])
    facts = [{
        "type": "题目统计",
        "text": f"共 {len(questions)} 题" + (f"（题型: {args.get('question_type')}）"
                                            if args.get("question_type") else ""),
    }]
    return {"data": data, "facts": facts, "evidence_type": "db_metric",
            "source": f"exam:{scope.get('exam_id')}"}


def _tool_student_trend(db, scope, anon, args) -> dict[str, Any]:
    """学生趋势：最近 N 场考试（scope 内该班级/学生）平均分与及格率。

    只输出匿名编号（无姓名/学号）；student_id scope 存在时输出该生分数序列。
    """
    from sqlalchemy import select
    from ...models.entities import Exam, ExamScore
    horizon = max(1, min(int(args.get("horizon", 3)), 10))
    session_fact = None
    class_id = scope.get("class_id")
    student_id = scope.get("student_id")

    exams = db.execute(
        select(Exam).where(
            Exam.term_id == scope.get("term_id"),
            Exam.status == "active",          # 已归档/删除的考试不进趋势
            Exam.archived_at.is_(None),       # 双重保险：归档状态（若保留数据）不参与
        )
        .order_by(Exam.exam_date.desc()).limit(horizon)
    ).scalars().all()
    exams = list(reversed(exams))  # 旧→新
    series = []
    for exam in exams:
        q = select(db_model("ExamScore").total_score).where(
            db_model("ExamScore").exam_id == exam.id,
            db_model("ExamScore").total_score.is_not(None),
        )
        if class_id is not None:
            q = q.where(db_model("ExamScore").class_id_at_exam == class_id)
        if student_id is not None:
            q = q.where(db_model("ExamScore").student_id == student_id)
        scores = [r[0] for r in db.execute(q).all()]
        if not scores:
            series.append({"exam_id": exam.id, "exam_name": exam.name,
                           "participant_count": 0})
            continue
        series.append({
            "exam_id": exam.id,
            "exam_name": exam.name,
            "participant_count": len(scores),
            "average_score": round(sum(scores) / len(scores), 2),
        })
    data = {
        "data": {
            "term_id": scope.get("term_id"),
            "class_id": class_id,
            "student_id": student_id,
            "horizon": horizon,
            "exam_count": len(series),
            "series": series,
        }
    }
    facts = [{
        "type": "学生趋势",
        "text": "近 %d 场考试：%s" % (
            len(series),
            "，".join(f"{s['exam_name']} 平均 {s['average_score']}"
                      for s in series if "average_score" in s) or "无有效数据"),
    }]
    return {"data": data, "facts": facts, "evidence_type": "computed_metric",
            "source": f"exam:{scope.get('exam_id')}"}


def db_model(name: str):
    """按名返回 SQLAlchemy 模型（延迟导入避免模块级循环）。"""
    from ...models import entities
    return getattr(entities, name)


def _tool_risk_signals(db, scope, anon, args) -> dict[str, Any]:
    from ...agent.tools.risk_tools import _get_at_risk_students
    token = _set_tool_ctx(db, scope)
    try:
        data = _get_at_risk_students(threshold=float(args.get("threshold") or 60))
    finally:
        from ...agent.tools.tool_context import reset_tool_context
        reset_tool_context(token)
    if "error" in data:
        _die(data["error"])
    cleaned = anon.sanitize(data)
    students = cleaned.get("data", {}).get("at_risk_students", [])
    facts = [{
        "type": "风险信号",
        "text": f"{len(students)} 名学生低于风险线（匿名编号: "
                + "、".join(s.get("name", "?") for s in students[:10]) + "）",
    }]
    return {"data": cleaned, "facts": facts, "evidence_type": "db_metric",
            "source": f"exam:{scope.get('exam_id')}"}


def _tool_wrong_questions(db, scope, anon, args) -> dict[str, Any]:
    """错题统计：知识点覆盖（无逐题得分时给出覆盖与说明，禁止编造）。"""
    from ...agent.tools.exam_tools import _get_knowledge_coverage
    token = _set_tool_ctx(db, scope)
    try:
        data = _get_knowledge_coverage()
    finally:
        from ...agent.tools.tool_context import reset_tool_context
        reset_tool_context(token)
    if "error" in data:
        _die(data["error"])
    kps = data.get("data", {}).get("knowledge_points", [])
    facts = [{
        "type": "错题统计",
        "text": f"覆盖 {len(kps)} 个知识点；逐题得分未录入前无法推断错题率（不编造）",
        "values": {"knowledge_point_count": len(kps)},
    }]
    return {"data": data, "facts": facts, "evidence_type": "db_metric",
            "source": f"exam:{scope.get('exam_id')}"}


def _session_id_for_run(db, scope) -> int | None:
    run_id = scope.get("run_id")
    if run_id is None:
        return None
    from ...models.agent_entities import AnalysisRun
    run = db.get(AnalysisRun, run_id)
    return run.session_id if run is not None else None


def _compact_bundle_value(value: Any) -> Any:
    """限制聚合工具结果体积，避免“减少调用次数却放大单次输入”。"""
    if isinstance(value, dict):
        return {str(k): _compact_bundle_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_compact_bundle_value(v) for v in value[:60]]
    if isinstance(value, str) and len(value) > 2_000:
        return value[:2_000] + "…[数据包字段已裁剪]"
    return value


def _tool_exam_analysis_bundle(db, scope, anon, args) -> dict[str, Any]:
    """一次返回成绩分析所需的核心事实，避免模型逐工具、逐页往返。"""
    if scope.get("exam_id") is not None:
        components: dict[str, Any] = {}
        facts: list[dict[str, Any]] = []
        calls = (
            ("overview", _tool_exam_overview, {}),
            ("score_distribution", _tool_score_distribution, {"bin_size": 10}),
            ("questions", _tool_question_list, {}),
            ("trend", _tool_student_trend, {"horizon": 3}),
            ("risks", _tool_risk_signals, {"threshold": 60}),
            ("wrong_questions", _tool_wrong_questions, {"top_n": 10}),
        )
        for key, fn, call_args in calls:
            result = fn(db, scope, anon, call_args)
            payload = result.get("data") or {}
            components[key] = _compact_bundle_value(payload.get("data", payload))
            facts.extend(result.get("facts") or [])
        components["usage_note"] = (
            "核心统计已一次性聚合。除非某个明确字段缺失，不要再重复调用单项统计工具。"
        )
        return {
            "data": {"data": components},
            "facts": facts,
            "evidence_type": "computed_metric",
            "source": f"exam:{scope.get('exam_id')}",
        }

    # 未绑定数据库考试时，只读取当前会话中教师已确认的正式附件。
    session_id = _session_id_for_run(db, scope)
    if session_id is None:
        _die("当前运行未绑定会话，无法读取正式成绩资料")
    from ...services.agent_analysis.formal_context import FormalContextProvider
    db_path = Path(str(db.get_bind().url.database or ""))
    provider = FormalContextProvider(db, data_dir=db_path.parent)
    attachments = provider.list_formal_attachments(session_id)
    context = provider.get_formal_context_text(
        session_id, max_chars=12_000, max_tokens=3_000, max_attachments=5,
    )
    if not attachments or not context:
        _die("当前会话没有可读的教师已确认成绩资料")
    facts = [
        {
            "type": "正式附件",
            "text": f"已读取当前会话正式附件《{item['title']}》（用途: {item['purpose']}）",
        }
        for item in attachments[:5]
    ]
    return {
        "data": {"data": {
            "attachments": attachments[:5],
            "formal_context": context,
            "usage_note": (
                "内容已按输入 Token 预算抽取。优先据此完成报告；只有缺少明确页码细节时，"
                "才可调用 get_formal_attachment，且最多两次。"
            ),
        }},
        "facts": facts,
        "evidence_type": "formal_document",
        "source": f"session:{session_id}",
    }


def _tool_formal_attachment(db, scope, anon, args) -> dict[str, Any]:
    from ...services.agent_analysis.formal_context import (
        FormalContextProvider, smart_truncate_parsed,
    )
    from ...agent.token_budget import truncate_text_to_tokens
    from ...models.entities import Attachment
    from ...models.agent_entities import AnalysisEvidence
    from sqlalchemy import func, select

    run_id = scope.get("run_id")
    session_id = _session_id_for_run(db, scope)
    if session_id is None:
        _die("当前运行未绑定会话，无法读取正式附件")
    # 聚合数据包之后只允许少量定向补读，阻止 0/1500/3000... 的全文扫描。
    if run_id is not None:
        reads = db.scalar(
            select(func.count(AnalysisEvidence.id)).where(
                AnalysisEvidence.run_id == run_id,
                AnalysisEvidence.source_entity.like("attachment:%"),
            )
        ) or 0
        if reads >= 2:
            _die("本回合正式附件定向补读已达 2 次上限，请基于数据包完成分析并说明局限")

    db_path = Path(str(db.get_bind().url.database or ""))
    provider = FormalContextProvider(db, data_dir=db_path.parent)
    attachments = provider.list_formal_attachments(session_id)
    hit = None
    for meta in attachments:
        title = meta.get("title") or meta.get("original_name") or ""
        if args.get("title_keyword") and args["title_keyword"] not in title:
            continue
        att = db.get(Attachment, meta["attachment_id"])
        if att is None:
            continue
        hit = {"attachment_id": att.id, "title": title,
               "parsed": (att.metadata_json or {}).get("parsed") or {}}
        break
    if hit is None:
        _die("无可读正式附件（未确认/跨学期/名称不匹配）")

    # 读取模式：
    # 1) page 指定 → 返回 PDF 该页文本（1 基）；
    # 2) offset/limit → 正文分段读取；
    # 3) 省略参数 → 智能摘要（PDF 首页/表格页/末页 + 省略页标注；其余前 N 字符 + 标注）。
    parsed = hit["parsed"]
    page_no = args.get("page")
    offset = max(0, int(args.get("offset") or 0))
    limit = max(32, min(int(args.get("limit") or 3000), 6000))
    fmt = parsed.get("format") or ""
    content = ""
    read_note = ""
    pages = parsed.get("pages") if isinstance(parsed.get("pages"), list) else []

    if page_no is not None and fmt == "pdf" and pages:
        try:
            idx = int(page_no) - 1
        except (TypeError, ValueError):
            idx = -1
        if 0 <= idx < len(pages):
            content = (pages[idx].get("text") or "").strip()[: max(32, int(limit))]
            read_note = f"（第 {int(page_no)} 页）"
        else:
            _die(f"页码越界：共 {len(pages)} 页，请求第 {int(page_no)} 页")
    elif "offset" in args or "limit" in args:
        full = (parsed.get("content") or "").strip()
        content = full[offset: offset + limit]
        read_note = f"（正文第 {offset + 1}~{offset + len(content)} 字符）"
    else:
        content, read_note = smart_truncate_parsed(parsed, max_chars=6000)
        content = truncate_text_to_tokens(content, 3000)

    data = {"data": {"attachment": anon.sanitize({
        "attachment_id": hit["attachment_id"],
        "title": hit["title"],
        "content": content,
        "note": read_note,
    })}}
    facts = [{"type": "正式附件",
              "text": f"已读取正式附件 《{hit['title']}》{read_note}（本次返回 {len(content)} 字）"}]
    return {"data": data, "facts": facts, "evidence_type": "formal_document",
            "source": f"attachment:{hit['attachment_id']}"}


def _tool_submit_report(db, scope, anon, args) -> dict[str, Any]:
    """报告提交：校验证据引用（存在 + 归属当前 run），失败即拒绝。

    校验通过后将结构化报告（summary/findings/recommendations/limitations）
    持久化到 analysis_runs.input_summary_json.structured_answer 与对应
    assistant 消息的 structured_answer_json，供自研前端报告画布渲染。
    """
    from ...agent.evidence import validate_report_evidence_references
    findings = args.get("findings") or []
    recommendations = args.get("recommendations") or []
    limitations = args.get("limitations") or []
    summary = args.get("summary") or ""
    profile_summary = args.get("profile_summary") or ""
    run_id = scope.get("run_id")
    if run_id is None:
        _die("submit_report 需要 run_id scope（教学回话之外不可用）")
    errors = validate_report_evidence_references(
        findings, recommendations, ledger=None, db_session=db, run_id=run_id,
    )
    if errors:
        _die("报告证据引用校验失败：" + "；".join(errors))

    # U3-03：结构化报告持久化（自研前端报告画布/证据检查器消费）
    try:
        from ...models.agent_entities import AnalysisRun, AgentMessage
        run = db.get(AnalysisRun, run_id)
        if run is None:
            raise RuntimeError(f"run {run_id} 不存在，无法持久化报告")
        from ...agent.evidence import normalize_evidence_refs
        evidence_ids: list[str] = []
        for finding in findings:
            for evidence_id in normalize_evidence_refs(finding.get("evidence_ids") or []):
                if evidence_id not in evidence_ids:
                    evidence_ids.append(evidence_id)
        for recommendation in recommendations:
            for evidence_id in normalize_evidence_refs(
                recommendation.get("supports") or recommendation.get("evidence_ids") or []
            ):
                if evidence_id not in evidence_ids:
                    evidence_ids.append(evidence_id)

        payload = {
            "summary": summary,
            "profile_summary": profile_summary,
            "findings": findings,
            "recommendations": recommendations,
            "limitations": limitations,
            "evidence_ids": evidence_ids,
            "answer_type": run.capability or "exam_analysis",
            "schema_version": "1.0.0",
        }
        # 新版分析包路径只开放 submit_report；学生诊断报告在受理后由服务端
        # 自动同步为正式画像，确保学生管理与聊天报告使用同一条数据链路。
        if run.capability in {"student_diagnosis", "exam_analysis"} and run.student_id:
            from ...services.student_profiles import apply_analysis_report_to_profile
            apply_analysis_report_to_profile(db, run=run, report=payload, confirmed_by="ai")
        summary_json = dict(run.input_summary_json or {})
        summary_json["structured_answer"] = payload
        run.input_summary_json = summary_json
        # 在正式报告首次提交时冻结数据范围。后续成绩修订不会改变这份历史报告
        # 对应的参与人数、缺考人数和统计口径。
        from ...services.agent_analysis.report_snapshot import ensure_report_scope_snapshot
        ensure_report_scope_snapshot(db, run)
        msg = (
            db.query(AgentMessage)
            .filter(AgentMessage.analysis_run_id == run_id)
            .filter(AgentMessage.role == "assistant")
            .order_by(AgentMessage.id.desc())
            .first()
        )
        if msg is not None:
            msg.structured_answer_json = payload
            msg.evidence_ids_json = evidence_ids
        db.commit()
    except Exception as exc:  # 报告落库失败阻断受理，让引擎侧可见具体错误
        logger.warning("submit_report 持久化失败 run=%s: %s", run_id, exc)
        _die(f"报告持久化失败: {exc}")

    facts = [{
        "type": "报告提交",
        "text": f"报告已受理（findings={len(findings)}, "
                f"recommendations={len(recommendations)}）",
    }]
    return {"data": {"status": "accepted",
                     "findings": len(findings),
                     "recommendations": len(recommendations)},
            "facts": facts, "evidence_type": "submission",
            "source": f"run:{run_id}"}


def _tool_teaching_guidance(db, scope, anon, args) -> dict[str, Any]:
    """教学依据精确查询（v3 深查通道）。

    仅做规范 ID / 受控别名的精确匹配，返回单条短片段（上限 600 字），
    并以 teaching_reference 类型落 evidence。不做开放模糊检索。
    """
    query = str(args.get("query") or "").strip()
    if not query:
        _die("缺少 query（考点 ID / 条目 ID / 标签）")
    capability = scope.get("capability")
    if capability == "general_chat":
        _die("普通聊天不开放教学工具")
    # 硬性次数门（与 get_formal_attachment 的两次上限一致）：只统计本工具的
    # 成功调用（记在 run.packet_stats.guidance_calls），分析包预登记的
    # teaching_reference 证据不占额度。
    max_calls = 2
    run_id = scope.get("run_id")
    used = 0
    run = None
    if run_id is not None:
        from ...models.agent_entities import AnalysisRun
        run = db.get(AnalysisRun, run_id)
        stats = dict((run.input_summary_json or {}).get("packet_stats") or {}) \
            if run is not None else {}
        used = int(stats.get("guidance_calls") or 0)
        if used >= max_calls:
            _die(f"教学依据深查已达上限（{max_calls} 次）；"
                 "请基于已获取的依据完成报告")
    from ..knowledge_compiler import load_index
    entry = load_index().lookup(query)
    if entry is None:
        _die(f"知识库未命中: {query}（请在报告 limitations 中说明缺少教学依据）")
    if run is not None:
        summary_json = dict(run.input_summary_json or {})
        stats = dict(summary_json.get("packet_stats") or {})
        stats["guidance_calls"] = used + 1
        summary_json["packet_stats"] = stats
        run.input_summary_json = summary_json
        db.commit()
    facts = [{
        "type": "教学依据",
        "text": f"[{entry.entry_id}] {entry.title}（可信度 {entry.credibility}，"
                f"来源：{entry.source}）",
    }]
    return {"data": {"entry_id": entry.entry_id, "title": entry.title,
                     "domain": entry.domain, "level": entry.level,
                     "credibility": entry.credibility, "source": entry.source,
                     "snippet": entry.snippet(600)},
            "facts": facts, "evidence_type": "teaching_reference",
            "source": f"entry:{entry.entry_id}"}


_TOOL_IMPL = {
    "get_exam_analysis_bundle": _tool_exam_analysis_bundle,
    "get_exam_overview": _tool_exam_overview,
    "get_score_distribution": _tool_score_distribution,
    "get_question_list": _tool_question_list,
    "get_student_trend": _tool_student_trend,
    "get_risk_signals": _tool_risk_signals,
    "get_wrong_questions": _tool_wrong_questions,
    "get_student_scores": _tool_student_scores,
    "get_formal_attachment": _tool_formal_attachment,
    "get_teaching_guidance": _tool_teaching_guidance,
    "submit_report": _tool_submit_report,
}


def run_tool(tool: str, db, scope: dict[str, int | None], args: dict[str, Any]
             ) -> dict[str, Any]:
    """执行白名单工具；返回 {data, facts, evidence_type, source, anon}（未落证据）。"""
    from ...agent.privacy import PrivacyMapper
    from ...services.agent_analysis.identity_dict import (
        build_run_identity_dictionary,
        register_identity_into_mapper,
    )

    impl = _TOOL_IMPL.get(tool)
    if impl is None:
        _die(f"不支持的工具: {tool}")
    mapper = PrivacyMapper(allow_student_names=True)
    identity = build_run_identity_dictionary(
        db,
        term_id=scope.get("term_id"),
        class_id=scope.get("class_id"),
        student_id=scope.get("student_id"),
    )
    register_identity_into_mapper(mapper, identity)
    anon = _Anonymizer(mapper)
    result = impl(db, scope, anon, args)
    result["anon"] = anon
    result["privacy_mapper"] = mapper
    return result


def _sanitize_for_provider(db, scope, value: Any, privacy_mapper=None) -> Any:
    """同时脱敏结构化字段和附件自由文本，返回可发送给模型的副本。"""
    from ...agent.privacy import PrivacyMapper
    from ...services.agent_analysis.identity_dict import (
        build_run_identity_dictionary,
        register_identity_into_mapper,
    )

    mapper = privacy_mapper
    if mapper is None:
        mapper = PrivacyMapper(allow_student_names=True)
        identity = build_run_identity_dictionary(
            db,
            term_id=scope.get("term_id"),
            class_id=scope.get("class_id"),
            student_id=scope.get("student_id"),
        )
        register_identity_into_mapper(mapper, identity)

    def sanitize_document_text(text: str) -> str:
        """补足未入学生库的附件姓名：按表头/显式标签做格式化脱敏。"""
        lines = text.splitlines()
        sensitive_columns: dict[int, str] = {}
        row_counter = 0
        output: list[str] = []
        for line in lines:
            cells = line.split("\t")
            header_columns = {
                idx: ("name" if cell.strip() in {"姓名", "学生姓名", "Name"}
                      else "id")
                for idx, cell in enumerate(cells)
                if cell.strip() in {
                    "姓名", "学生姓名", "Name", "学号", "考号", "准考证号",
                    "手机号", "电话", "家长电话",
                }
            }
            if header_columns:
                sensitive_columns = header_columns
            elif sensitive_columns and len(cells) > max(sensitive_columns):
                row_counter += 1
                for idx, kind in sensitive_columns.items():
                    if cells[idx].strip():
                        cells[idx] = (
                            f"student_file_{row_counter:03d}"
                            if kind == "name" else "[身份编号已脱敏]"
                        )
                line = "\t".join(cells)
            output.append(line)
        cleaned = "\n".join(output)
        cleaned = re.sub(
            r"((?:学生)?姓名\s*[:：]\s*)([\u3400-\u9fff·]{2,12})",
            r"\1[姓名已脱敏]", cleaned,
        )
        return cleaned

    def sanitize_texts(item: Any) -> Any:
        if isinstance(item, str):
            return mapper.sanitize_text(sanitize_document_text(item))
        if isinstance(item, list):
            return [sanitize_texts(child) for child in item]
        if isinstance(item, dict):
            return {key: sanitize_texts(child) for key, child in item.items()}
        return item

    return mapper.sanitize_for_model(sanitize_texts(value))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="TeachMate Education Bridge CLI")
    parser.add_argument("--tool", required=True, choices=sorted(_ALLOWED_TOOLS))
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--db", default=None, help="测试用 SQLite 路径覆盖")
    parser.add_argument("--scope", required=True, help="服务器注入的 scope JSON 文件")
    parser.add_argument("--args", default="{}")
    opts = parser.parse_args(argv)

    tool = opts.tool
    try:
        args = json.loads(opts.args)
    except ValueError as exc:
        _die(f"args 不是合法 JSON: {exc}")
    _reject_scope_keys(args)
    _validate_args(tool, args)

    scope = _load_scope(opts.scope)
    run_id: int | None = scope.get("run_id")

    # v3 生效边界：策略门在工具实现之前，未列入策略的工具一律拒绝。
    # scope 缺少 tool_policy 时，_load_scope 已按能力注入最小安全策略；
    # 未知能力才保留 None 并交给后续能力校验。
    capability = scope.get("capability")
    policy = scope.get("tool_policy")
    # 显式空策略仍表示调用方要求本轮完全禁用工具；默认生成的
    # general_chat 策略是安全只读白名单，不会走这里。
    if capability == "general_chat" and policy == []:
        _die("普通聊天当前运行未开放教学工具")
    if policy is not None and tool not in policy:
        _die(f"当前运行的工具策略不允许调用 {tool}（允许: {sorted(policy)}）；"
             "标准路径请直接依据分析包调用 submit_report")

    db, engine = _open_db(opts.data_dir, opts.db)
    try:
        result = run_tool(tool, db, scope, args)
        data = result["data"]
        facts = result["facts"]
        # 先做工具级匿名编号，再用完整运行身份词典清理自由文本、学号、电话、
        # 本地 ID 与路径。附件正文不能只依赖字段名脱敏。
        privacy_mapper = result.get("privacy_mapper")
        sanitized_data = _sanitize_for_provider(
            db, scope, result["anon"].sanitize(data), privacy_mapper,
        )
        sanitized_facts = _sanitize_for_provider(
            db, scope, facts, privacy_mapper,
        )

        evidence_id = _record_evidence(
            db, run_id=run_id,
            evidence_type=result.get("evidence_type", "db_metric"),
            facts=facts,
            source_entity=result.get("source"),
            display_summary=facts[0]["text"] if facts else tool,
        )
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 —— CLI 边界：任何异常转失败输出
        logger.exception("bridge_cli 异常")
        _die(f"内部错误: {type(exc).__name__}")
    finally:
        db.close()
        engine.dispose()

    _ok(sanitized_data, evidence_id=evidence_id, facts=sanitized_facts,
        notes=["结果已按身份词典匿名化"] if facts else [])


if __name__ == "__main__":
    main()
