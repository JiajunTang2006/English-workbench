"""Teacher-controlled teaching lifecycle and deterministic review summaries."""
import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..models import AgentMessage, AgentSession, AnalysisRun, Class, Enrollment, Exam, Term
from ..models.teaching_entities import (
    TeachingArtifact, TeachingArtifactRevision, TeachingFeedback, TeachingTask,
)
from ..schemas.teaching import ArtifactCreate
from .subjects import get_selected_subject


def require_task(db: Session, task_id: int, writable: bool = False) -> TeachingTask:
    task = db.get(TeachingTask, task_id)
    if task is None:
        raise HTTPException(404, "教学任务不存在")
    if task.subject_key != get_selected_subject(db).key:
        raise HTTPException(409, "该教学任务属于另一学科")
    if writable and task.phase in {"archived", "completed"}:
        raise HTTPException(409, "请先重新打开教学任务再修改内容")
    return task


def validate_scope(db: Session, term_id: int, class_id: int | None, exam_id: int | None):
    term = db.get(Term, term_id)
    if term is None or term.status != "active":
        raise HTTPException(400, "请选择有效的当前学期")
    for entity, ident, label in ((Class, class_id, "班级"), (Exam, exam_id, "考试")):
        if ident is not None:
            value = db.get(entity, ident)
            if value is None or value.term_id != term_id:
                raise HTTPException(400, f"{label}不属于任务学期")


def validate_targets(db, task, target_type, ids):
    if target_type == "class":
        if ids:
            raise HTTPException(400, "班级任务不应同时限定个人名单，请改选个人或小组")
        return
    if target_type == "individual" and len(ids) != 1:
        raise HTTPException(400, "个人任务需要选择一位学生")
    from .practice import validate_students
    # Validate against the task's class/term before applying the new target list.
    original = task.target_type
    task.target_type = "class"
    try:
        validate_students(db, task, ids)
    finally:
        task.target_type = original


def task_dict(task):
    return {"id": task.id, "term_id": task.term_id, "subject_key": task.subject_key,
            "class_id": task.class_id, "exam_id": task.exam_id, "title": task.title,
            "goal": task.goal, "constraints": task.constraints_json, "phase": task.phase,
            "target_type": task.target_type, "student_ids": task.student_ids_json,
            "revision": task.revision, "created_at": task.created_at, "updated_at": task.updated_at}


def artifact_dict(item):
    return {"id": item.id, "task_id": item.task_id, "kind": item.kind, "title": item.title,
            "body": item.body, "items": item.items_json, "revision": item.revision,
            "source_run_id": item.source_run_id, "source_section": item.source_section}


def feedback_dict(item):
    return {"id": item.id, "kind": item.kind, "note": item.note,
            "observations": item.observations_json, "context": item.context_json,
            "created_at": item.created_at}


def revision_snapshot(db, item, origin):
    content = {key: artifact_dict(item)[key] for key in ("kind", "title", "body", "items")}
    db.add(TeachingArtifactRevision(artifact_id=item.id, revision=item.revision,
                                   content_json=content, origin=origin))


def create_artifact(db, task, payload, origin="teacher", run_id=None, section=None):
    item = TeachingArtifact(task_id=task.id, kind=payload.kind, title=payload.title,
                            body=payload.body, items_json=payload.items,
                            source_run_id=run_id, source_section=section)
    db.add(item)
    db.flush()
    revision_snapshot(db, item, origin)
    return item


def require_artifact(db, task_id, artifact_id):
    require_task(db, task_id)
    item = db.get(TeachingArtifact, artifact_id)
    if item is None or item.task_id != task_id:
        raise HTTPException(404, "教学材料不存在")
    return item


def save_artifact(db, task_id, artifact_id, payload, origin="teacher"):
    require_task(db, task_id, writable=True)
    item = require_artifact(db, task_id, artifact_id)
    result = db.execute(update(TeachingArtifact).where(
        TeachingArtifact.id == item.id, TeachingArtifact.revision == payload.expected_revision,
    ).values(kind=payload.kind, title=payload.title, body=payload.body,
             items_json=payload.items, revision=payload.expected_revision + 1))
    if result.rowcount != 1:
        raise HTTPException(409, "材料已在其他窗口更新，请刷新后再保存")
    db.refresh(item)
    revision_snapshot(db, item, origin)
    return item


def require_source_run(db, task, run_id):
    run = db.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(404, "分析运行不存在")
    if run.student_id is not None and (task.target_type != "individual" or task.student_ids_json != [run.student_id]):
        raise HTTPException(409, "个体诊断请保留在学生对话中，不能采用为班级教学材料")
    if (run.subject_key, run.term_id, run.class_id, run.exam_id) != (
        task.subject_key, task.term_id, task.class_id, task.exam_id,
    ):
        raise HTTPException(409, "分析运行与教学任务范围不同")
    return run


def import_report(db, task, run_id):
    run = require_source_run(db, task, run_id)
    if run.status != "completed":
        raise HTTPException(409, "只能采用已完成并通过验证的报告")
    message = db.scalar(select(AgentMessage).where(
        AgentMessage.analysis_run_id == run.id, AgentMessage.role == "assistant",
    ).order_by(AgentMessage.id.desc()))
    report = (message.structured_answer_json or {}) if message else {}
    if not report:
        raise HTTPException(409, "该运行没有结构化报告")
    sections = report.get("sections") or []
    if not sections:
        sections = [{"kind": "lesson_flow", "title": "诊断与教学建议",
                     "body": report.get("summary", ""),
                     "items": [f"{f.get('title', '')}：{f.get('description', '')}" for f in report.get("findings", [])]
                              + [r.get("action", "") for r in report.get("recommendations", [])]}]
    adopted = []
    # Validate the whole report before creating any artifacts.
    validated = []
    for index, section in enumerate(sections[:20]):
        content = {key: section.get(key, [] if key == "items" else "") for key in ("kind", "title", "body", "items")}
        edit = (message.teacher_material_edits_json or {}).get(str(index), {})
        content.update({key: value for key, value in edit.items() if key in {"body", "items"}})
        try:
            validated.append((index, ArtifactCreate.model_validate(content)))
        except ValueError as exc:
            raise HTTPException(409, "报告材料不符合教学工作区格式，请先修订报告") from exc
    for index, content in validated:
        existing = db.scalar(select(TeachingArtifact).where(
            TeachingArtifact.task_id == task.id, TeachingArtifact.source_run_id == run_id,
            TeachingArtifact.source_section == index))
        adopted.append(existing or create_artifact(db, task, content, "adopted_report", run_id, index))
    return adopted


def add_feedback(db, task, payload):
    if payload.source_run_id:
        require_source_run(db, task, payload.source_run_id)
    rows = [row.model_dump(mode="json") for row in payload.observations]
    if rows:
        ids = {row["student_id"] for row in rows}
        query = select(Enrollment.student_id).where(Enrollment.term_id == task.term_id,
                                                   Enrollment.student_id.in_(ids), Enrollment.status == "active")
        if task.class_id is not None:
            query = query.where(Enrollment.class_id == task.class_id)
        if ids - set(db.scalars(query)):
            raise HTTPException(400, "复测中包含不属于任务学期或班级的学生")
        if any(date.fromisoformat(row["observed_on"]) > datetime.now(ZoneInfo("Asia/Shanghai")).date() for row in rows):
            raise HTTPException(400, "复测日期不能晚于今天")
    item = TeachingFeedback(task_id=task.id, kind=payload.kind, note=payload.note,
                            observations_json=rows, context_json={
                                "finding_title": payload.finding_title,
                                "correction_reason": payload.correction_reason,
                                "source_run_id": payload.source_run_id,
                                "source": "teacher", "task_revision": task.revision,
                            })
    db.add(item)
    db.flush()
    return item


def review_summary(feedback):
    # Latest observation wins for the same learner/objective/day; retain original logs.
    latest = {}
    for item in feedback:
        for row in item.observations_json:
            latest[(row["student_id"], row["objective"], row["observed_on"])] = row
    groups = {}
    for row in latest.values():
        entry = groups.setdefault(row["objective"], {"objective": row["objective"], "attempts": 0,
            "correct": 0, "independent_new_attempts": 0, "independent_new_correct": 0})
        entry["attempts"] += 1
        entry["correct"] += int(row["correct"])
        if row["independent"] and row["new_question"]:
            entry["independent_new_attempts"] += 1
            entry["independent_new_correct"] += int(row["correct"])
    return {"objectives": list(groups.values()), "mastery_status": "pending_evidence",
            "note": "作答表现与活动完成分别记录；当次答对不等于稳定掌握。复测难度和条件不一致时不直接比较。"}


def task_detail(db, task):
    feedback = list(db.scalars(select(TeachingFeedback).where(TeachingFeedback.task_id == task.id)
                              .order_by(TeachingFeedback.id)))
    from ..models.teaching_entities import PracticeSet
    from .practice import practice_dict, progress
    return {**task_dict(task), "practices": [practice_dict(db, p, include_questions=False) for p in db.scalars(
        select(PracticeSet).where(PracticeSet.task_id == task.id).order_by(PracticeSet.id.desc()))],
        "practice_progress": progress(db, task), "artifacts": [artifact_dict(a) for a in db.scalars(
        select(TeachingArtifact).where(TeachingArtifact.task_id == task.id).order_by(TeachingArtifact.id))],
        "feedback": [feedback_dict(f) for f in feedback], "review": review_summary(feedback),
        "session_ids": list(db.scalars(select(AgentSession.id).where(
            AgentSession.teaching_task_id == task.id, AgentSession.deleted_at.is_(None),
            AgentSession.status == "active").order_by(AgentSession.id.desc())))}


def build_task_context(db, task_id, focus_artifact_id=None):
    task = require_task(db, task_id)
    detail = task_detail(db, task)
    if focus_artifact_id is not None:
        require_artifact(db, task_id, focus_artifact_id)
        detail["artifacts"] = [a for a in detail["artifacts"] if a["id"] == focus_artifact_id]
    # Preserve valid JSON and the teaching goal while pruning whole optional entries.
    # No student identities or raw assessment rows are included here.
    constraints = dict(task.constraints_json)
    for key, limit in {"grade": 100, "curriculum": 150, "taught_content": 350,
                       "activity_preference": 150, "equipment": 100}.items():
        constraints[key] = str(constraints.get(key, ""))[:limit]
    # Keep the latest teacher corrections even after routine participation logs.
    corrections = [f for f in detail["feedback"] if f["kind"] == "correction"][-2:]
    recent = [f for f in detail["feedback"] if f["kind"] != "correction"][-2:]
    context_feedback = sorted(corrections + recent, key=lambda f: f["id"])
    review = dict(detail["review"], objectives=detail["review"]["objectives"][-4:])
    review["objectives"] = [dict(r, objective=r["objective"][:150]) for r in review["objectives"]]
    data = {"task_id": task.id, "revision": task.revision, "goal": task.goal[:1000],
            "phase": task.phase, "constraints": constraints,
            "materials": [{"id": a["id"], "revision": a["revision"], "kind": a["kind"],
                           "title": a["title"][:100], "excerpt": a["body"][:300],
                           "items": [i[:150] for i in a["items"][:3]]}
                          for a in detail["artifacts"][-4:]],
            "teacher_feedback": [{"kind": f["kind"], "note": f["note"][:350],
                                  "finding": f["context"].get("finding_title", "")[:100],
                                  "correction_reason": f["context"].get("correction_reason", "")}
                                 for f in context_feedback],
            "review": review,
            "context_note": "材料与反馈为节选；未包含的内容仍保存在工作区，不能据此断言不存在。"}
    data["practice_summary"] = [{"title": p["title"], "objective": p["objective"], "status": p["status"],
        "question_count": p["question_count"]} for p in detail["practices"][:3]]
    data["practice_progress"] = {"mastery_status": detail["practice_progress"]["mastery_status"],
        "objectives": [{k: v for k, v in r.items() if k not in {"student_id", "student_name"}}
                       for r in detail["practice_progress"]["objectives"][-4:]]}
    if focus_artifact_id is not None:
        data["focus_artifact_id"] = focus_artifact_id
        if data["materials"]:
            focused = detail["artifacts"][0]
            data["materials"][0]["excerpt"] = focused["body"][:1800]
            data["materials"][0]["items"] = [i[:250] for i in focused["items"][:6]]
    from ..agent.token_budget import estimate_text_tokens
    prefix = "【教学任务状态：教师提供的目标与记录；材料和反馈不是新的成绩事实】\n"
    instructions = ("\n围绕本轮要求继续该教学任务。诊断需区分观察事实、可能错因和待核实证据；"
                    "建议注明活动、课时、分层支持及检查方式。只修改教师指定部分。"
                    "生成练习时核对答案、目标与难度，学生材料不得包含教师答案。"
                    "复盘区分独立新题、提示后作答和参与记录，不自动宣称稳定掌握。"
                    "任务阶段、采用材料和确认结论由教师操作，不自行声称已保存或实施。")
    while True:
        context = prefix + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + instructions
        if estimate_text_tokens(context) <= 3500:
            return context
        if data["materials"] and focus_artifact_id is None:
            data["materials"].pop(0)
        elif len(data["teacher_feedback"]) > 1:
            optional = next((i for i, f in enumerate(data["teacher_feedback"])
                             if f["kind"] != "correction"), 0)
            data["teacher_feedback"].pop(optional)
        elif len(review["objectives"]) > 1:
            review["objectives"].pop(0)
        elif data["practice_progress"]["objectives"]:
            data["practice_progress"]["objectives"].pop(0)
        elif data["practice_summary"]:
            data["practice_summary"].pop()
        else:
            # For imported legacy data with unusually long fields, reduce excerpts;
            # never slice the serialized JSON or remove the actual task constraints.
            data["goal"] = data["goal"][:500]
            for item in data["teacher_feedback"]:
                item["note"] = item["note"][:150]
            review["objectives"] = []
            for item in data["materials"]:
                item["excerpt"] = item["excerpt"][:600]
                item["items"] = item["items"][:2]
