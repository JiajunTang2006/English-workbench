"""Codex 插件 API 路由（TeachMatePluginAPI v1，L2）。

只读、可撤销、可审计的适配层：
- 工具端点要求插件令牌（teaching.read scope）+ codex_plugin_enabled 开关；
- FastAPI 侧再次校验学期/班级/考试/学生归属，防止越权与跨学期泄漏；
- 绝不返回 ORM 对象、绝对路径、API Key、Token 摘要、原始哈希或任何未确认正文；
- 错误码统一放 detail.code：plugin_disabled / unauthenticated / forbidden_scope /
  not_found / api_version_unsupported / data_incomplete。
"""
from __future__ import annotations

import hashlib
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, func, and_, or_

from ..agent.config import get_agent_config
from ..agent.privacy import PrivacyMapper
from ..auth import require_token
from ..models.agent_entities import (
    AgentMessage, AnalysisRun, ErrorCauseAssessment, ExamPaperVersion,
    ExamQuestion, KnowledgePoint, StudentItemResult,
)
from ..models.entities import (
    Class, Enrollment, Exam, ExamClassMetric, ExamDimensionScore, ExamScore,
    ScoreDimension, Student, Term,
)
from ..schemas.plugin_api import (
    PLUGIN_API_VERSION,
    ExamSnapshot, ExamSnapshotClassMetric, ExamSnapshotSummary,
    AnalysisReportScope, AnalysisReportRun, AnalysisReportView,
    AnalysisReportCatalog, AnalysisReportListItem,
    EvidenceSource, EvidenceCalculation, EvidenceView,
    FormalMaterialMeta, FormalMaterialPage, PluginStatus, PluginTokenIssued,
    PluginTokenMeta, PluginConnectConfig, ReviewPlanFacts, DimensionAverage, StudentExamPointLite,
    StudentProfileView, StudentSearchItem, StudentSearchResponse,
    StudentPracticeContext, PracticeWrongItem, PracticeErrorCause,
    PracticeKnowledgePoint, PracticeErrorPattern,
    TeachingScopes, TermLite, ClassLite, ExamLite,
)
from ..services.agent_analysis.evidence import EvidenceService
from ..services.agent_analysis.formal_context import FormalContextProvider
from ..services.exams import get_exam, list_exams, exam_summary
from ..services.plugin_auth import PLUGIN_SCOPE, PluginTokenRecord, PluginTokenStore
from ..services.profiles import student_profile
from ..services.student_profiles import get_profile_payload
from ..services.terms import current_term_id, require_term
from ..services.agent_analysis.identity_dict import (
    build_run_identity_dictionary, register_identity_into_mapper,
)
from ..services.agent_analysis.report_snapshot import read_report_scope_snapshot
from ..version import APP_VERSION, SCHEMA_REVISION

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/plugin")

TOOL_NAMES = [
    "get_teachmate_status", "list_teaching_scopes", "get_exam_snapshot",
    "list_analysis_reports", "get_latest_analysis_report",
    "get_student_learning_profile", "get_review_plan_facts",
    "search_students", "get_student_practice_context",
    "list_formal_materials", "read_formal_material", "get_evidence",
]


def _snapshot_scope(snapshot: dict, *, run: AnalysisRun, term: Term | None,
                    exam: Exam, classroom: Class | None) -> AnalysisReportScope:
    frozen = snapshot.get("scope") if isinstance(snapshot, dict) else {}
    frozen = frozen if isinstance(frozen, dict) else {}
    return AnalysisReportScope(
        term_id=int(frozen.get("term_id") or run.term_id),
        term_name=str(frozen.get("term_name") or (term.name if term else run.term_id)),
        class_id=frozen.get("class_id") if "class_id" in frozen else run.class_id,
        class_name=frozen.get("class_name") if "class_name" in frozen else (
            classroom.name if classroom else None
        ),
        exam_id=int(frozen.get("exam_id") or exam.id),
        exam_name=str(frozen.get("exam_name") or exam.name),
        full_score=float(frozen.get("full_score") or exam.full_score),
    )


def _run_view(run: AnalysisRun) -> AnalysisReportRun:
    return AnalysisReportRun(
        run_id=run.id, capability=run.capability, status=run.status,
        started_at=run.started_at, completed_at=run.completed_at,
        runtime_kind=run.runtime_kind, runtime_version=run.runtime_version,
        rules_version=run.rules_version, prompt_version=run.prompt_version,
    )


# --- 依赖 ---
def _store(request: Request) -> PluginTokenStore:
    return PluginTokenStore(request.app.state.settings.data_dir / "plugin")


def _session(request: Request):
    return request.app.state.session_factory()


def require_plugin_enabled(request: Request) -> None:
    enabled = get_agent_config().feature_flags.get("codex_plugin_enabled", False)
    if not enabled:
        raise HTTPException(status_code=503, detail={"code": "plugin_disabled",
                                                     "message": "Codex 插件未启用（设置 codex_plugin_enabled=true）"})


def negotiate_api_version(api_version: str = Query(default=PLUGIN_API_VERSION)) -> None:
    if api_version != PLUGIN_API_VERSION:
        raise HTTPException(status_code=400, detail={"code": "api_version_unsupported",
                                                     "message": f"仅支持 api_version={PLUGIN_API_VERSION}"})


def require_plugin_token(authorization: str | None = Header(default=None),
                         request: Request = None) -> PluginTokenRecord:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={"code": "unauthenticated",
                                                     "message": "缺少插件令牌"})
    raw = authorization[len("Bearer "):]
    store = _store(request)
    rec = store.verify_token(raw)
    if rec is None:
        raise HTTPException(status_code=401, detail={"code": "unauthenticated",
                                                     "message": "插件令牌无效、已过期或已撤销"})
    if rec.scope != PLUGIN_SCOPE:
        raise HTTPException(status_code=403, detail={"code": "forbidden_scope",
                                                     "message": "令牌 scope 不足"})
    store.record_use(rec.id)
    return rec


def _require_class(session, class_id: int, term_id: int) -> Class:
    """确认班级属于所选学期，防止只靠一个 class_id 跨学期取数。"""
    classroom = session.scalar(select(Class).where(
        Class.id == class_id, Class.term_id == term_id,
    ))
    if classroom is None:
        raise HTTPException(status_code=404, detail={
            "code": "not_found", "message": "班级不存在或不属于所选学期",
        })
    return classroom


def _scoped_exam_scores(session, exam_id: int, class_id: int | None = None):
    stmt = select(ExamScore).where(ExamScore.exam_id == exam_id)
    if class_id is not None:
        stmt = stmt.where(ExamScore.class_id_at_exam == class_id)
    return session.scalars(stmt).all()


def _score_summary_from_rows(exam: Exam, rows) -> dict:
    present = [r for r in rows if r.attendance_status == "present"]
    scored = [float(r.total_score) for r in present if r.total_score is not None]
    return {
        "exam_id": exam.id,
        "exam_name": exam.name,
        "full_score": exam.full_score,
        "present_count": len(present),
        "scored_count": len(scored),
        "missing_scores": max(0, len(present) - len(scored)),
        "absent_count": len(rows) - len(present),
        "average": round(sum(scored) / len(scored), 2) if scored else None,
        "highest": max(scored) if scored else None,
        "lowest": min(scored) if scored else None,
    }


def _sanitize_report_for_plugin(report: dict, session, *, term_id: int,
                                class_id: int | None, exam_id: int,
                                identify: bool) -> dict:
    """按插件出站边界处理报告文本；默认只返回匿名学生标识。"""
    mapper = PrivacyMapper(allow_student_names=identify)
    identity = build_run_identity_dictionary(
        session, term_id=term_id, class_id=class_id,
    )
    register_identity_into_mapper(mapper, identity)

    # 分析运行可能早于/脱离 Enrollment 写入。再从本场考试成绩表补齐学生词典，
    # 确保报告里出现的每个姓名都能在默认匿名模式下被处理。
    score_students_stmt = (
        select(Student.id, Student.name, Student.student_no, Student.parent_phone)
        .join(ExamScore, ExamScore.student_id == Student.id)
        .where(ExamScore.exam_id == exam_id)
    )
    if class_id is not None:
        score_students_stmt = score_students_stmt.where(ExamScore.class_id_at_exam == class_id)
    for sid, name, student_no, phone in session.execute(score_students_stmt).all():
        if sid is not None:
            mapper.register_student(sid)
        if name:
            mapper.register_name(name, student_id=sid)
        mapper.register_protected_terms([value for value in (student_no, phone) if value])

    # 报告中的本地学生 ID 不属于 WorkBuddy 生成文档所需内容，即使 identify=true 也不外发。
    blocked_keys = {
        "student_id", "student_no", "parent_phone", "phone", "seat",
        "attachment_id", "storage_name", "url",
    }

    def walk(value):
        if isinstance(value, str):
            return value if identify else mapper.sanitize_text(value)
        if isinstance(value, list):
            return [walk(item) for item in value]
        if not isinstance(value, dict):
            return value
        output = {}
        for key, item in value.items():
            if key.lower() in blocked_keys:
                continue
            # 识别模式关闭时，name 也不能作为未处理的独立字段外发。
            if not identify and key.lower() in {"name", "student_name"}:
                if isinstance(item, str):
                    output[key] = mapper.sanitize_text(item)
                continue
            output[key] = walk(item)
        return output

    return walk(report)


def _report_from_run(session, run: AnalysisRun) -> tuple[dict, list[str]] | None:
    """优先读取最终 assistant 消息，兼容仅写入 run 摘要的历史运行。"""
    message = session.scalar(
        select(AgentMessage).where(
            AgentMessage.analysis_run_id == run.id,
            AgentMessage.role == "assistant",
        ).order_by(AgentMessage.created_at.desc(), AgentMessage.id.desc()).limit(1)
    )
    report = message.structured_answer_json if message else None
    evidence_ids = list(message.evidence_ids_json or []) if message else []
    if not isinstance(report, dict) or not report:
        summary = run.input_summary_json or {}
        report = summary.get("structured_answer") if isinstance(summary, dict) else None
    if not isinstance(report, dict) or not report:
        return None
    if isinstance(report.get("structured_answer"), dict) and not report.get("summary"):
        report = report["structured_answer"]
    for evidence_id in report.get("evidence_ids", []) if isinstance(report.get("evidence_ids"), list) else []:
        if isinstance(evidence_id, str) and evidence_id not in evidence_ids:
            evidence_ids.append(evidence_id)
    # 兼容早期报告：当时只有嵌套 finding/supports，没有写入顶层 evidence_ids。
    for finding in report.get("findings", []) if isinstance(report.get("findings"), list) else []:
        for evidence_id in finding.get("evidence_ids", []) if isinstance(finding, dict) and isinstance(finding.get("evidence_ids"), list) else []:
            if isinstance(evidence_id, str) and evidence_id not in evidence_ids:
                evidence_ids.append(evidence_id)
    for recommendation in report.get("recommendations", []) if isinstance(report.get("recommendations"), list) else []:
        refs = recommendation.get("supports", []) if isinstance(recommendation, dict) else []
        if isinstance(refs, list):
            for evidence_id in refs:
                if isinstance(evidence_id, str) and evidence_id not in evidence_ids:
                    evidence_ids.append(evidence_id)
    return report, evidence_ids


# --- 认证（教师令牌）+ 配对/撤销 ---
class PairRequest(BaseModel):
    code: str = Field(min_length=1, max_length=64)


@router.post("/auth/pair", response_model=PluginTokenIssued, status_code=201,
             dependencies=[Depends(require_token)])
def pair_plugin(payload: PairRequest, request: Request):
    store = _store(request)
    issued = store.exchange_pairing_code(payload.code)
    if issued is None:
        raise HTTPException(status_code=400, detail={"code": "invalid_pairing_code",
                                                     "message": "配对码无效或已过期"})
    return PluginTokenIssued(
        token=issued["token"], scope=issued["scope"],
        issued_at=issued["issued_at"], expires_at=issued["expires_at"],
    )


@router.post("/auth/connect", response_model=PluginConnectConfig, status_code=201,
             dependencies=[Depends(require_token)])
def connect_workbuddy(request: Request):
    """从已登录的 TeachMate 页面直接生成 WorkBuddy 配置。"""
    # 教师主动点击“连接 WorkBuddy”即视为明确启用意图，避免再要求单独设置
    # 环境变量；状态会持久化，后续 WorkBuddy 直接可用。
    from dataclasses import replace
    from ..agent.config import set_runtime_config

    current_config = get_agent_config()
    if not current_config.feature_flags.get("codex_plugin_enabled", False):
        flags = dict(current_config.feature_flags)
        flags["codex_plugin_enabled"] = True
        set_runtime_config(replace(
            current_config,
            config_version=current_config.config_version + 1,
            feature_flags=flags,
        ))
    issued = _store(request).issue_access_token()
    project_root = Path(__file__).resolve().parents[3]
    plugin_root = project_root / "plugins" / "teachmate"
    config = {
        "mcpServers": {
            "teachmate": {
                "type": "stdio",
                "command": sys.executable,
                "args": ["-m", "server"],
                "cwd": str(plugin_root),
                "env": {
                    "PYTHONPATH": str(plugin_root),
                    "TEACHMATE_PLUGIN_BASE_URL": str(request.base_url).rstrip("/"),
                    "TEACHMATE_PLUGIN_TOKEN": issued["token"],
                },
            }
        }
    }
    return PluginConnectConfig(
        token=issued["token"], scope=issued["scope"], config=config,
        issued_at=issued["issued_at"], expires_at=issued["expires_at"],
    )


@router.post("/auth/revoke", dependencies=[Depends(require_token)])
def revoke_plugin(payload: dict, request: Request):
    token_id = (payload or {}).get("token_id")
    if not token_id:
        raise HTTPException(status_code=422, detail={"code": "invalid_input", "message": "缺少 token_id"})
    ok = _store(request).revoke_token(token_id)
    if not ok:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "令牌不存在"})
    return {"ok": True}


@router.get("/auth/tokens", response_model=list[PluginTokenMeta],
            dependencies=[Depends(require_token)])
def list_plugin_tokens(request: Request):
    return [PluginTokenMeta(**m) for m in _store(request).list_tokens()]


# --- 状态（工具）---
@router.get("/status", response_model=PluginStatus,
            dependencies=[Depends(require_plugin_enabled), Depends(require_plugin_token)])
def plugin_status(request: Request):
    return PluginStatus(
        app_version=APP_VERSION, schema_revision=SCHEMA_REVISION,
        plugin_enabled=get_agent_config().feature_flags.get("codex_plugin_enabled", False),
        capabilities=TOOL_NAMES,
    )


# --- 工具：教学作用域 ---
@router.get("/scopes", response_model=TeachingScopes,
            dependencies=[Depends(require_plugin_token), Depends(require_plugin_enabled),
                          Depends(negotiate_api_version)])
def list_teaching_scopes(request: Request, term_id: int | None = Query(default=None, gt=0)):
    with _session(request) as session:
        tid = term_id or current_term_id(session)
        require_term(session, tid)
        terms = session.scalars(select(Term).order_by(Term.id)).all()
        classes = session.scalars(select(Class).where(Class.term_id == tid,
                                                      Class.status == "active").order_by(Class.name)).all()
        exams = list_exams(session, term_id=tid)
        return TeachingScopes(
            term_id=tid,
            terms=[TermLite(id=t.id, name=t.name, status=t.status) for t in terms],
            classes=[ClassLite(id=c.id, name=c.name, status=c.status) for c in classes],
            exams=[ExamLite(id=e.id, name=e.name,
                            exam_date=e.exam_date.isoformat() if e.exam_date else None,
                            full_score=e.full_score, exam_type=e.exam_type, status=e.status)
                   for e in exams],
        )


# --- 工具：考试快照 ---
@router.get("/exams/{exam_id}/snapshot", response_model=ExamSnapshot,
            dependencies=[Depends(require_plugin_token), Depends(require_plugin_enabled),
                          Depends(negotiate_api_version)])
def get_exam_snapshot(exam_id: int, request: Request,
                      term_id: int | None = Query(default=None, gt=0),
                      class_id: int | None = Query(default=None, gt=0)):
    with _session(request) as session:
        tid = term_id or current_term_id(session)
        exam = get_exam(session, exam_id, term_id=tid)  # 非本学期 → 404
        classroom = _require_class(session, class_id, tid) if class_id is not None else None
        summary = (_score_summary_from_rows(exam, _scoped_exam_scores(session, exam_id, class_id))
                   if class_id is not None else exam_summary(session, exam_id, term_id=tid))
        metric_stmt = (select(ExamClassMetric).join(Class, ExamClassMetric.class_id == Class.id)
                       .where(ExamClassMetric.exam_id == exam_id))
        if class_id is not None:
            metric_stmt = metric_stmt.where(ExamClassMetric.class_id == class_id)
        metric_rows = session.scalars(metric_stmt).all()
        class_metrics = [
            ExamSnapshotClassMetric(class_id=m.class_id, class_name=m.classroom.name,
                                   grade_rank=m.grade_rank)
            for m in metric_rows
        ]
        # 缺考/未填分数统计
        scoped_rows = (_scoped_exam_scores(session, exam_id, class_id)
                       if class_id is not None else _scoped_exam_scores(session, exam_id))
        rows = [r for r in scoped_rows
                if r.attendance_status == "present" and r.total_score is None]
        scored_count = sum(
            1 for row in scoped_rows
            if row.attendance_status == "present" and row.total_score is not None
        )
        data_quality = {
            "present_count": summary["present_count"] if isinstance(summary, dict) else summary.present_count,
            "scored_count": scored_count,
            "absent_count": summary["absent_count"] if isinstance(summary, dict) else summary.absent_count,
            "missing_scores": len(rows),
        }
        return ExamSnapshot(
            term_id=tid,
            class_id=class_id,
            class_name=classroom.name if classroom else None,
            exam=ExamSnapshotSummary(
                exam_id=summary["exam_id"] if isinstance(summary, dict) else summary.exam_id,
                exam_name=summary["exam_name"] if isinstance(summary, dict) else summary.exam_name,
                full_score=summary["full_score"] if isinstance(summary, dict) else summary.full_score,
                present_count=summary["present_count"] if isinstance(summary, dict) else summary.present_count,
                absent_count=summary["absent_count"] if isinstance(summary, dict) else summary.absent_count,
                average=summary["average"] if isinstance(summary, dict) else summary.average,
                highest=summary["highest"] if isinstance(summary, dict) else summary.highest,
                lowest=summary["lowest"] if isinstance(summary, dict) else summary.lowest,
            ),
            class_metrics=class_metrics, data_quality=data_quality,
        )


# --- 工具：读取已生成的结构化分析报告 ---
@router.get("/analysis-reports", response_model=AnalysisReportCatalog,
            dependencies=[Depends(require_plugin_token), Depends(require_plugin_enabled),
                          Depends(negotiate_api_version)])
def list_analysis_reports(
    request: Request,
    term_id: int | None = Query(default=None, gt=0),
    class_id: int | None = Query(default=None, gt=0),
    exam_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=50, ge=1, le=100),
):
    """列出已经真实生成的报告，供 WorkBuddy 先发现再读取。"""
    with _session(request) as session:
        tid = term_id or current_term_id(session)
        term = require_term(session, tid)
        if class_id is not None:
            _require_class(session, class_id, tid)
        if exam_id is not None:
            get_exam(session, exam_id, term_id=tid)

        stmt = select(AnalysisRun).where(
            AnalysisRun.capability == "exam_analysis",
            AnalysisRun.term_id == tid,
            AnalysisRun.status.in_(["completed", "degraded"]),
            AnalysisRun.exam_id.is_not(None),
        )
        if class_id is not None:
            stmt = stmt.where(AnalysisRun.class_id == class_id)
        if exam_id is not None:
            stmt = stmt.where(AnalysisRun.exam_id == exam_id)
        stmt = stmt.order_by(
            AnalysisRun.completed_at.desc(), AnalysisRun.created_at.desc(), AnalysisRun.id.desc()
        )

        items: list[AnalysisReportListItem] = []
        for run in session.scalars(stmt).all():
            candidate = _report_from_run(session, run)
            if candidate is None:
                continue
            report, _evidence_ids = candidate
            exam = session.get(Exam, run.exam_id)
            if exam is None:
                continue
            classroom = session.get(Class, run.class_id) if run.class_id is not None else None
            snapshot, source = read_report_scope_snapshot(session, run)
            findings = report.get("findings") if isinstance(report.get("findings"), list) else []
            recommendations = (
                report.get("recommendations")
                if isinstance(report.get("recommendations"), list) else []
            )
            items.append(AnalysisReportListItem(
                scope=_snapshot_scope(
                    snapshot, run=run, term=term, exam=exam, classroom=classroom,
                ),
                run=_run_view(run),
                finding_count=len(findings),
                recommendation_count=len(recommendations),
                data_quality=(snapshot.get("data_quality") or {}),
                snapshot_captured_at=snapshot.get("captured_at"),
                snapshot_source=source,
            ))

        return AnalysisReportCatalog(
            term_id=tid, total=len(items), reports=items[:limit],
        )


@router.get("/analysis-reports/latest", response_model=AnalysisReportView,
            dependencies=[Depends(require_plugin_token), Depends(require_plugin_enabled),
                          Depends(negotiate_api_version)])
def get_latest_analysis_report(
    request: Request,
    exam_id: int = Query(gt=0),
    term_id: int | None = Query(default=None, gt=0),
    class_id: int | None = Query(default=None, gt=0),
    identify: bool = Query(default=False),
    run_id: int | None = Query(default=None, gt=0),
):
    """返回 TeachMate 已完成的最新分析，不重新调用模型、不混用其他班级。"""
    with _session(request) as session:
        tid = term_id or current_term_id(session)
        require_term(session, tid)
        exam = get_exam(session, exam_id, term_id=tid)
        classroom = _require_class(session, class_id, tid) if class_id is not None else None

        stmt = select(AnalysisRun).where(
            AnalysisRun.capability == "exam_analysis",
            AnalysisRun.term_id == tid,
            AnalysisRun.exam_id == exam_id,
            AnalysisRun.status.in_(["completed", "degraded"]),
        )
        # class_id=null 表示 TeachMate 的全体班级分析；不会退化成“任意班级”。
        if class_id is None:
            stmt = stmt.where(AnalysisRun.class_id.is_(None))
        else:
            stmt = stmt.where(AnalysisRun.class_id == class_id)
        if run_id is not None:
            stmt = stmt.where(AnalysisRun.id == run_id)
        stmt = stmt.order_by(
            AnalysisRun.completed_at.desc(), AnalysisRun.created_at.desc(), AnalysisRun.id.desc()
        )

        selected = None
        for run in session.scalars(stmt).all():
            candidate = _report_from_run(session, run)
            if candidate is not None:
                selected = (run, candidate[0], candidate[1])
                break
        if selected is None:
            raise HTTPException(status_code=404, detail={
                "code": "data_incomplete",
                "message": "所选学期、班级和考试没有已生成的结构化分析报告",
            })

        run, report, evidence_ids = selected
        snapshot, snapshot_source = read_report_scope_snapshot(session, run)
        public_report = _sanitize_report_for_plugin(
            report, session, term_id=tid, class_id=class_id, exam_id=exam_id,
            identify=identify,
        )
        term = session.get(Term, tid)
        return AnalysisReportView(
            scope=_snapshot_scope(
                snapshot, run=run, term=term, exam=exam, classroom=classroom,
            ),
            run=_run_view(run),
            report=public_report,
            evidence_ids=evidence_ids,
            data_quality=snapshot.get("data_quality") or {},
            scope_snapshot=snapshot,
            snapshot_source=snapshot_source,
            identifiable=identify,
        )


# --- 工具：学生检索与个性化训练上下文 ---
def _student_anon_id(student: Student | dict, term_id: int) -> str:
    student_no = student.get("student_no", "") if isinstance(student, dict) else student.student_no
    return hashlib.sha256(f"{student_no}:{term_id}".encode()).hexdigest()[:16]


@router.get("/students/search", response_model=StudentSearchResponse,
            dependencies=[Depends(require_plugin_token), Depends(require_plugin_enabled),
                          Depends(negotiate_api_version)])
def search_students(request: Request,
                    term_id: int | None = Query(default=None, gt=0),
                    class_id: int | None = Query(default=None, gt=0),
                    keyword: str | None = Query(default=None, max_length=100),
                    identify: bool = Query(default=False),
                    limit: int = Query(default=50, ge=1, le=100)):
    """在当前学期检索学生，默认只返回稳定匿名标识。"""
    with _session(request) as session:
        tid = term_id or current_term_id(session)
        require_term(session, tid)
        if class_id is not None:
            _require_class(session, class_id, tid)
        stmt = (
            select(Student, Enrollment, Class)
            .join(Enrollment, Enrollment.student_id == Student.id)
            .join(Class, Class.id == Enrollment.class_id)
            .where(
                Enrollment.term_id == tid,
                Enrollment.status == "active",
                Student.status == "active",
            )
            .order_by(Student.name, Student.id)
        )
        if class_id is not None:
            stmt = stmt.where(Enrollment.class_id == class_id)
        query = (keyword or "").strip()
        if query:
            stmt = stmt.where(or_(Student.name.ilike(f"%{query}%"),
                                  Student.student_no.ilike(f"%{query}%")))
        total = session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
        rows = session.execute(stmt.limit(limit)).all()
        return StudentSearchResponse(
            term_id=tid,
            total=int(total),
            identifiable=identify,
            students=[StudentSearchItem(
                student_id=student.id,
                student_anon_id=_student_anon_id(student, tid),
                class_id=enrollment.class_id,
                class_name=classroom.name,
                display_name=student.name if identify else None,
            ) for student, enrollment, classroom in rows],
        )


@router.get("/students/{student_id}/practice-context", response_model=StudentPracticeContext,
            dependencies=[Depends(require_plugin_token), Depends(require_plugin_enabled),
                          Depends(negotiate_api_version)])
def get_student_practice_context(
    student_id: int,
    request: Request,
    term_id: int | None = Query(default=None, gt=0),
    exam_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=20, ge=1, le=50),
):
    """返回生成个性化练习所需的画像、薄弱点、错因和真实错题上下文。"""
    with _session(request) as session:
        tid = term_id or current_term_id(session)
        require_term(session, tid)
        profile = student_profile(session, student_id, term_id=tid)
        if exam_id is not None:
            get_exam(session, exam_id, term_id=tid)

        item_stmt = (
            select(StudentItemResult, ExamQuestion, Exam)
            .join(Exam, Exam.id == StudentItemResult.exam_id)
            .join(ExamQuestion, ExamQuestion.id == StudentItemResult.question_id)
            .join(ExamPaperVersion, ExamPaperVersion.id == ExamQuestion.paper_version_id)
            .where(
                StudentItemResult.student_id == student_id,
                StudentItemResult.attendance_status == "present",
                Exam.term_id == tid,
                Exam.status == "active",
                ExamPaperVersion.exam_id == Exam.id,
                ExamPaperVersion.status.in_(["confirmed", "superseded"]),
                or_(
                    StudentItemResult.correct.is_(False),
                    and_(StudentItemResult.score.is_not(None),
                         StudentItemResult.score < ExamQuestion.max_score),
                ),
            )
            .order_by(Exam.exam_date.desc(), Exam.id.desc(), ExamQuestion.question_no)
        )
        if exam_id is not None:
            item_stmt = item_stmt.where(StudentItemResult.exam_id == exam_id)
        all_rows = session.execute(item_stmt).all()
        # Keep the predicate deterministic even for legacy rows with inconsistent flags.
        wrong_rows = [
            row for row in all_rows
            if row[0].correct is False
            or (row[0].score is not None and row[0].score < row[1].max_score)
        ]
        total_wrong = len(wrong_rows)
        rows = wrong_rows[:limit]
        keys = [(item.exam_id, item.question_id) for item, _question, _exam in rows]
        causes_by_key: dict[tuple[int, int], list[ErrorCauseAssessment]] = {}
        knowledge_point_names: dict[int, str] = {}
        if keys:
            exam_ids = {key[0] for key in keys}
            question_ids = {key[1] for key in keys}
            causes = session.scalars(select(ErrorCauseAssessment).where(
                ErrorCauseAssessment.student_id == student_id,
                ErrorCauseAssessment.exam_id.in_(exam_ids),
                ErrorCauseAssessment.question_id.in_(question_ids),
                ErrorCauseAssessment.status != "rejected",
            )).all()
            cause_point_ids = {cause.knowledge_point_id for cause in causes if cause.knowledge_point_id}
            if cause_point_ids:
                knowledge_point_names = {
                    point.id: point.name
                    for point in session.scalars(select(KnowledgePoint).where(KnowledgePoint.id.in_(cause_point_ids)))
                }
            for cause in causes:
                key = (cause.exam_id, cause.question_id)
                if key in keys:
                    causes_by_key.setdefault(key, []).append(cause)

        profile_payload = get_profile_payload(session, student_id, tid)
        wrong_items: list[PracticeWrongItem] = []
        point_rates: dict[str, list[float]] = {}
        cause_counts: dict[str, list[int]] = {}
        for item, question, exam in rows:
            score_rate = item.score_rate
            if score_rate is None and item.score is not None and question.max_score:
                score_rate = round(float(item.score) / float(question.max_score), 4)
            points = [str(value).strip() for value in (question.knowledge_nodes_json or []) if str(value).strip()]
            pitfall_tags = [str(value).strip() for value in (item.pitfall_tags_json or question.pitfall_tags_json or []) if str(value).strip()]
            error_causes = []
            for cause in causes_by_key.get((item.exam_id, item.question_id), []):
                error_causes.append(PracticeErrorCause(
                    cause=cause.cause,
                    status=cause.status,
                    confidence=cause.confidence,
                    knowledge_point=knowledge_point_names.get(cause.knowledge_point_id),
                ))
                counts = cause_counts.setdefault(cause.cause, [0, 0])
                counts[0] += 1
                if cause.status == "confirmed":
                    counts[1] += 1
            for point in points:
                point_rates.setdefault(point, []).append(score_rate if score_rate is not None else 0.0)
            wrong_items.append(PracticeWrongItem(
                item_id=item.id,
                exam_id=exam.id,
                exam_name=exam.name,
                question_id=question.id,
                question_no=question.question_no,
                question_type=question.question_type,
                content_text=question.content_text,
                options=question.options_json or {},
                correct_answer=question.correct_answer_json,
                student_answer=item.student_answer_text or item.selected_option,
                score=item.score,
                max_score=question.max_score,
                score_rate=score_rate,
                knowledge_points=points,
                pitfall_tags=pitfall_tags,
                error_causes=error_causes,
            ))

        return StudentPracticeContext(
            term_id=tid,
            class_id=profile.student.get("class_id"),
            class_name=profile.student.get("class_name"),
            student_anon_id=_student_anon_id(profile.student, tid),
            profile_version=profile_payload["version"],
            confirmed_profile=profile_payload["profile"],
            longitudinal_profile=profile_payload["longitudinal_profile"],
            weak_knowledge_points=[PracticeKnowledgePoint(
                name=name,
                wrong_count=len(rates),
                average_score_rate=round(sum(rates) / len(rates), 4) if rates else None,
            ) for name, rates in sorted(point_rates.items(), key=lambda pair: (sum(pair[1]) / len(pair[1]), pair[0]))],
            error_patterns=[PracticeErrorPattern(
                cause=cause, count=counts[0], confirmed_count=counts[1],
            ) for cause, counts in sorted(cause_counts.items(), key=lambda pair: (-pair[1][0], pair[0]))],
            wrong_items=wrong_items,
            total_wrong_items=total_wrong,
        )


# --- 工具：匿名学生档案 ---
@router.get("/students/{student_id}/profile", response_model=StudentProfileView,
            dependencies=[Depends(require_plugin_token), Depends(require_plugin_enabled),
                          Depends(negotiate_api_version)])
def get_student_profile(student_id: int, request: Request,
                        term_id: int | None = Query(default=None, gt=0),
                        identify: bool = Query(default=False)):
    with _session(request) as session:
        tid = term_id or current_term_id(session)
        require_term(session, tid)
        profile = student_profile(session, student_id, term_id=tid)  # 无活跃报名 → 404
        student = profile.student
        anon = _student_anon_id(student, tid)
        exams = [
            StudentExamPointLite(
                exam_id=p.exam_id, exam_name=p.exam_name,
                exam_date=p.exam_date.isoformat() if p.exam_date else None,
                full_score=p.full_score, total_score=p.total_score,
                score_rate=p.score_rate, attendance_status=p.attendance_status, tier=p.tier,
            )
            for p in profile.exams
        ]
        latest = profile.latest_score
        latest_lite = StudentExamPointLite(
            exam_id=latest.exam_id, exam_name=latest.exam_name,
            exam_date=latest.exam_date.isoformat() if latest.exam_date else None,
            full_score=latest.full_score, total_score=latest.total_score,
            score_rate=latest.score_rate, attendance_status=latest.attendance_status,
            tier=latest.tier,
        ) if latest else None
        weak_tags = [t.strip() for t in (student.get("weak_tags") or "").replace(";", ",").split(",")
                     if t.strip()]
        return StudentProfileView(
            student_anon_id=anon, term_id=tid,
            class_id=student.get("class_id"), class_name=student.get("class_name"),
            target_score=student.get("target_score"),
            weak_tags=weak_tags,
            identifiable=identify,
            exams=exams, latest_score=latest_lite,
            learning_profile=profile.learning_profile,
            learning_profile_version=(profile.learning_profile_meta or {}).get("version", 0),
        )


# --- 工具：复习计划事实 ---
@router.get("/review-plans/facts", response_model=ReviewPlanFacts,
            dependencies=[Depends(require_plugin_token), Depends(require_plugin_enabled),
                          Depends(negotiate_api_version)])
def get_review_plan_facts(request: Request, exam_id: int = Query(gt=0),
                          term_id: int | None = Query(default=None, gt=0),
                          class_id: int | None = Query(default=None, gt=0)):
    with _session(request) as session:
        tid = term_id or current_term_id(session)
        get_exam(session, exam_id, term_id=tid)  # 越学期 → 404
        if class_id is not None:
            _require_class(session, class_id, tid)
        dims = session.scalars(select(ScoreDimension).where(ScoreDimension.exam_id == exam_id)).all()
        dim_meta = {d.id: (d.name, d.max_score) for d in dims}
        stmt = select(ExamDimensionScore).join(ExamScore, ExamDimensionScore.exam_score_id == ExamScore.id) \
            .where(ExamScore.exam_id == exam_id)
        if class_id is not None:
            stmt = stmt.where(ExamScore.class_id_at_exam == class_id)
        dscores = session.scalars(stmt).all()
        acc: dict[int, list[float]] = {}
        for ds in dscores:
            meta = dim_meta.get(ds.dimension_id)
            if not meta or not meta[1]:
                continue
            acc.setdefault(ds.dimension_id, []).append(ds.score / meta[1])
        dim_avgs: list[DimensionAverage] = []
        common_errors: list[str] = []
        for did, (name, _max) in dim_meta.items():
            rates = acc.get(did)
            if not rates:
                continue
            avg = round(sum(rates) / len(rates) * 100, 2)
            dim_avgs.append(DimensionAverage(dimension_id=did, dimension_name=name, average_score_rate=avg))
            if avg < 60:
                common_errors.append(name)
        # 薄弱知识点：聚合本学期参考班级的 weak_tags
        if class_id is not None:
            class_ids = [class_id]
        else:
            class_ids = session.scalars(
                select(ExamScore.class_id_at_exam).where(
                    ExamScore.exam_id == exam_id,
                    ExamScore.class_id_at_exam.is_not(None),
                ).distinct()
            ).all()
        weak_counter: dict[str, int] = {}
        if class_ids:
            enr = session.scalars(select(Enrollment).where(
                Enrollment.term_id == tid, Enrollment.class_id.in_(class_ids),
                Enrollment.status == "active")).all()
            for e in enr:
                if not e.weak_tags:
                    continue
                for tag in [t.strip() for t in e.weak_tags.replace(";", ",").split(",") if t.strip()]:
                    weak_counter[tag] = weak_counter.get(tag, 0) + 1
        weak_points = sorted(weak_counter.items(), key=lambda kv: kv[1], reverse=True)[:8]
        present_stmt = select(func.count()).select_from(ExamScore).where(
            ExamScore.exam_id == exam_id, ExamScore.attendance_status == "present",
        )
        absent_stmt = select(func.count()).select_from(ExamScore).where(
            ExamScore.exam_id == exam_id, ExamScore.attendance_status != "present",
        )
        if class_id is not None:
            present_stmt = present_stmt.where(ExamScore.class_id_at_exam == class_id)
            absent_stmt = absent_stmt.where(ExamScore.class_id_at_exam == class_id)
        present = session.scalar(present_stmt)
        absent = session.scalar(absent_stmt)
        coverage = {"present_count": present or 0, "absent_count": absent or 0,
                    "class_count": len(class_ids)}
        return ReviewPlanFacts(
            term_id=tid, exam_id=exam_id, class_id=class_id,
            dimension_averages=dim_avgs, common_error_dimensions=common_errors,
            weak_knowledge_points=[w for w, _ in weak_points], coverage=coverage,
        )


# --- 工具：确认资料 ---
@router.get("/materials", response_model=list[FormalMaterialMeta],
            dependencies=[Depends(require_plugin_token), Depends(require_plugin_enabled),
                          Depends(negotiate_api_version)])
def list_formal_materials(request: Request, term_id: int | None = Query(default=None, gt=0)):
    with _session(request) as session:
        tid = term_id or current_term_id(session)
        require_term(session, tid)
        provider = FormalContextProvider(session, data_dir=request.app.state.settings.data_dir)
        items = provider.list_confirmed_materials(tid)
        return [FormalMaterialMeta(**i) for i in items]


@router.get("/materials/{material_id}", response_model=FormalMaterialPage,
            dependencies=[Depends(require_plugin_token), Depends(require_plugin_enabled),
                          Depends(negotiate_api_version)])
def read_formal_material(material_id: int, request: Request,
                         term_id: int | None = Query(default=None, gt=0),
                         page: int = Query(default=1, gt=0),
                         page_size: int = Query(default=4000, gt=0, le=20000)):
    with _session(request) as session:
        tid = term_id or current_term_id(session)
        require_term(session, tid)
        provider = FormalContextProvider(session, data_dir=request.app.state.settings.data_dir)
        page_data = provider.read_confirmed_material(material_id, tid, page=page, page_size=page_size)
        if page_data is None:
            raise HTTPException(status_code=404, detail={"code": "not_found",
                                                         "message": "资料不存在、未确认或越权"})
        return FormalMaterialPage(**page_data)


# --- 工具：证据 ---
@router.get("/evidence/{evidence_id}", response_model=EvidenceView,
            dependencies=[Depends(require_plugin_token), Depends(require_plugin_enabled),
                          Depends(negotiate_api_version)])
def get_evidence(evidence_id: str, request: Request):
    with _session(request) as session:
        ev = EvidenceService(session).get_evidence_by_id(evidence_id)
        if ev is None:
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "证据不存在"})
        return EvidenceView(
            evidence_id=ev.evidence_id, evidence_type=ev.evidence_type,
            display_summary=ev.display_summary, local_fact=ev.local_fact_json or {},
            source=EvidenceSource(entity=ev.source_entity, field=ev.source_field,
                                  file=ev.source_file, page=ev.source_page,
                                  question_no=ev.source_question_no, cell=ev.source_cell),
            calculation=EvidenceCalculation(formula=ev.calculation_formula,
                                            numerator=ev.numerator, denominator=ev.denominator),
            contains_personal_data=ev.contains_personal_data,
        )
