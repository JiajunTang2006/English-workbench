"""AnalysisGroup 创建、调度和状态聚合。"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import select

from ..agent.registry.capabilities import create_default_capability_registry
from ..models.agent_entities import AnalysisGroup, AnalysisGroupTask, AnalysisRun
from ..services.agent_runs.scheduler import schedule_analysis_run
from .multi_agent import AnalysisGroupPlan, build_scope_snapshot, plan_student_batch

logger = logging.getLogger(__name__)

# asyncio 只对 create_task 保留弱引用；任务组本身又不经过普通 run registry，
# 因此必须持有强引用，避免请求返回后组任务被回收而永久停在 running。
_ACTIVE_GROUP_TASKS: dict[int, asyncio.Task] = {}

# 并发供应商偶发超时/限流时，自动再试一次。次数要有限，避免教师不知情地
# 无限消耗额度；失败任务仍会保留在组卡片中，交给教师选择性手动重试。
AUTO_RETRY_LIMIT = 1
AUTO_RETRY_BACKOFF_SECONDS = 0.75
AUTO_RETRY_STAGGER_SECONDS = 0.2


def cancel_active_analysis_group(group_id: int) -> bool:
    """取消任务组的调度协程，避免取消接口返回后外层协程继续推进。"""
    active = _ACTIVE_GROUP_TASKS.get(int(group_id))
    if active is None or active.done():
        return False
    try:
        loop = active.get_loop()
        current = asyncio.get_running_loop()
        if loop is current:
            active.cancel()
        elif loop.is_running():
            loop.call_soon_threadsafe(active.cancel)
        else:
            active.cancel()
        return True
    except RuntimeError:
        # 任务已脱离运行中的事件循环；数据库状态仍由路由负责落库。
        active.cancel()
        return True


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_student_group(
    session,
    *,
    session_id: int,
    term_id: int,
    class_id: int | None,
    exam_id: int,
    student_ids: list[int],
    max_concurrency: int = 4,
    shard_size: int = 6,
    include_exam_analysis: bool = False,
    estimated_cost_yuan: float | None = None,
) -> tuple[AnalysisGroup, list[AnalysisGroupTask], AnalysisGroupPlan]:
    """在一个事务中创建任务组和其子运行。

    当前子运行沿用现有单学生诊断契约，因此每个学生对应一个
    ``AnalysisRun``；任务组负责并发上限、共享快照和统一状态。
    """
    plan = plan_student_batch(
        student_ids,
        include_exam_analysis=include_exam_analysis,
        max_concurrency=max_concurrency,
        shard_size=shard_size,
    )
    normalized_ids = [student_id for shard in plan.shards for student_id in shard.student_ids]
    from .subjects import get_selected_subject
    subject_key = get_selected_subject(session).key
    snapshot_id, snapshot = build_scope_snapshot(
        term_id=term_id,
        class_id=class_id,
        exam_id=exam_id,
        student_ids=normalized_ids,
    )
    group = AnalysisGroup(
        group_type=plan.group_type,
        term_id=term_id,
        class_id=class_id,
        exam_id=exam_id,
        status="queued",
        requested_student_count=len(normalized_ids),
        max_concurrency=plan.max_concurrency,
        estimated_cost_yuan=estimated_cost_yuan,
        scope_snapshot_id=snapshot_id,
        scope_snapshot_json=snapshot,
    )
    session.add(group)
    session.flush()

    tasks: list[AnalysisGroupTask] = []
    if include_exam_analysis:
        exam_run = AnalysisRun(
            session_id=session_id,
            capability="exam_analysis",
            term_id=term_id,
            subject_key=subject_key,
            class_id=class_id,
            exam_id=exam_id,
            status="queued",
            input_summary_json={
                "analysis_group_id": group.id,
                "scope_snapshot_id": snapshot_id,
                "scope_snapshot": snapshot,
                "user_message": "批量任务组：考试整体分析",
            },
        )
        session.add(exam_run)
        session.flush()
        exam_task = AnalysisGroupTask(
            group_id=group.id,
            analysis_run_id=exam_run.id,
            task_role="exam_agent",
            shard_index=0,
            student_ids_json=[],
            status="queued",
        )
        session.add(exam_task)
        tasks.append(exam_task)
    for shard_index, student_id in enumerate(normalized_ids, start=1):
        # Each run remains independently auditable and can use the existing
        # student_diagnosis tool contract without leaking another student's ID.
        run = AnalysisRun(
            session_id=session_id,
            capability="student_diagnosis",
            term_id=term_id,
            subject_key=subject_key,
            class_id=class_id,
            exam_id=exam_id,
            student_id=student_id,
            status="queued",
            input_summary_json={
                "analysis_group_id": group.id,
                "scope_snapshot_id": snapshot_id,
                "scope_snapshot": snapshot,
                "group_student_count": len(normalized_ids),
                "group_max_concurrency": plan.max_concurrency,
                "user_message": "批量学生画像分析",
            },
        )
        session.add(run)
        session.flush()
        task = AnalysisGroupTask(
            group_id=group.id,
            analysis_run_id=run.id,
            task_role="student_worker",
            shard_index=shard_index,
            student_ids_json=[student_id],
            status="queued",
        )
        session.add(task)
        tasks.append(task)
    session.commit()
    session.refresh(group)
    return group, tasks, plan


def _queue_task_retry(
    db,
    task: AnalysisGroupTask,
    run: AnalysisRun,
    *,
    reason: str | None = None,
    force: bool = False,
) -> AnalysisRun | None:
    """为失败的子运行创建新的可调度记录，并保留旧运行作为审计记录。"""
    if not force and task.retry_count >= AUTO_RETRY_LIMIT:
        return None
    attempt = int(task.retry_count or 0) + 1
    summary = dict(run.input_summary_json or {})
    summary.update({
        "retry_of_run_id": run.id,
        "retry_attempt": attempt,
    })
    if reason:
        summary["retry_reason"] = str(reason)[:500]
    # 调度器在真正创建子任务前抛错时，旧 run 可能仍是 queued；先封存为失败，
    # 防止应用重启恢复时把已经替换掉的旧 run 又执行一遍。
    if run.status not in {"completed", "degraded", "failed", "cancelled"}:
        run.status = "failed"
        run.error_message = str(reason or "批量任务自动重试")[:2000]
        run.completed_at = _utcnow()
    retry_run = AnalysisRun(
        session_id=run.session_id,
        capability=run.capability,
        term_id=run.term_id,
        subject_key=run.subject_key,
        class_id=run.class_id,
        exam_id=run.exam_id,
        student_id=run.student_id,
        status="queued",
        input_summary_json=summary,
        rules_version=run.rules_version,
        prompt_version=run.prompt_version,
        estimated_cost_yuan=run.estimated_cost_yuan,
        estimated_tokens=run.estimated_tokens,
        retry_count=attempt,
    )
    db.add(retry_run)
    db.flush()
    task.analysis_run_id = retry_run.id
    task.retry_count = attempt
    task.status = "queued"
    task.error_message = None
    task.completed_at = None
    return retry_run


def prepare_analysis_group_retry(session, group: AnalysisGroup, student_ids: list[int]) -> tuple[list[AnalysisGroupTask], list[int]]:
    """把选中的失败学生重新置为 queued，返回任务和不合格的学生 ID。"""
    selected = {int(value) for value in student_ids}
    tasks = list(session.scalars(
        select(AnalysisGroupTask)
        .where(AnalysisGroupTask.group_id == group.id)
        .order_by(AnalysisGroupTask.shard_index)
    ))
    eligible = {"failed", "cancelled", "queued", "running", "waiting_confirmation"}
    selected_tasks: list[AnalysisGroupTask] = []
    invalid: list[int] = []
    for task in tasks:
        ids = [int(value) for value in (task.student_ids_json or [])]
        matches = selected.intersection(ids)
        if not matches:
            continue
        if task.task_role != "student_worker" or task.status not in eligible:
            invalid.extend(sorted(matches))
            continue
        selected_tasks.append(task)
    matched = {int(value) for task in selected_tasks for value in (task.student_ids_json or [])} & selected
    invalid.extend(sorted(selected - matched - set(invalid)))
    if invalid:
        return selected_tasks, sorted(set(invalid))
    for task in selected_tasks:
        old_run = session.get(AnalysisRun, task.analysis_run_id)
        if old_run is None:
            invalid.extend(int(value) for value in (task.student_ids_json or []) if int(value) in selected)
            continue
        _queue_task_retry(session, task, old_run, reason="教师选择重新生成", force=True)
    if invalid:
        return selected_tasks, sorted(set(invalid))
    group.status = "queued"
    group.error_message = None
    group.actual_cost_yuan = None
    group.completed_at = None
    return selected_tasks, []


async def schedule_analysis_group(
    group_id: int,
    db_session_factory: Callable,
) -> asyncio.Task:
    """受控调度任务组；每个学生运行独立占用一个并发槽位。"""
    from ..agent.runtime.event_projection import get_main_event_loop

    async def _run_group_body() -> None:
        with db_session_factory() as db:
            group = db.get(AnalysisGroup, group_id)
            if group is None:
                return
            group.status = "running"
            db.commit()
            tasks = list(
                db.scalars(
                    select(AnalysisGroupTask)
                    .where(AnalysisGroupTask.group_id == group_id)
                    .order_by(AnalysisGroupTask.shard_index)
                )
            )
            run_ids = [task.analysis_run_id for task in tasks]
            confirmed_budget = (group.scope_snapshot_json or {}).get("confirmed_budget_yuan")
            runs = {run.id: run for run in db.scalars(select(AnalysisRun).where(AnalysisRun.id.in_(run_ids)))}
            messages = {
                run_id: (
                    "请完成这次考试的整体分析，输出事实、证据、局限和教学建议。"
                    if runs[run_id].capability == "exam_analysis"
                    else "请对指定学生完成学生诊断，并将整理后的画像摘要直接写入学生管理。"
                )
                for run_id in run_ids
            }

        semaphore = asyncio.Semaphore(max(1, min(group.max_concurrency, 4)))

        async def _one(task_id: int, run_id: int) -> None:
            current_run_id = run_id
            while True:
                retry_run_id: int | None = None
                retry_delay = AUTO_RETRY_BACKOFF_SECONDS
                async with semaphore:
                    with db_session_factory() as db:
                        task = db.get(AnalysisGroupTask, task_id)
                        run = db.get(AnalysisRun, current_run_id)
                        if task is None or run is None or task.status == "cancelled":
                            return
                        task.status = "running"
                        db.commit()
                        run = db.get(AnalysisRun, current_run_id)
                    try:
                        child = await schedule_analysis_run(
                            run,
                            messages.get(current_run_id, messages.get(run_id, "请完成这次学生诊断，并将整理后的画像摘要直接写入学生管理。")),
                            db_session_factory,
                            confirmed_budget_yuan=float(confirmed_budget) if confirmed_budget is not None else None,
                        )
                        await child
                    except asyncio.CancelledError:
                        with db_session_factory() as db:
                            task = db.get(AnalysisGroupTask, task_id)
                            if task is not None:
                                task.status = "cancelled"
                                task.completed_at = _utcnow()
                                db.commit()
                        raise
                    except Exception as exc:  # pragma: no cover - defensive boundary
                        logger.exception("analysis group task %d failed", task_id)
                        with db_session_factory() as db:
                            task = db.get(AnalysisGroupTask, task_id)
                            run = db.get(AnalysisRun, current_run_id)
                            if task is not None and run is not None and task.status != "cancelled":
                                retry = _queue_task_retry(db, task, run, reason=str(exc))
                                if retry is not None:
                                    messages[retry.id] = messages.get(current_run_id, messages.get(run_id, "请完成这次学生诊断，并将整理后的画像摘要直接写入学生管理。"))
                                    retry_run_id = retry.id
                                    retry_delay = AUTO_RETRY_BACKOFF_SECONDS * max(1, int(retry.retry_count or 1)) + (task_id % 4) * AUTO_RETRY_STAGGER_SECONDS
                                    db.commit()
                                else:
                                    task.status = "failed"
                                    task.error_message = str(exc)[:2000]
                                    task.completed_at = _utcnow()
                                    db.commit()
                    else:
                        with db_session_factory() as db:
                            task = db.get(AnalysisGroupTask, task_id)
                            run = db.get(AnalysisRun, current_run_id)
                            if task is None or run is None:
                                return
                            if run.status == "failed" and task.status != "cancelled":
                                retry = _queue_task_retry(db, task, run, reason=run.error_message)
                                if retry is not None:
                                    messages[retry.id] = messages.get(current_run_id, messages.get(run_id, "请完成这次学生诊断，并将整理后的画像摘要直接写入学生管理。"))
                                    retry_run_id = retry.id
                                    retry_delay = AUTO_RETRY_BACKOFF_SECONDS * max(1, int(retry.retry_count or 1)) + (task_id % 4) * AUTO_RETRY_STAGGER_SECONDS
                                    db.commit()
                                else:
                                    task.status = "failed"
                                    task.error_message = run.error_message
                                    task.completed_at = _utcnow()
                                    db.commit()
                            else:
                                task.status = run.status
                                task.error_message = run.error_message
                                task.completed_at = _utcnow() if run.status in {"completed", "degraded", "failed", "cancelled"} else None
                                db.commit()
                if retry_run_id is None:
                    return
                await asyncio.sleep(retry_delay)
                current_run_id = retry_run_id

        await asyncio.gather(*(_one(task.id, task.analysis_run_id) for task in tasks), return_exceptions=True)

        with db_session_factory() as db:
            group = db.get(AnalysisGroup, group_id)
            if group is None:
                return
            # 取消接口已经把组标记为 cancelled；不要让仍在收尾的外层
            # 协程按已完成子任务重新聚合，覆盖教师刚刚发出的停止操作。
            if group.status == "cancelled":
                return
            statuses = [task.status for task in db.scalars(select(AnalysisGroupTask).where(AnalysisGroupTask.group_id == group_id))]
            if statuses and all(status == "cancelled" for status in statuses):
                group.status = "cancelled"
            elif statuses and all(status in {"completed", "degraded"} for status in statuses):
                group.status = "completed"
            elif any(status in {"completed", "degraded"} for status in statuses):
                group.status = "partially_completed"
            elif statuses and any(status == "waiting_confirmation" for status in statuses):
                group.status = "waiting_confirmation"
            else:
                group.status = "failed"
            costs: list[float] = []
            for task in db.scalars(select(AnalysisGroupTask).where(AnalysisGroupTask.group_id == group_id)):
                run = db.get(AnalysisRun, task.analysis_run_id)
                if run is not None:
                    costs.append(float(run.actual_cost_yuan or 0.0))
            group.actual_cost_yuan = sum(costs)
            group.completed_at = _utcnow()
            db.commit()

    async def _run_group() -> None:
        """带故障边界的组任务入口，避免异常让任务组永久卡在 running。"""
        try:
            await _run_group_body()
        except asyncio.CancelledError:
            try:
                with db_session_factory() as db:
                    group = db.get(AnalysisGroup, group_id)
                    if group is not None and group.status not in {"completed", "partially_completed", "failed", "cancelled"}:
                        group.status = "cancelled"
                        group.error_message = "任务组被取消。"
                        group.completed_at = _utcnow()
                        db.commit()
            except Exception:
                logger.exception("analysis group %d cancellation state update failed", group_id)
            raise
        except Exception as exc:  # pragma: no cover - defensive async boundary
            logger.exception("analysis group %d failed outside task worker", group_id)
            try:
                with db_session_factory() as db:
                    group = db.get(AnalysisGroup, group_id)
                    if group is not None:
                        group.status = "failed"
                        group.error_message = str(exc)[:2000]
                        group.completed_at = _utcnow()
                        db.commit()
            except Exception:
                logger.exception("analysis group %d failure state update failed", group_id)

    async def _enqueue() -> asyncio.Task:
        existing = _ACTIVE_GROUP_TASKS.get(int(group_id))
        if existing is not None and not existing.done():
            return existing
        task = asyncio.create_task(_run_group())
        _ACTIVE_GROUP_TASKS[int(group_id)] = task
        task.add_done_callback(lambda done, gid=int(group_id): _ACTIVE_GROUP_TASKS.pop(gid, None))
        return task

    main_loop = get_main_event_loop()
    current = asyncio.get_running_loop()
    if main_loop is not None and main_loop.is_running() and main_loop is not current:
        future = asyncio.run_coroutine_threadsafe(_enqueue(), main_loop)
        return await asyncio.wrap_future(future)
    return await _enqueue()


async def schedule_recovered_groups(db_session_factory: Callable) -> list[int]:
    """应用重启后恢复仍有 queued 子运行的任务组。"""
    scheduled: list[int] = []
    recoverable: list[int] = []
    with db_session_factory() as db:
        groups = list(
            db.scalars(
                select(AnalysisGroup)
                .where(AnalysisGroup.status.in_(["queued", "running"]))
                .order_by(AnalysisGroup.id)
            )
        )
        for group in groups:
            pending = db.scalar(
                select(AnalysisGroupTask.id).where(
                    AnalysisGroupTask.group_id == group.id,
                    AnalysisGroupTask.status.in_(["queued", "running"]),
                ).limit(1)
            )
            if pending is None:
                continue
            recoverable.append(group.id)
    for group_id in recoverable:
        await schedule_analysis_group(group_id, db_session_factory)
        scheduled.append(group_id)
    return scheduled
