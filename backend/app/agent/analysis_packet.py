"""分析包预计算服务（RAG v3 · P1）

在模型会话创建之前，由服务端完成数据聚合与知识路由，生成 Compact Analysis
Packet。标准路径下模型不再调用任何数据工具，只依据分析包一轮生成报告并
调用 submit_report。

- 三个分析能力（exam_analysis / student_diagnosis / review_plan）各自组装；
- general_chat 明确不进入本模块，但可按需调用受限的安全只读工具；
- 每个诊断必须携带数据 evidence + （使用知识时）教学依据 evidence；
- 错因置信度执行 v3 证据纪律：笔试逐题数据（得分/对错/选项）只允许
  low 档候选，绝不因数据齐全而升档（升档需要作答过程或教师确认）。

Token 纪律：诊断最多 5 条、每条行动最多 2 条、包文本超过预算时先裁诊断条数。
"""

from __future__ import annotations

import logging
import json
from typing import Any

from .knowledge_compiler import load_index
from .registry.capabilities import (
    MANAGED_REQUIRED_TOOLS,
    MANAGED_OPTIONAL_TOOLS_BY_CAPABILITY,
    GENERAL_CHAT_MANAGED_READ_TOOLS,
)

logger = logging.getLogger(__name__)

ANALYSIS_CAPABILITIES = ("exam_analysis", "student_diagnosis", "review_plan")

# 分析包路径默认只开放报告提交；可选工具必须由服务端按需显式加入。
_PACKET_TOOL_POLICY = {
    capability: list(MANAGED_REQUIRED_TOOLS)
    for capability in ANALYSIS_CAPABILITIES
}

_MAX_DIAGNOSTICS = 5
_MAX_ACTIONS = 2
_PACKET_CHAR_BUDGET = 2600

_WEAK_POINT_RATE = 0.6
_WEAK_QUESTION_RATE = 0.5
_TARGET_RATE_CAP = 0.85
_SNIPPET_LIMIT = 800  # 试卷记忆注入 prompt 的截断上限


def tool_policy_for(
    capability: str,
    *,
    has_packet: bool,
    optional_tools: list[str] | None = None,
) -> list[str]:
    """生成该 run 的最小工具白名单。

    分析包路径默认只允许 ``submit_report``。可选工具必须由服务端明确
    传入，且只能来自统一能力契约；绝不再用 ``None`` 表示旧版全量工具。
    未绑定数据库考试的资料分析是唯一例外：它需要按需读取正式附件。
    """
    if capability == "general_chat":
        # 普通对话允许 AI 自主选择少量安全只读工具；是否调用由模型决定，
        # 服务器仍通过 scope 文件和 Bridge 白名单强制范围与只读约束。
        return list(GENERAL_CHAT_MANAGED_READ_TOOLS)
    if capability not in ANALYSIS_CAPABILITIES:
        return []
    if not has_packet:
        policy = list(MANAGED_REQUIRED_TOOLS)
        if capability == "exam_analysis":
            policy.append("get_formal_attachment")
    else:
        policy = list(_PACKET_TOOL_POLICY.get(capability, MANAGED_REQUIRED_TOOLS))
    allowed = set(MANAGED_OPTIONAL_TOOLS_BY_CAPABILITY.get(capability, ()))
    for tool in optional_tools or []:
        if tool in allowed and tool not in policy:
            policy.append(tool)
    return policy


def _set_ctx(db, scope):
    from .tools.tool_context import ToolContext, set_tool_context
    return set_tool_context(ToolContext(
        db_session=db,
        scope={k: v for k, v in scope.items() if v is not None},
    ))


def _call_tool(db, scope, handler, **kwargs) -> dict[str, Any]:
    token = _set_ctx(db, scope)
    try:
        return handler(**kwargs)
    finally:
        from .tools.tool_context import reset_tool_context
        reset_tool_context(token)


def _register_evidence(db, run_id, *, evidence_type, facts, source,
                       display_summary) -> str | None:
    if run_id is None:
        return None
    from .education_bridge.bridge_cli import _record_evidence
    return _record_evidence(db, run_id=run_id, evidence_type=evidence_type,
                            facts=facts, source_entity=source,
                            display_summary=display_summary)


def _entry_actions(index, names: list[str]) -> list[str]:
    """按名称/别名解析考点并取短行动；中文节点名经受控别名解析到考点 ID。"""
    out: list[str] = []
    for name in names:
        keys = [name]
        entry = index.lookup(name)
        if entry is not None:
            keys.append(entry.entry_id)
            if entry.tags:
                keys.append(entry.tags[0])
        for key in keys:
            for action in index.actions.get(key, []):
                if action not in out:
                    out.append(action)
    return out[:_MAX_ACTIONS]


def _build_exam_core(db, scope) -> dict[str, Any]:
    """考试分析 / 复习计划共用的数据核（统计 + 逐题 + 知识点覆盖）。"""
    from .tools.exam_tools import (
        _get_exam_statistics, _get_knowledge_coverage, _get_question_difficulty,
    )
    stats = _call_tool(db, scope, _get_exam_statistics)
    stats_data = stats.get("data") or {}
    difficulty = _call_tool(db, scope, _get_question_difficulty).get("data") or {}
    coverage = _call_tool(db, scope, _get_knowledge_coverage).get("data") or {}
    return {
        "stats": stats_data,
        "questions": difficulty.get("questions") or [],
        "paper_status": difficulty.get("paper_status"),
        "coverage": coverage,
    }


def _weak_points(coverage: dict[str, Any]) -> list[dict[str, Any]]:
    points = [p for p in coverage.get("knowledge_points", [])
              if p.get("avg_score_rate") is not None
              and p["avg_score_rate"] < _WEAK_POINT_RATE]
    return points[:3]


def _weak_questions(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    weak = [q for q in questions
            if q.get("score_rate") is not None and q["score_rate"] < _WEAK_QUESTION_RATE]
    weak.sort(key=lambda q: q["score_rate"])
    return weak[:3]


def _exam_metrics_text(stats: dict[str, Any]) -> str:
    return (f"参考 {stats.get('participant_count', 0)} 人，平均 "
            f"{stats.get('average_score', 0)}，及格率 {stats.get('pass_rate', 0)}，"
            f"优秀率 {stats.get('excellent_rate', 0)}")


def _build_exam_packet(db, scope, index) -> dict[str, Any] | None:
    core = _build_exam_core(db, scope)
    stats = core["stats"]
    if not stats or not stats.get("exam_id"):
        return None  # 无数据库考试 → 资料分析模式，回退旧路径
    index_dc = index
    diagnostics: list[dict[str, Any]] = []
    limitations: list[str] = []

    metrics_evidence = _register_evidence(
        db, scope.get("run_id"), evidence_type="computed_metric",
        facts=[{"type": "考试统计", "text": _exam_metrics_text(stats)}],
        source=f"exam:{stats.get('exam_id')}",
        display_summary=_exam_metrics_text(stats))

    coverage = core["coverage"]
    tagged = coverage.get("questions_tagged", 0)
    total = coverage.get("questions_total", 0)
    if total and tagged == 0:
        limitations.append("试卷结构未标注知识点，知识点维度不可用")
    if core.get("paper_status") == "draft":
        limitations.append("当前依据未确认的试卷草稿结构，结论以该草稿为准")
    if any("听力" in (q.get("question_type") or "")
           for q in core.get("questions", [])):
        limitations.append(
            "听力/人机对话按地区方案单独分析，本包仅提供跨地区通用诊断，"
            "不套用具体地市题型或评分规则")

    # 试卷记忆：教师确认的"这份卷子考什么"理解摘要（记忆板块）
    from ..services.paper_memory import get_confirmed_content
    memory = get_confirmed_content(db, stats.get("exam_id"))
    paper_memory = None
    if memory is not None:
        content, memory_version = memory
        evidence_id = _register_evidence(
            db, scope.get("run_id"), evidence_type="paper_memory",
            facts=[{"type": "试卷记忆",
                    "text": f"v{memory_version}：{content[:200]}"}],
            source=f"exam:{stats.get('exam_id')}:memory:v{memory_version}",
            display_summary=f"试卷记忆 v{memory_version}")
        paper_memory = {"content": content[:_SNIPPET_LIMIT],
                        "version": memory_version,
                        "evidence_id": evidence_id}
    else:
        limitations.append("尚未建立试卷记忆（可在试卷录入后生成并确认），"
                           "分析仅基于题目结构与逐题数据")

    # 诊断 1：薄弱知识点（分值加权得分率）
    for point in _weak_points(coverage):
        actions = _entry_actions(index_dc, [point["name"]])
        grounding = index_dc.lookup(point["name"])
        evidence_ids = []
        if metrics_evidence:
            evidence_ids.append(metrics_evidence)
        if grounding is not None:
            evidence_ids.append(_register_evidence(
                db, scope.get("run_id"), evidence_type="teaching_reference",
                facts=[{"type": "教学依据",
                        "text": f"[{grounding.entry_id}] {grounding.title}"}],
                source=f"entry:{grounding.entry_id}",
                display_summary=f"教学依据 {grounding.entry_id}"))
        signal = (f"知识点「{point['name']}」加权得分率 "
                  f"{point['avg_score_rate']:.2f}（{point['question_count']} 题 / "
                  f"{point['total_score']:g} 分）")
        diagnostics.append({
            "kind": "knowledge_point", "target": point["name"],
            "signal": signal, "confidence": "low",
            "actions": actions or ["按该知识点组织专项练习并安排阶段测评"],
            "evidence_ids": evidence_ids,
        })

    # 诊断 2：薄弱题目（逐题得分率 + 题型路由）
    routing = index_dc.routing
    for question in _weak_questions(core["questions"]):
        q_type = question.get("question_type") or ""
        route = routing.get(q_type) or {}
        cause_candidates = route.get("error_causes", [])
        actions = _entry_actions(index_dc, list(question.get("knowledge_nodes") or []))
        if not actions and q_type:
            actions = _entry_actions(index_dc, [q_type])
        evidence_ids = [metrics_evidence] if metrics_evidence else []
        route_entry = index_dc.lookup(route.get("entry_id", "")) if route else None
        if route_entry is not None:
            evidence_ids.append(_register_evidence(
                db, scope.get("run_id"), evidence_type="teaching_reference",
                facts=[{"type": "教学依据",
                        "text": f"[{route_entry.entry_id}] {route_entry.title}"}],
                source=f"entry:{route_entry.entry_id}",
                display_summary=f"教学依据 {route_entry.entry_id}"))
        wrong = "、".join(
            f"{opt['option']}({opt['count']}人)"
            for opt in question.get("wrong_options", [])[:2])
        signal = (f"第 {question.get('question_no')} 题得分率 "
                  f"{question['score_rate']:.2f}（满分 {question.get('max_score'):g}）"
                  + (f"，高频错误选项：{wrong}" if wrong else ""))
        diagnostics.append({
            "kind": "question", "target": f"第{question.get('question_no')}题",
            "signal": signal, "confidence": "low",
            "error_cause_candidates": cause_candidates,
            "actions": actions[:_MAX_ACTIONS] or ["结合错误选项讲评并布置同类变式题"],
            "evidence_ids": evidence_ids,
        })

    if not diagnostics:
        limitations.append("本次数据未发现低于阈值的薄弱点（或缺少逐题数据）")
    limitations.append(
        "错因为低置信度候选：缺少学生作答过程，置信度升档需要作答记录或教师确认")

    return {
        "capability": "exam_analysis",
        "scope": {k: scope.get(k) for k in ("exam_id", "class_id", "term_id")},
        "metrics": {
            "exam_name": stats.get("exam_name"),
            "participant_count": stats.get("participant_count"),
            "average_score": stats.get("average_score"),
            "pass_rate": stats.get("pass_rate"),
            "excellent_rate": stats.get("excellent_rate"),
        },
        "paper_memory": paper_memory,
        "diagnostics": diagnostics[:_MAX_DIAGNOSTICS],
        "limitations": limitations,
    }


def _build_student_packet(db, scope, index) -> dict[str, Any] | None:
    from sqlalchemy import select
    from ..models.entities import Exam, ExamScore
    from .tools.student_tools import _get_student_scores
    student_id = scope.get("student_id")
    if student_id is None:
        return None
    result = _call_tool(db, scope, _get_student_scores)
    data = result.get("data") or {}
    if not data or data.get("error"):
        return None
    diagnostics: list[dict[str, Any]] = []
    limitations: list[str] = []

    # 趋势（近 3 次有成绩考试）
    rows = db.execute(
        select(ExamScore.total_score, ExamScore.class_rank, Exam.name, Exam.exam_date)
        .join(Exam, Exam.id == ExamScore.exam_id)
        .where(ExamScore.student_id == student_id,
               ExamScore.total_score.is_not(None))
        .order_by(Exam.exam_date.desc(), Exam.id.desc()).limit(3)).all()
    trend = [{"exam": r.name, "total": r.total_score, "rank": r.class_rank}
             for r in reversed(rows)]
    trend_evidence = _register_evidence(
        db, scope.get("run_id"), evidence_type="computed_metric",
        facts=[{"type": "历史趋势",
                "text": "；".join(f"{t['exam']}：{t['total']}分"
                                  + (f"/班级第{t['rank']}" if t["rank"] else "")
                                  for t in trend)}],
        source=f"student:{student_id}",
        display_summary="学生历次成绩趋势") if len(trend) >= 2 else None

    items = [it for it in data.get("item_scores", [])
             if it.get("max_score") and it.get("score") is not None]
    weak_items = sorted(
        (it for it in items if it["score"] / it["max_score"] < _WEAK_QUESTION_RATE),
        key=lambda it: it["score"] / it["max_score"])[:3]

    item_evidence = _register_evidence(
        db, scope.get("run_id"), evidence_type="db_metric",
        facts=[{"type": "逐题成绩",
                "text": f"逐题 {len(items)} 项，薄弱 {len(weak_items)} 项"}],
        source=f"student:{student_id}:items",
        display_summary="学生逐题成绩") if items else None

    for item in weak_items:
        rate = item["score"] / item["max_score"]
        diagnostics.append({
            "kind": "question",
            "target": f"第{item.get('question_no')}题",
            "signal": (f"该生第 {item.get('question_no')} 题得分 "
                       f"{item['score']:g}/{item['max_score']:g}"
                       f"（得分率 {rate:.2f}）"),
            "confidence": "low",
            "actions": ["对照班级逐题表现判断是共性失分还是个体失分",
                        "按考点组织小剂量专项练习并近期复测"],
            "evidence_ids": [e for e in (item_evidence,) if e],
        })
    if trend_evidence:
        first, last = trend[0], trend[-1]
        direction = "下降" if last["total"] < first["total"] else "上升"
        diagnostics.insert(0, {
            "kind": "trend", "target": "历次成绩",
            "signal": (f"近 {len(trend)} 次成绩{direction}："
                       + "；".join(f"{t['exam']} {t['total']}分" for t in trend)),
            "confidence": "low", "actions": [],
            "evidence_ids": [trend_evidence],
        })
    limitations.append(
        "错因为低置信度候选：缺少作答过程；不因单次波动直接推断学习态度或习惯")
    if len(trend) < 2:
        limitations.append("历史考试不足 2 次，无法判断趋势")

    prior_profile = None
    growth_summary = None
    growth_profile = None
    growth_evidence = None
    term_id = scope.get("term_id")
    if isinstance(term_id, int) and term_id > 0:
        # 画像作为独立 JSON 档案注入分析包。模型读取它，但只需返回
        # profile_summary 这一段自然语言；原始字段仍在数据库中留档。
        from ..services.student_profiles import get_profile_payload
        payload = get_profile_payload(db, int(student_id), term_id)
        prior_profile = {
            "version": payload.get("version", 0),
            "profile": payload.get("profile") or {},
            "longitudinal_profile": payload.get("longitudinal_profile") or {},
        }
        # 成长事实由后端确定性输出，模型只能引用、不能改写（方案 §6.1/§6.2）。
        # 组装分析包时同时冻结事实指纹，供报告落库后判断画像是否过期。
        from ..services.growth import summary as growth_summary_service
        growth_summary = growth_summary_service.freeze_growth_reference(
            db, run_id=scope.get("run_id"), student_id=int(student_id), term_id=term_id)
        growth_profile = growth_summary_service.growth_profile_status(
            db, student_id=int(student_id), term_id=term_id,
            current_revision=growth_summary["source_revision"])
        growth_evidence = _register_evidence(
            db, scope.get("run_id"), evidence_type="growth_fact",
            facts=[{
                "type": "成长事实",
                "text": (f"学期营养 {growth_summary['term_points']}（阶段"
                         f"{growth_summary['stage']}），本周 {growth_summary['week_points']}；"
                         f"已记录完成任务 {growth_summary['observed_task_completion']['completed']} 次，"
                         f"经确认订正 {growth_summary['verified_corrections']} 次"),
            }],
            source=f"growth:{student_id}:{term_id}",
            display_summary="学生成长事实摘要")
        # 缺证据的成长分支以「局限」显式声明，模型据此写「暂不能判断」而不是
        # 把缺失当成薄弱；上限 4 条，避免挤占诊断条目的 prompt 预算。
        limitations.extend((growth_summary.get("limitations") or [])[:4])

    return {
        "capability": "student_diagnosis",
        "scope": {k: scope.get(k) for k in ("exam_id", "class_id",
                                            "student_id", "term_id")},
        "metrics": {
            "student": "该生",
            "total_score": data.get("total_score"),
            "class_rank": data.get("class_rank"),
            "item_count": len(items),
        },
        "diagnostics": diagnostics[:_MAX_DIAGNOSTICS],
        "limitations": limitations,
        "prior_profile": prior_profile,
        "growth_summary": growth_summary,
        "growth_profile": growth_profile,
        "growth_evidence_id": growth_evidence,
    }


def _build_review_packet(db, scope, index) -> dict[str, Any] | None:
    packet = _build_exam_packet(db, scope, index)
    if packet is None:
        return None
    priorities = []
    for diagnostic in packet.get("diagnostics", []):
        priorities.append({
            "target": diagnostic["target"],
            "signal": diagnostic["signal"],
            "actions": diagnostic.get("actions", []),
            "evidence_ids": diagnostic.get("evidence_ids", []),
        })
    if not priorities:
        priorities = [{
            "target": d["target"], "signal": d["signal"],
            "actions": d.get("actions", []),
            "evidence_ids": d.get("evidence_ids", []),
        } for d in packet.get("diagnostics", [])]
    priorities = priorities[:5]
    priorities.append({
        "target": "计划骨架",
        "signal": "按周组织：每周聚焦 2~3 个优先目标，安排阶段测评节点",
        "actions": ["参考七天复习计划模板（RV-模板-七天）安排周次"],
        "evidence_ids": [],
    })
    return {
        "capability": "review_plan",
        "scope": packet["scope"],
        "metrics": packet["metrics"],
        "paper_memory": packet.get("paper_memory"),
        "priorities": priorities,
        "limitations": packet["limitations"],
    }


def build_packet(db, capability: str, scope: dict[str, Any]) -> dict[str, Any] | None:
    """按能力组装分析包；非分析能力或数据不足时返回 None。

    这里的 ``None`` 只表示该能力没有可用数据（例如未绑定考试），不再
    吞掉预计算异常；异常必须由调用方将本次运行标记为失败。
    """
    if capability not in ANALYSIS_CAPABILITIES:
        return None  # general_chat 等一律不组装（5.4 生效边界）
    index = load_index()
    if capability == "exam_analysis":
        return _build_exam_packet(db, scope, index)
    if capability == "student_diagnosis":
        return _build_student_packet(db, scope, index)
    if capability == "review_plan":
        return _build_review_packet(db, scope, index)
    return None


def teaching_reference_usage(db, run_id: int | None) -> dict[str, int]:
    """深查用量统计（v3 观测项）：当前 run 的教学依据证据条数。"""
    if run_id is None:
        return {"teaching_reference_count": 0}
    from sqlalchemy import func, select
    from ..models.agent_entities import AnalysisEvidence
    count = db.scalar(select(func.count()).select_from(AnalysisEvidence).where(
        AnalysisEvidence.run_id == run_id,
        AnalysisEvidence.evidence_type == "teaching_reference",
    ))
    return {"teaching_reference_count": int(count or 0)}


def _diagnostic_line(idx: int, diagnostic: dict[str, Any]) -> str:
    """单条诊断的注入文本：信号 + 行动 + 错因候选 + 证据 ID。

    证据 ID 必须出现在文本中——标准路径要求模型引用"包中的 evidence ID"，
    模型唯一能看到的就是这份文本。
    """
    actions = "；".join(diagnostic.get("actions", [])[:_MAX_ACTIONS])
    causes = diagnostic.get("error_cause_candidates") or []
    cause_text = f"（优先错因：{'/'.join(causes)}）" if causes else ""
    evidence_ids = [e for e in (diagnostic.get("evidence_ids") or []) if e]
    evidence_text = f"［证据:{','.join(evidence_ids)}］" if evidence_ids else ""
    return (f"{idx}. {diagnostic.get('signal')} → {actions}{cause_text}"
            f"{evidence_text}")


def packet_to_text(packet: dict[str, Any]) -> str:
    """把分析包压缩为注入 prompt 的紧凑文本。

    预算裁剪：逐条装入诊断，超出预算从尾部丢弃**完整条目**（编号保持连续）；
    局限声明与试卷记忆始终保留（安全边界优先，见 v3 4.3）。
    """
    if not packet:
        return ""
    metrics = packet.get("metrics") or {}
    metric_items = "；".join(f"{k}={v}" for k, v in metrics.items() if v is not None)
    head = f"【分析包·{packet.get('capability')}】{metric_items}"
    diagnostics = packet.get("diagnostics") or []
    plan_note = None
    if packet.get("priorities"):
        diagnostics = packet["priorities"][:-1]
        plan_note = packet["priorities"][-1]

    # 安全边界与上下文记忆属于必留区，先计入总预算；诊断只能占用剩余空间。
    # 这样即使首条诊断异常冗长，也不会让最终注入文本无上限膨胀。
    suffix: list[str] = []
    prior_profile = packet.get("prior_profile")
    if prior_profile:
        # 保留完整 JSON 结构的同时限制 prompt 体积，摘要优先于旧的列表字段。
        profile_text = json.dumps(prior_profile, ensure_ascii=False, separators=(",", ":"))
        suffix.append(
            "【既有学生画像 JSON（独立档案，版本 "
            f"v{prior_profile.get('version', 0)}）】{profile_text[:1100]}"
        )
    if plan_note is not None:
        suffix.append(f"计划要求：{plan_note.get('signal')}")
    growth = packet.get("growth_summary")
    if growth:
        # 数值来自后端规则，模型只读：可以据此写叙述与建议，不能改积分、
        # 置信状态或教师原话（方案 §6.2）。limitations 不重复注入——它们已经
        # 以「局限」形式进入必留区，避免同一信息占两份 prompt 预算。
        prompt_growth = {
            "source_revision": growth["source_revision"],
            "rule_version": growth["rule_version"],
            "term_points": growth["term_points"],
            "stage": growth["stage"],
            "week_points": growth["week_points"],
            "observed_task_completion": growth["observed_task_completion"],
            "verified_corrections": growth["verified_corrections"],
            "dimensions": {key: {field: value.get(field) for field in (
                "status", "value", "observations", "trend", "latest_date")}
                for key, value in growth["dimensions"].items()},
            "evidence_refs": growth["evidence_refs"][:10],
        }
        growth_text = json.dumps(prompt_growth, ensure_ascii=False,
                                 separators=(",", ":"))
        evidence_id = packet.get("growth_evidence_id")
        evidence_text = f"［证据:{evidence_id}］" if evidence_id else ""
        suffix.append(
            "【成长事实 JSON（后端确定性输出，只读；不得改写其中的数值与置信状态）】"
            f"{growth_text}{evidence_text}"
        )
    growth_profile = packet.get("growth_profile") or {}
    if growth_profile.get("status") == "stale":
        suffix.append("既有画像的成长依据已更新，相关结论需按最新成长事实修订后再使用")
    memory = packet.get("paper_memory")
    if memory:
        evidence_id = memory.get("evidence_id")
        evidence_text = f"［证据:{evidence_id}］" if evidence_id else ""
        suffix.append(
            f"【试卷记忆 v{memory.get('version')}】"
            f"{memory.get('content', '')}{evidence_text}"
        )
    for limitation in packet.get("limitations", []):
        suffix.append(f"局限：{limitation}")

    kept: list[str] = []
    for diagnostic in diagnostics:
        candidate = kept + [_diagnostic_line(len(kept) + 1, diagnostic)]
        if len("\n".join([head, *candidate, *suffix])) > _PACKET_CHAR_BUDGET:
            break
        kept = candidate

    return "\n".join([head, *kept, *suffix])
