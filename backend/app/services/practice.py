"""Teacher practice drafts, immutable questions and evidence-bound assessments."""
from datetime import datetime
from fractions import Fraction
import json
import re
import time
import asyncio
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import case, func, select, update

from ..agent.config import get_agent_config
from ..agent.cost import CostEstimator, UnknownModelPricingError
from ..agent.factory import _create_text_provider
from ..agent.token_budget import estimate_text_tokens, resolve_token_budget
from ..models import AgentSession, AnalysisRun, ExamScore, Student
from ..models.teaching_entities import PracticeAttempt, PracticeQuestion, PracticeSet
from ..schemas.practice import PracticeDraft
from .agent_analysis.conversation import normalize, roster, run_mapper
from .agent_analysis.usage import UsageService
from .teaching import require_task


def practice_skill():
    from pathlib import Path
    path = Path(__file__).resolve().parents[3] / "plugins/bundled/targeted-practice/skills/generate-targeted-practice/SKILL.md"
    body = path.read_text(encoding="utf-8")
    return body.split("---", 2)[-1].strip()


def validate_students(db, task, ids):
    if not ids or len(ids) != len(set(ids)) or any(type(sid) is not int or sid <= 0 for sid in ids):
        raise HTTPException(400, "请选择不重复的有效学生")
    allowed = {r["id"] for r in roster(db, {"term_id": task.term_id, "class_id": task.class_id})}
    if set(ids) - allowed:
        raise HTTPException(400, "包含不属于任务学期或班级的学生")
    if task.target_type != "class" and set(ids) - set(task.student_ids_json):
        raise HTTPException(400, "练习对象不属于本教学任务")


def require_practice(db, task_id, practice_id, writable=False):
    task = require_task(db, task_id, writable)
    item = db.get(PracticeSet, practice_id)
    if item is None or item.task_id != task.id:
        raise HTTPException(404, "练习不属于当前教学任务")
    return task, item


def questions(db, item):
    return list(db.scalars(select(PracticeQuestion).where(PracticeQuestion.practice_id == item.id)
                           .order_by(PracticeQuestion.position)))


def practice_dict(db, item, include_questions=True, student_copy=False):
    rows = questions(db, item)
    names = list(db.scalars(select(Student.name).where(Student.id.in_(item.student_ids_json))))
    run = db.get(AnalysisRun, item.source_run_id) if item.source_run_id else None
    data = {"id": item.id, "task_id": item.task_id, "title": item.title, "objective": item.objective,
            "student_ids": item.student_ids_json, "student_names": names, "level": item.level,
            "status": item.status, "scheduled_for": (item.constraints_json or {}).get("scheduled_for"), "parent_id": item.parent_id, "source_run_id": item.source_run_id,
            "checks": item.checks_json, "question_count": len(rows), "created_at": item.created_at}
    if not student_copy:
        total, pending = db.execute(select(
            func.count(PracticeAttempt.id),
            func.count(case((PracticeAttempt.correct.is_(None), 1))),
        ).where(PracticeAttempt.practice_id == item.id)).one()
        data["attempt_summary"] = {"total": total, "pending": pending}
    if run and not student_copy:
        data["generation_usage"] = {"estimated_cost_yuan": run.estimated_cost_yuan,
            "actual_cost_yuan": run.actual_cost_yuan if (run.input_summary_json or {}).get("usage_known") else None,
            "actual_tokens": run.actual_tokens if (run.input_summary_json or {}).get("usage_known") else None,
            "usage_known": (run.input_summary_json or {}).get("usage_known"),
            "input_parts_estimate": (run.input_summary_json or {}).get("input_parts_estimate"),
            "timings_ms": (run.input_summary_json or {}).get("timings_ms")}
    if include_questions:
        data["questions"] = [{"id": q.id, "position": q.position, "version": q.version,
            **{k: v for k, v in q.content_json.items() if not student_copy or k not in {"answers", "explanation", "hints"}}}
            for q in rows]
    return data


def quality_checks(db, task, payload):
    prompts = [normalize(q.prompt) for q in payload.questions]
    if len(prompts) != len(set(prompts)):
        raise HTTPException(422, "练习中包含重复题目")
    if any(normalize(q.objective) != normalize(payload.objective) for q in payload.questions):
        raise HTTPException(422, "每道题必须对应本份练习的学习目标")
    if payload.level != "progressive" and any(q.difficulty != payload.level for q in payload.questions):
        raise HTTPException(422, "题目难度档位与练习要求不一致")
    if payload.level == "progressive" and len(payload.questions) >= 3:
        ranks = [{"basic": 0, "consolidation": 1, "transfer": 2}[q.difficulty] for q in payload.questions]
        if ranks != sorted(ranks) or len(set(ranks)) < 2:
            raise HTTPException(422, "递进练习需要由易到难且至少包含两个难度档位")
    for q in payload.questions:
        if q.kind == "number":
            try:
                for answer in q.answers:
                    Fraction(normalize(answer))
            except (ValueError, ZeroDivisionError):
                raise HTTPException(422, "数值题答案须为数字或分数；带单位/复杂表达请使用开放题")
    if payload.parent_id:
        _, parent = require_practice(db, task.id, payload.parent_id)
        if normalize(parent.objective) != normalize(payload.objective):
            raise HTTPException(422, "复测必须检查原练习的同一学习目标")
        if set(parent.student_ids_json) != set(payload.student_ids):
            raise HTTPException(422, "复测对象须与原练习一致")
        old = {normalize(q.content_json["prompt"]) for q in questions(db, parent)}
        if old.intersection(prompts):
            raise HTTPException(422, "复测需使用不同题目，不能重复原题")
    return {"structural": "passed", "answer_correctness": "teacher_review_required",
            "difficulty": "teacher_review_required", "curriculum_scope": "teacher_review_required",
            "note": "程序核对结构、选项、重复和目标关联；答案解法、实际难度及已教范围仍需教师核对。"}


def create_draft(db, task, payload, source_run_id=None, verified_source=False):
    validate_students(db, task, payload.student_ids)
    if not verified_source and any(q.source for q in payload.questions):
        raise HTTPException(422, "原题来源只能通过已确认错题索引绑定，不能手工声明")
    checks = quality_checks(db, task, payload)
    item = PracticeSet(task_id=task.id, title=payload.title, objective=payload.objective,
        student_ids_json=payload.student_ids, level=payload.level, parent_id=payload.parent_id,
        source_run_id=source_run_id, checks_json=checks, constraints_json=dict(task.constraints_json))
    db.add(item); db.flush()
    for position, q in enumerate(payload.questions, 1):
        db.add(PracticeQuestion(practice_id=item.id, position=position, objective=q.objective,
                                content_json=q.model_dump(), version=1))
    db.flush()
    return item


def weakness_context(db, task, ids):
    validate_students(db, task, ids)
    from .student_score_details import get_student_score_details
    students = []
    for sid in ids:
        student = db.get(Student, sid)
        row = {"student_id": sid, "student_name": student.name, "status": "missing_exam",
               "weak_sections": [], "missing_items": 0, "suggested_level": "basic"}
        if task.exam_id:
            try:
                details = get_student_score_details(db, exam_id=task.exam_id, student_id=sid, class_id=task.class_id)
                observed = [s for s in details["section_scores"] if s["scored_items"] and s["max_score"] > 0 and s.get("full_score_known", True)]
                complete = [s for s in observed if s["scored_items"] == s["expected_items"]]
                row.update(status=details["detail_status"], total_score=details["total_score"],
                    weak_sections=[dict(s, score_rate=round(s["score"] / s["max_score"], 3))
                                   for s in complete if s["score"] / s["max_score"] < 0.6][:5],
                    missing_items=details["expected_items"]-details["scored_items"],
                    source={"exam_id": task.exam_id, "paper_version": details["paper_version"],
                            "score_updated_at": details["score_updated_at"]},
                    item_examples=details["item_scores"][:8])
                # An editable suggestion, never a persistent label of ability.
                rates = [s["score"] / s["max_score"] for s in complete]
                if rates:
                    minimum = min(rates)
                    row["suggested_level"] = "basic" if minimum < 0.6 else "consolidation" if minimum < 0.85 else "transfer"
            except LookupError:
                row["status"] = "student_exam_score_missing"
        students.append(row)
    from .teaching import task_detail
    feedback = task_detail(db, task)["feedback"]
    from .teaching import task_detail
    recent_observations = [{"kind": f["kind"], "note": f["note"][:600]} for f in feedback
        if f["kind"] in {"observation", "assessment", "implementation"}][-6:]
    performance = progress(db, task)
    return {"teacher_observations": recent_observations, "practice_progress": [r for r in performance["objectives"] if r["student_id"] in ids][-20:],
        "students": students, "teacher_corrections": [f["note"] for f in feedback if f["kind"] == "correction"][-4:],
        "constraints": task.constraints_json, "note": "低分与分组阈值只是表现线索；缺失分数不算零，不能确定错因。缺证据时先用诊断小题验证；教师可以更改分组。"}


def model_estimate(config, text):
    budget = resolve_token_budget(config)
    size = estimate_text_tokens(text)
    if size > budget.prompt_limit:
        raise HTTPException(413, "练习上下文超过本轮预算，请减少学生或题量")
    try:
        cost = CostEstimator().estimate_text(config.text_model_name, size, budget.output_limit).estimated_cost_yuan
    except UnknownModelPricingError as exc:
        raise HTTPException(503, str(exc))
    price_known = CostEstimator().get_pricing(config.text_model_name) is not None
    return {"input_tokens_estimate": size, "output_tokens_limit": budget.output_limit,
            "estimated_cost_yuan": cost if price_known else None, "price_known": price_known,
            "budget_yuan": min(0.5, config.budget_soft_limit_yuan)}


def generation_input(db, task, payload):
    context = weakness_context(db, task, payload.student_ids)
    from .subjects import get_subject
    subject = get_subject(task.subject_key)
    mapper = run_mapper(db, {"term_id": task.term_id, "class_id": task.class_id})
    request = {"subject": {"key": subject.key, "label": subject.label},
               "objective": payload.objective, "level": payload.level, "question_count": payload.question_count,
               "teacher_note": payload.teacher_note, "context": mapper.sanitize_for_model(context)}
    if payload.parent_id:
        _, parent = require_practice(db, task.id, payload.parent_id)
        if set(parent.student_ids_json) != set(payload.student_ids) or normalize(parent.objective) != normalize(payload.objective):
            raise HTTPException(422, "复测对象与目标须沿用原练习")
        request["previous_questions"] = [q.content_json["prompt"] for q in questions(db, parent)]
        request["retest_requirement"] = "同一目标、不同情境的新题；不能改写题号后重复原题。"
    return mapper, mapper.sanitize_text(json.dumps(request, ensure_ascii=False, separators=(",", ":")))


async def call_model(db, task, session_id, messages, confirmed_budget, capability):
    cfg = get_agent_config()
    if not cfg.is_feature_enabled("text_agent_enabled"):
        raise HTTPException(403, "请先开启文字模型功能")
    agent_session = db.get(AgentSession, session_id)
    if agent_session is None or agent_session.teaching_task_id != task.id or agent_session.deleted_at is not None:
        raise HTTPException(400, "请在当前教学任务的有效对话中操作")
    estimate = model_estimate(cfg, json.dumps(messages, ensure_ascii=False))
    limit = confirmed_budget or estimate["budget_yuan"]
    if estimate["estimated_cost_yuan"] is not None and estimate["estimated_cost_yuan"] > limit:
        raise HTTPException(409, detail={"code": "PRACTICE_BUDGET_CONFIRMATION", **estimate,
            "message": "预计费用超过本次预算，请确认费用后再生成"})
    run = AnalysisRun(session_id=session_id, term_id=task.term_id, class_id=task.class_id,
        exam_id=task.exam_id, subject_key=task.subject_key, capability=capability, status="running",
        estimated_cost_yuan=estimate["estimated_cost_yuan"],
        input_summary_json={"model_name": cfg.text_model_name, "provider": cfg.text_provider,
            "teaching_task_id": task.id, "task_revision": task.revision, "estimate": estimate})
    db.add(run); db.commit()
    provider = None
    started = time.perf_counter()
    try:
        provider = _create_text_provider(cfg)
        response = await provider.complete(messages, model=cfg.text_model_name, temperature=0.3,
            max_tokens=estimate["output_tokens_limit"],
            response_format={"type": "json_object"} if provider.get_capabilities(cfg.text_model_name).supports_json else None,
            tools=None)
        usage = response.usage
        if usage:
            cost = CostEstimator().safe_actual_cost(cfg.text_model_name, usage.input_tokens, usage.output_tokens)
            UsageService(db).record_usage(run_id=run.id, provider=cfg.text_provider, model_name=cfg.text_model_name,
                stage=capability, input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                cost_yuan=cost if cost is not None else 0.0, provider_request_id=usage.provider_request_id)
            run.actual_tokens = usage.input_tokens + usage.output_tokens
            run.actual_cost_yuan = cost
        run.input_summary_json = {**run.input_summary_json, "usage_known": usage is not None,
            "model_calls": 1, "timings_ms": {"model_call": int((time.perf_counter()-started)*1000)},
            "input_parts_estimate": {"system": estimate_text_tokens(messages[0]["content"]),
                                     "task_context": estimate_text_tokens(messages[-1]["content"])}}
        if response.finish_reason in {"length", "error"}:
            raise ValueError("模型未完整生成，请减少题量或调整模型输出额度")
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", response.content.strip())
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("模型结果必须是对象")
        db.refresh(task)
        if task.revision != run.input_summary_json["task_revision"] or task.phase in {"archived", "completed"}:
            raise ValueError("生成期间任务已变更，请按最新目标重新生成")
        run.status = "completed"
        run.completed_at = datetime.now(ZoneInfo("UTC"))
        db.commit()
        return parsed, run
    except asyncio.CancelledError:
        run.status = "cancelled"
        run.completed_at = datetime.now(ZoneInfo("UTC"))
        db.commit()
        raise
    except Exception as exc:
        run.status = "failed"
        run.error_message = "生成未完成：" + (str(exc)[:200] if isinstance(exc, (ValueError, HTTPException)) else type(exc).__name__)
        db.commit()
        raise HTTPException(422 if isinstance(exc, ValueError) else 502, run.error_message)
    finally:
        if provider is not None and callable(getattr(provider, "close", None)):
            await provider.close()


def generation_messages(db, task, payload):
    mapper, text = generation_input(db, task, payload)
    schema = {"title": "标题", "objective": payload.objective, "questions": [{
        "objective": payload.objective, "kind": "choice/number/text", "prompt": "完整题干",
        "options": {"A": "选项", "B": "选项"}, "answers": ["有效选项键/数值/参考答案"],
        "explanation": "可核对的解法", "hints": ["启发线索", "关键步骤"],
        "difficulty": "basic/consolidation/transfer"}]}
    messages = [{"role": "system", "content": practice_skill() + "\n为教师设计专项练习。自主选择题型与教学情境，尊重教材、已教内容和教师纠正；证据不足时出诊断小题，不认定错因。只输出 JSON，结构为：" +
        json.dumps(schema, ensure_ascii=False) + "。顶层仅包含 title、objective、questions，不输出 level、parent_id 等服务器字段。实际题量与难度按请求；非选择题 options 为 {}，选择题 answers 为选项键。递进练习由易到难。每题目标必须与请求一致。根据 subject 使用学科适用的练习语言：英语阅读的语篇、题干和选项用英语，解释与提示可用中文；教师有明确语言要求时尊重要求。语篇放 passage，题干放 prompt，不在题干重复语篇或选项。复测更换情境但保持同一目标及已教考查方式，不擅自升级难度。教师要求原文直接定位时，答案必须由原文直接给出，不能改成额外计算或推断。不得把学生身份写进题目。"},
        {"role": "user", "content": text}]
    return mapper, messages


async def generate(db, task, payload):
    mapper, messages = generation_messages(db, task, payload)
    result, run = await call_model(db, task, payload.session_id, messages, payload.confirmed_budget_yuan, "practice_generation")
    try:
        if len(result.get("questions", [])) != payload.question_count:
            raise ValueError("生成题量与要求不一致")
        draft = PracticeDraft.model_validate({**result, "student_ids": payload.student_ids,
            "level": payload.level, "parent_id": payload.parent_id})
        if normalize(draft.objective) != normalize(payload.objective):
            raise ValueError("生成目标与请求不一致")
        item = create_draft(db, task, draft, run.id)
        if payload.scheduled_for:
            item.constraints_json = {**item.constraints_json, "scheduled_for": payload.scheduled_for.isoformat()}
        db.commit()
        return item
    except (ValueError, TypeError, HTTPException) as exc:
        db.rollback()
        run = db.get(AnalysisRun, run.id)
        run.status = "failed"; run.error_message = "练习未通过内容校验，请调整要求后重试"
        db.commit()
        raise HTTPException(422, run.error_message) from exc


def grade(question, answer):
    content = question.content_json
    if content["kind"] == "text":
        return None, "pending"
    if content["kind"] == "number":
        try:
            value = Fraction(normalize(answer))
            return any(value == Fraction(normalize(a)) for a in content["answers"]), "local"
        except (ValueError, ZeroDivisionError):
            return None, "pending"
    return normalize(answer) in {normalize(a) for a in content["answers"]}, "local"


def attempt_dict(item):
    return {"id": item.id, "practice_id": item.practice_id, "question_id": item.question_id,
        "student_id": item.student_id, "answer": item.answer, "observed_on": item.observed_on,
        "hint_level": item.hint_level, "answer_viewed": item.answer_viewed,
        "independent": item.hint_level == 0 and not item.answer_viewed, "new_question": item.new_question,
        "correct": item.correct, "graded_by": item.graded_by, "feedback": item.feedback_json,
        "corrections": item.corrections_json, "revision": item.revision}


def add_attempt(db, task, item, payload):
    validate_students(db, task, [payload.student_id])
    if payload.student_id not in item.student_ids_json:
        raise HTTPException(400, "学生不属于这份练习")
    if item.status != "confirmed":
        raise HTTPException(409, "请先核对并采用这份练习")
    question = db.get(PracticeQuestion, payload.question_id)
    if question is None or question.practice_id != item.id:
        raise HTTPException(400, "题目不属于这份练习")
    if payload.observed_on > datetime.now(ZoneInfo("Asia/Shanghai")).date():
        raise HTTPException(400, "作答日期不能晚于今天")
    existing = db.scalar(select(PracticeAttempt).where(PracticeAttempt.practice_id == item.id,
        PracticeAttempt.submission_key == payload.submission_key))
    if existing:
        for field in ("student_id", "question_id", "answer", "observed_on", "hint_level", "answer_viewed"):
            if getattr(existing, field) != getattr(payload, field):
                raise HTTPException(409, "同一提交标识的作答内容不一致")
        return existing
    prior = list(db.scalars(select(PracticeAttempt).where(PracticeAttempt.student_id == payload.student_id)
                           .join(PracticeSet, PracticeSet.id == PracticeAttempt.practice_id)
                           .where(PracticeSet.task_id == task.id)))
    novel = not question.content_json.get("source", {}).get("original_reuse", False) and all(normalize(db.get(PracticeQuestion, p.question_id).content_json["prompt"]) !=
                normalize(question.content_json["prompt"]) for p in prior)
    correct, graded_by = grade(question, payload.answer)
    attempt = PracticeAttempt(practice_id=item.id, **payload.model_dump(), correct=correct,
        graded_by=graded_by, new_question=novel,
        feedback_json={"status": "confirmed_by_key" if graded_by == "local" else "teacher_review_required",
                       "note": "按教师已采用的答案核对" if graded_by == "local" else "开放作答或格式不确定，请教师核对；可请求 AI 反馈建议"})
    db.add(attempt); db.flush()
    return attempt


def correct_attempt(db, item, payload):
    entry = {"revision": item.revision, "correct": item.correct, "graded_by": item.graded_by,
             "reason": payload.reason, "corrected_at": datetime.now(ZoneInfo("UTC")).isoformat()}
    changed = db.execute(update(PracticeAttempt).where(PracticeAttempt.id == item.id,
        PracticeAttempt.revision == payload.expected_revision).values(correct=payload.correct,
        graded_by="teacher", corrections_json=item.corrections_json + [entry], revision=payload.expected_revision+1))
    if changed.rowcount != 1:
        raise HTTPException(409, "作答反馈已更新，请刷新后重试")
    db.flush(); db.refresh(item)
    return item


def progress(db, task):
    rows = list(db.scalars(select(PracticeAttempt).join(PracticeSet, PracticeSet.id == PracticeAttempt.practice_id)
        .where(PracticeSet.task_id == task.id).order_by(PracticeAttempt.id)))
    grouped = {}
    for a in rows:
        question = db.get(PracticeQuestion, a.question_id)
        set_ = db.get(PracticeSet, a.practice_id)
        g = grouped.setdefault((a.student_id, question.objective), {"student_id": a.student_id,
            "student_name": db.get(Student, a.student_id).name, "objective": question.objective,
            "attempts": 0, "correct": 0, "pending": 0, "hinted_correct": 0,
            "independent_new_attempts": 0, "independent_new_correct": 0,
            "retest_attempts": 0, "retest_correct": 0})
        g["attempts"] += 1; g["correct"] += int(a.correct is True); g["pending"] += int(a.correct is None)
        independent = a.hint_level == 0 and not a.answer_viewed
        g["hinted_correct"] += int(a.correct is True and not independent)
        if independent and a.new_question and a.correct is not None:
            g["independent_new_attempts"] += 1; g["independent_new_correct"] += int(a.correct)
        if set_.parent_id and independent and a.new_question and a.correct is not None:
            g["retest_attempts"] += 1; g["retest_correct"] += int(a.correct)
    return {"objectives": list(grouped.values()), "mastery_status": "pending_evidence",
            "note": "答对、提示后完成与独立新题分别统计；难度或条件不一致时不可直接比较，一次答对不自动认定稳定掌握。"}


async def feedback_suggestion(db, task, question, attempt, payload):
    mapper = run_mapper(db, {"term_id": task.term_id, "class_id": task.class_id})
    revision = attempt.revision
    data = {"question": question.content_json, "student_answer": attempt.answer,
            "hints_used": attempt.hint_level, "answer_viewed": attempt.answer_viewed,
            "constraints": task.constraints_json}
    messages = [{"role": "system", "content": practice_skill() + "\n为教师提供作答反馈建议，考虑等价表达和替代解法。不得把参考答案措辞当唯一解法，不确定就说明。仅输出 JSON：{\"suggested_correct\":true/false/null,\"feedback\":\"基于具体作答的解释\",\"next_step\":\"下一步建议\"}。不得自行确认正式成绩或掌握。"},
                {"role": "user", "content": mapper.sanitize_text(json.dumps(data, ensure_ascii=False))}]
    result, run = await call_model(db, task, payload.session_id, messages, payload.confirmed_budget_yuan, "practice_feedback")
    if type(result.get("suggested_correct")) not in {bool, type(None)} or not isinstance(result.get("feedback"), str) or not result["feedback"].strip():
        run.status = "failed"; run.error_message = "作答反馈结构无效"; db.commit()
        raise HTTPException(422, run.error_message)
    updated = {"status": "teacher_review_required", "suggested_correct": result.get("suggested_correct"),
        "feedback": mapper.sanitize_text(result["feedback"])[:3000], "next_step": mapper.sanitize_text(str(result.get("next_step") or ""))[:1500], "run_id": run.id}
    changed = db.execute(update(PracticeAttempt).where(PracticeAttempt.id == attempt.id,
        PracticeAttempt.revision == revision).values(feedback_json=updated, revision=revision+1))
    if changed.rowcount != 1:
        run.status = "failed"; run.error_message = "生成期间作答已修正，未覆盖教师判断"; db.commit()
        raise HTTPException(409, run.error_message)
    db.commit(); db.refresh(attempt)
    return attempt
