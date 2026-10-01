from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session, selectinload

from ..models import ChangeLog, Class, Enrollment, Exam, ExamClassMetric, ExamDimensionScore, ExamScore, ScoreDimension, Student, WorkspaceState, ExamPaperVersion, ExamQuestion, StudentItemResult
from ..schemas.exams import (
    ExamClassMetricRead,
    ExamClassMetricUpsert,
    ExamCreate,
    ExamPatch,
    ExamScoreRead,
    ExamScoresUpsert,
    ExamSummary,
)


def get_exam(session: Session, exam_id: int, *, term_id: int | None = None, require_active: bool = True) -> Exam:
    exam = session.scalar(
        select(Exam)
        .options(selectinload(Exam.dimensions))
        .where(Exam.id == exam_id)
    )
    if not exam or (term_id is not None and exam.term_id != term_id) or (require_active and exam.status != "active"):
        raise HTTPException(404, "考试不存在")
    return exam


def list_exams(session: Session, *, term_id: int, include_archived: bool = False) -> list[Exam]:
    query = select(Exam).options(selectinload(Exam.dimensions)).where(Exam.term_id == term_id).order_by(Exam.exam_date.desc(), Exam.id.desc())
    if not include_archived:
        query = query.where(Exam.status == "active")
    return list(session.scalars(query).unique())


def create_exam(session: Session, payload: ExamCreate, *, term_id: int) -> Exam:
    exam = Exam(term_id=term_id, **payload.model_dump(exclude={"dimensions"}))
    exam.dimensions = [ScoreDimension(**item.model_dump()) for item in payload.dimensions]
    session.add(exam)
    session.flush()
    from .paper_distribution import ensure_exam_distribution
    ensure_exam_distribution(session, exam)
    session.add(ChangeLog(entity="exam", entity_id=str(exam.id), action="create", detail_json={"name": exam.name}))
    return exam


def update_exam(session: Session, exam_id: int, payload: ExamPatch, *, term_id: int) -> Exam:
    exam = get_exam(session, exam_id, term_id=term_id)
    values = payload.model_dump(exclude_unset=True)
    new_full_score = values.get("full_score", exam.full_score)
    highest = session.scalar(
        select(ExamScore.total_score)
        .where(ExamScore.exam_id == exam_id, ExamScore.total_score.is_not(None))
        .order_by(ExamScore.total_score.desc())
        .limit(1)
    )
    dimension_total = sum(item.max_score for item in exam.dimensions)
    if highest is not None and new_full_score < highest:
        raise HTTPException(422, "新满分不能低于已录入的最高成绩")
    if new_full_score < dimension_total:
        raise HTTPException(422, "新满分不能低于题型满分之和")
    next_lines = [values.get(key, getattr(exam, key)) for key in ("tier_a_cutoff", "tier_b_cutoff", "tier_c_cutoff")]
    if any(value is not None and value > new_full_score for value in next_lines):
        raise HTTPException(422, "分层线不能超过考试满分")
    if all(value is not None for value in next_lines) and not (next_lines[0] >= next_lines[1] >= next_lines[2]):
        raise HTTPException(422, "A层线应不低于B层线，B层线应不低于C层线")
    for key, value in values.items():
        setattr(exam, key, value.strip() if isinstance(value, str) else value)
    session.add(ChangeLog(entity="exam", entity_id=str(exam.id), action="update", detail_json=values))
    return exam


def _workspace_exam_mutation(
    session: Session,
    exam_id: int,
    *,
    term_id: int,
    source_key: str,
    expected_revision: int,
    action: str,
):
    exam = get_exam(session, exam_id, term_id=term_id, require_active=False)
    if exam.source_key != source_key:
        raise HTTPException(409, "考试标识已变化，请刷新后重试")
    workspace = session.get(WorkspaceState, term_id)
    if workspace is None:
        raise HTTPException(409, "工作台快照不存在，请先初始化工作台")
    state = deepcopy(workspace.state_json or {})
    active = list(state.get("exams") or [])
    archived = list(state.get("archivedExams") or [])
    key = str(source_key)

    def match(record):
        return str(record.get("id", record.get("source_key", ""))) == key

    active_item = next((item for item in active if match(item)), None)
    archived_item = next((item for item in archived if match(item)), None)
    if action == "archive":
        if exam.status != "active" or active_item is None:
            raise HTTPException(409, "考试已经归档")
        active.remove(active_item)
        active_item["status"] = "archived"
        archived.append(active_item)
        exam.status = "archived"
        exam.archived_at = datetime.now(timezone.utc)
    elif action == "restore":
        if exam.status != "archived" or archived_item is None:
            raise HTTPException(409, "考试未归档或快照不一致")
        archived.remove(archived_item)
        archived_item["status"] = "active"
        active.append(archived_item)
        exam.status = "active"
        exam.archived_at = None
    elif action == "purge":
        if exam.status != "archived" or archived_item is None:
            raise HTTPException(409, "只能永久清理已归档考试")
        archived.remove(archived_item)
        if str(state.get("currentExamId", "")) == key:
            state["currentExamId"] = str(active[0].get("id", "")) if active else ""
        session.delete(exam)
    else:
        raise ValueError(f"unsupported exam workspace action: {action}")

    if action != "purge" and str(state.get("currentExamId", "")) == key:
        state["currentExamId"] = str(active[0].get("id", "")) if active else ""
    state["exams"] = active
    state["archivedExams"] = archived
    result = session.execute(
        update(WorkspaceState)
        .where(WorkspaceState.term_id == term_id, WorkspaceState.revision == expected_revision)
        .values(state_json=state, revision=expected_revision + 1, updated_at=datetime.now(timezone.utc))
    )
    if result.rowcount != 1:
        session.rollback()
        raise HTTPException(409, "数据已被另一页面修改，请刷新后再操作")
    session.add(ChangeLog(
        entity="exam", entity_id=str(exam_id), action=action,
        detail_json={"term_id": term_id, "source_key": key, "previous_revision": expected_revision, "revision": expected_revision + 1},
    ))
    session.flush()
    return (None if action == "purge" else exam), state, expected_revision + 1


def archive_exam(session: Session, exam_id: int, *, term_id: int, source_key: str, expected_revision: int):
    return _workspace_exam_mutation(session, exam_id, term_id=term_id, source_key=source_key, expected_revision=expected_revision, action="archive")


def restore_exam(session: Session, exam_id: int, *, term_id: int, source_key: str, expected_revision: int):
    return _workspace_exam_mutation(session, exam_id, term_id=term_id, source_key=source_key, expected_revision=expected_revision, action="restore")


def purge_exam(session: Session, exam_id: int, *, term_id: int, source_key: str, expected_revision: int):
    return _workspace_exam_mutation(session, exam_id, term_id=term_id, source_key=source_key, expected_revision=expected_revision, action="purge")


def upsert_scores(session: Session, exam_id: int, payload: ExamScoresUpsert, *, term_id: int) -> list[ExamScore]:
    exam = get_exam(session, exam_id, term_id=term_id)
    dimension_map = {item.id: item for item in exam.dimensions}
    student_ids = [item.student_id for item in payload.items]
    enrollment_rows = session.execute(
        select(Student, Enrollment)
        .join(Enrollment, Enrollment.student_id == Student.id)
        .where(
            Student.id.in_(student_ids),
            Enrollment.term_id == exam.term_id,
            Enrollment.status == "active",
        )
    )
    students: dict[int, Student] = {}
    enrollments: dict[int, Enrollment] = {}
    for student, enrollment in enrollment_rows:
        students[student.id] = student
        enrollments[student.id] = enrollment
    missing = sorted(set(student_ids) - set(students))
    if missing:
        raise HTTPException(422, f"学生不存在或已归档：{missing}")

    existing = {
        item.student_id: item for item in session.scalars(
            select(ExamScore)
            .options(selectinload(ExamScore.dimension_scores))
            .where(ExamScore.exam_id == exam_id, ExamScore.student_id.in_(student_ids))
        ).unique()
    }
    changed: list[ExamScore] = []
    for item in payload.items:
        if item.total_score is not None and item.total_score > exam.full_score:
            raise HTTPException(422, f"学生 {students[item.student_id].student_no} 的总分超过考试满分")
        for part in item.dimension_scores:
            dimension = dimension_map.get(part.dimension_id)
            if not dimension:
                raise HTTPException(422, f"题型 {part.dimension_id} 不属于当前考试")
            if part.score > dimension.max_score:
                raise HTTPException(422, f"{dimension.name} 得分超过题型满分")

        score = existing.get(item.student_id)
        if score is None:
            score = ExamScore(exam_id=exam_id, student_id=item.student_id, class_id_at_exam=enrollments[item.student_id].class_id)
            session.add(score)
            session.flush()
        score.total_score = item.total_score
        score.grade_rank = item.grade_rank
        score.attendance_status = item.attendance_status
        score.note = item.note
        score.teacher_override = True
        score.override_note = item.note
        if score.class_id_at_exam is None:
            score.class_id_at_exam = enrollments[item.student_id].class_id
        current_parts = {part.dimension_id: part for part in score.dimension_scores}
        submitted_ids = {part.dimension_id for part in item.dimension_scores}
        for old in list(score.dimension_scores):
            if old.dimension_id not in submitted_ids:
                session.delete(old)
        for part in item.dimension_scores:
            record = current_parts.get(part.dimension_id)
            if record:
                record.score = part.score
            else:
                score.dimension_scores.append(ExamDimensionScore(dimension_id=part.dimension_id, score=part.score))
        changed.append(score)
    session.add(ChangeLog(entity="exam_scores", entity_id=str(exam_id), action="batch_upsert", detail_json={"count": len(changed)}))
    session.flush()
    return changed


def calculate_class_ranks(records: list[ExamScore], fallback_class_ids: dict[int, int] | None = None) -> dict[int, int]:
    """按学生所属班级计算 SQL RANK 风格的并列排名。"""
    grouped: dict[int, list[ExamScore]] = {}
    for item in records:
        if item.attendance_status == "present" and item.total_score is not None:
            class_id = item.class_id_at_exam or (fallback_class_ids or {}).get(item.student_id)
            if class_id is not None:
                grouped.setdefault(class_id, []).append(item)
    rank_by_id: dict[int, int] = {}
    for class_records in grouped.values():
        ranked = sorted(class_records, key=lambda item: item.total_score, reverse=True)
        previous_score = None
        current_rank = 0
        for index, item in enumerate(ranked, start=1):
            if item.total_score != previous_score:
                current_rank = index
                previous_score = item.total_score
            rank_by_id[item.id] = current_rank
    return rank_by_id


def score_rows(session: Session, exam_id: int, *, term_id: int | None = None, class_id: int | None = None) -> list[ExamScoreRead]:
    exam = get_exam(session, exam_id, term_id=term_id)
    query = (
        select(ExamScore)
        .options(
            selectinload(ExamScore.student).selectinload(Student.classroom),
            selectinload(ExamScore.dimension_scores).selectinload(ExamDimensionScore.dimension),
        )
        .where(ExamScore.exam_id == exam_id)
    )
    if class_id:
        query = query.where(ExamScore.class_id_at_exam == class_id)
    records = list(session.scalars(query).unique())
    student_ids = [item.student_id for item in records]
    enrollment_classes = {
        enrollment.student_id: enrollment.class_id
        for enrollment in session.scalars(select(Enrollment).where(
            Enrollment.term_id == exam.term_id,
            Enrollment.student_id.in_(student_ids),
        ))
    } if student_ids else {}
    rank_by_id = calculate_class_ranks(records, enrollment_classes)

    rows = []
    for item in sorted(records, key=lambda value: value.student.student_no):
        tier = None
        if item.total_score is not None and all(value is not None for value in (exam.tier_a_cutoff, exam.tier_b_cutoff, exam.tier_c_cutoff)):
            if item.total_score >= exam.tier_a_cutoff: tier = "A"
            elif item.total_score >= exam.tier_b_cutoff: tier = "B"
            elif item.total_score >= exam.tier_c_cutoff: tier = "C"
            else: tier = "D"
        historical_class_id = item.class_id_at_exam or enrollment_classes.get(item.student_id) or item.student.class_id
        historical_class = session.get(Class, historical_class_id)
        rows.append(ExamScoreRead(
            id=item.id,
            student_id=item.student_id,
            student_no=item.student.student_no,
            student_name=item.student.name,
            class_id=historical_class_id,
            class_name=historical_class.name if historical_class else "未分班",
            total_score=item.total_score,
            grade_rank=item.grade_rank,
            attendance_status=item.attendance_status,
            note=item.note,
            source_sync_run_id=item.source_sync_run_id,
            teacher_override=item.teacher_override,
            override_note=item.override_note,
            rank=rank_by_id.get(item.id),
            class_rank=item.class_rank or rank_by_id.get(item.id),
            tier=tier,
            dimension_scores=[{
                "dimension_id": part.dimension_id,
                "dimension_name": part.dimension.name,
                "score": part.score,
                "max_score": part.dimension.max_score,
            } for part in item.dimension_scores],
        ))
    return rows


def get_class_metric(session: Session, exam_id: int, class_id: int, *, term_id: int) -> ExamClassMetricRead:
    exam = get_exam(session, exam_id, term_id=term_id)
    classroom = session.get(Class, class_id)
    if not classroom or classroom.term_id != exam.term_id or classroom.status != "active":
        raise HTTPException(404, "班级不存在")
    item = session.scalar(select(ExamClassMetric).where(
        ExamClassMetric.exam_id == exam_id,
        ExamClassMetric.class_id == class_id,
    ))
    return ExamClassMetricRead(
        exam_id=exam_id,
        class_id=class_id,
        grade_rank=item.grade_rank if item else None,
    )


def upsert_class_metric(
    session: Session,
    exam_id: int,
    class_id: int,
    payload: ExamClassMetricUpsert,
    *,
    term_id: int,
) -> ExamClassMetricRead:
    exam = get_exam(session, exam_id, term_id=term_id)
    classroom = session.get(Class, class_id)
    if not classroom or classroom.term_id != exam.term_id or classroom.status != "active":
        raise HTTPException(404, "班级不存在")
    item = session.scalar(select(ExamClassMetric).where(
        ExamClassMetric.exam_id == exam_id,
        ExamClassMetric.class_id == class_id,
    ))
    if item is None:
        item = ExamClassMetric(exam_id=exam_id, class_id=class_id, grade_rank=payload.grade_rank)
        session.add(item)
    else:
        item.grade_rank = payload.grade_rank
    session.add(ChangeLog(
        entity="exam_class_metric",
        entity_id=f"{exam_id}:{class_id}",
        action="upsert",
        detail_json={"grade_rank": payload.grade_rank},
    ))
    session.flush()
    return ExamClassMetricRead(exam_id=exam_id, class_id=class_id, grade_rank=item.grade_rank)


def exam_summary(session: Session, exam_id: int, *, term_id: int, class_id: int | None = None) -> ExamSummary:
    exam = get_exam(session, exam_id, term_id=term_id)
    rows = score_rows(session, exam_id, term_id=term_id, class_id=class_id)
    present_rows = [item for item in rows if item.attendance_status == "present"]
    present = [item.total_score for item in present_rows if item.total_score is not None]
    absent = len([item for item in rows if item.attendance_status != "present"])
    return ExamSummary(
        exam_id=exam.id,
        exam_name=exam.name,
        full_score=exam.full_score,
        present_count=len(present),
        absent_count=absent,
        average=round(sum(present) / len(present), 2) if present else None,
        highest=max(present) if present else None,
        lowest=min(present) if present else None,
        missing_score_count=max(0, len(present_rows) - len(present)),
    )


def question_metrics(
    session: Session, exam_id: int, *, term_id: int, class_id: int | None = None,
    version: ExamPaperVersion | None = None,
) -> list[dict]:
    """按最新已确认试卷版本计算逐题确定性指标。

    缺考和没有得分的记录不进入平均分分母；缺失题目单独返回，避免被误当成 0 分。
    调用方可通过 ``version`` 显式指定试卷版本（如 Agent 工具的 confirmed 优先回退策略）。
    """
    exam = get_exam(session, exam_id, term_id=term_id)
    if version is None:
        version = session.scalar(select(ExamPaperVersion).where(
            ExamPaperVersion.exam_id == exam_id,
            ExamPaperVersion.status == "confirmed",
        ).order_by(ExamPaperVersion.version.desc()).limit(1))
    if version is None:
        return []
    participant_query = select(ExamScore.student_id).where(
        ExamScore.exam_id == exam_id,
        ExamScore.attendance_status == "present",
        ExamScore.total_score.is_not(None),
    )
    if class_id is not None:
        participant_query = participant_query.where(ExamScore.class_id_at_exam == class_id)
    participant_ids = set(session.scalars(participant_query))
    questions = list(session.scalars(select(ExamQuestion).where(
        ExamQuestion.paper_version_id == version.id,
    ).order_by(ExamQuestion.question_no)))
    result: list[dict] = []
    for question in questions:
        rows = list(session.scalars(select(StudentItemResult).where(
            StudentItemResult.exam_id == exam_id,
            StudentItemResult.question_id == question.id,
            StudentItemResult.student_id.in_(participant_ids) if participant_ids else False,
        )))
        scored = [row.score for row in rows if row.score is not None and row.attendance_status == "present"]
        average = round(sum(scored) / len(scored), 4) if scored else None
        result.append({
            "exam_id": exam_id,
            "question_id": question.id,
            "question_no": question.question_no,
            # 题型 + 题号 + 小题号三者构成小分表里的定位键；前端上传界面用它做骨架提示。
            "sub_question_no": question.sub_question_no,
            "section_name": question.section_name,
            "question_type": question.question_type,
            "max_score": question.max_score,
            "participant_count": len(participant_ids),
            "scored_count": len(scored),
            "missing_count": max(0, len(participant_ids) - len(scored)),
            "average_score": average,
            "score_rate": round(average / question.max_score, 4) if average is not None and question.max_score else None,
            "full_mark_count": sum(1 for value in scored if value >= question.max_score) if question.max_score > 0 else None,
            "full_mark_rate": round(sum(1 for value in scored if value >= question.max_score) / len(scored), 4) if scored and question.max_score > 0 else None,
        })
    return result


def _latest_confirmed_paper(session: Session, exam_id: int) -> ExamPaperVersion:
    from .paper_versions import select_paper_version

    version, _status = select_paper_version(session, exam_id)
    if version is None:
        raise HTTPException(404, "该考试还没有已确认的试卷结构")
    return version


def student_item_results(session: Session, exam_id: int, student_id: int, *, term_id: int) -> list[dict]:
    """返回最新已确认试卷的逐题成绩，并保留导入来源和教师覆盖状态。"""
    exam = get_exam(session, exam_id, term_id=term_id)
    student = session.scalar(
        select(Student)
        .join(Enrollment, Enrollment.student_id == Student.id)
        .where(Student.id == student_id, Enrollment.term_id == exam.term_id, Enrollment.status == "active")
    )
    if student is None:
        raise HTTPException(404, "学生不存在或不属于当前学期")
    version = _latest_confirmed_paper(session, exam.id)
    questions = list(session.scalars(
        select(ExamQuestion).where(ExamQuestion.paper_version_id == version.id).order_by(ExamQuestion.question_no)
    ))
    output: list[dict] = []
    for question in questions:
        item = session.scalar(select(StudentItemResult).where(
            StudentItemResult.exam_id == exam.id,
            StudentItemResult.student_id == student_id,
            StudentItemResult.question_id == question.id,
        ))
        output.append({
            "id": item.id if item else None,
            "question_id": question.id,
            "question_no": question.question_no,
            "sub_question_no": question.sub_question_no,
            "section_name": question.section_name,
            "question_type": question.question_type,
            "max_score": question.max_score,
            "difficulty_level": question.difficulty_level,
            "cognitive_level": question.cognitive_level,
            "knowledge_nodes": question.knowledge_nodes_json or [],
            "ability_nodes": question.ability_nodes_json or [],
            "pitfall_tags": question.pitfall_tags_json or [],
            "score": item.score if item else None,
            "score_rate": item.score_rate if item else None,
            "correct": item.correct if item else None,
            "selected_option": item.selected_option if item else None,
            "time_spent_ms": item.time_spent_ms if item else None,
            "modify_count": item.modify_count if item else None,
            "hesitation_time_ms": item.hesitation_time_ms if item else None,
            "teaching_blocks": item.teaching_blocks_json if item else question.teaching_blocks_json or [],
            "student_answer_text": item.student_answer_text if item else None,
            "attendance_status": item.attendance_status if item else "present",
            "source_sync_run_id": item.source_sync_run_id if item else None,
            "source_record_id": item.source_record_id if item else None,
            "source_cell": item.source_cell if item else None,
            "import_confidence": item.import_confidence if item else None,
            "teacher_override": bool(item.teacher_override) if item else False,
            "override_note": item.override_note if item else None,
        })
    return output


def override_student_item_result(
    session: Session,
    exam_id: int,
    student_id: int,
    question_id: int,
    payload,
    *,
    term_id: int,
) -> dict:
    """教师确认/修正单题分数；原始同步来源保留，覆盖只记录为新事实。"""
    exam = get_exam(session, exam_id, term_id=term_id)
    student = session.scalar(
        select(Student)
        .join(Enrollment, Enrollment.student_id == Student.id)
        .where(Student.id == student_id, Enrollment.term_id == exam.term_id, Enrollment.status == "active")
    )
    if student is None:
        raise HTTPException(404, "学生不存在或不属于当前学期")
    version = _latest_confirmed_paper(session, exam.id)
    question = session.scalar(select(ExamQuestion).where(
        ExamQuestion.id == question_id,
        ExamQuestion.paper_version_id == version.id,
    ))
    if question is None:
        raise HTTPException(404, "题目不存在或不属于当前试卷版本")
    if question.max_score > 0 and payload.score > question.max_score:
        raise HTTPException(422, "单题得分不能超过该题满分")
    item = session.scalar(select(StudentItemResult).where(
        StudentItemResult.exam_id == exam.id,
        StudentItemResult.student_id == student_id,
        StudentItemResult.question_id == question.id,
    ))
    if item is None:
        item = StudentItemResult(
            exam_id=exam.id,
            student_id=student_id,
            question_id=question.id,
            attendance_status="present",
        )
        session.add(item)
    item.score = payload.score
    # 派生字段同步重算：知识点分析优先用 score_rate，错因判定依赖 correct；
    # 教师把错题改为满分后，后续报告不得再把它当作错题或低得分题。
    item.score_rate = (
        round(payload.score / question.max_score, 4) if question.max_score else None
    )
    item.correct = payload.score >= question.max_score if question.max_score > 0 else None
    item.teacher_override = True
    item.override_note = payload.override_note
    session.add(ChangeLog(
        entity="student_item_result",
        entity_id=f"{exam.id}:{student_id}:{question_id}",
        action="teacher_override",
        detail_json={"score": payload.score, "override_note": payload.override_note},
    ))
    session.flush()
    rows = student_item_results(session, exam.id, student_id, term_id=term_id)
    return next(row for row in rows if row["question_id"] == question_id)


# 本接口写入的小分在 source_record_id 上留痕，用于区分「上次上传」与「教师单题修正」：
# 两者都带 teacher_override，但重传一份修正过的表不该被自己上一次的上传挡住。
ITEM_SCORE_SOURCE_PREFIX = "item-scores:"


def _item_score_lookup_keys(question: ExamQuestion) -> list[str]:
    """一道题可被引用的题型名。

    视觉识别把学科题型写进 ``question_type``，学校同步与试卷录入把卷面小节写进
    ``section_name``。教师手上的小分表两种写法都可能出现，所以两个字段都接受；
    这里只用于定位，不改写字段本身。
    """
    keys: list[str] = []
    for value in (question.question_type, question.section_name):
        from ..question_types import canonical_question_type
        text = canonical_question_type((value or "").strip())
        if text and text not in keys:
            keys.append(text)
    return keys


def _section_key(question: ExamQuestion) -> str:
    """题型分聚合键。与 ``school_sync`` / ``student_score_details`` 同一口径。"""
    return (question.section_name or question.question_type or "其他").strip() or "其他"


def bulk_upsert_item_scores(session: Session, exam_id: int, payload, *, term_id: int) -> dict:
    """按「题型 + 题号」批量写入逐题小分。

    写入口径与学校数据源同步（``services/school_sync.py``）一致：只有本次批次明确
    带到的题目才写分数，未出现在批次里的题目保留已有事实——不写空、不补零，也不把
    「本批次没取到」当成 0 分。

    刻意不做的三件事，都是为了让逐题小分保持唯一事实源：

    - **不推导总分**：批次可能只含部分题型，用不完整的小分合计覆盖总分等于编造事实；
      总分仍由 ``PUT /exams/{id}/scores`` 录入，两者不一致由 ``score-details`` 的
      ``total_reconciliation`` 如实报出。
    - **不写题型分**（``exam_dimension_scores``）：与单题修正接口
      ``override_student_item_result`` 同口径，题型分由读取侧按小分实时聚合
      （``student_score_details.section_scores``），避免同一事实出现第二份。
    - **不新建成绩记录**：该生在这场考试没有成绩记录时整批拒绝，避免上传动作顺手造出
      一条没有总分的考试记录。

    校验是整批的：任何一行定位不到、超满分、学生不在名单或缺考，都会整批取消并返回
    逐行原因，不会写一半。
    """
    from .paper_versions import select_paper_version

    exam = get_exam(session, exam_id, term_id=term_id)
    from .paper_distribution import ensure_exam_distribution, question_label
    ensure_exam_distribution(session, exam)
    version, paper_status = select_paper_version(
        session, exam.id, allow_draft=payload.include_draft
    )
    if version is None:
        raise HTTPException(404, "该考试还没有已确认的试卷结构，无法按题型写入小分")

    questions = list(session.scalars(
        select(ExamQuestion)
        .where(ExamQuestion.paper_version_id == version.id)
        .order_by(ExamQuestion.question_no, ExamQuestion.id)
    ))
    if not questions:
        raise HTTPException(404, "该试卷版本没有题目，无法按题型写入小分")

    question_by_id = {question.id: question for question in questions}
    candidates: dict[tuple[str, str], set[int]] = {}
    known_types: list[str] = []
    # 定位失败时要把「这个题型有哪些题号」回给教师，否则他们只能靠猜。
    numbers_by_type: dict[str, list[str]] = {}
    for question in questions:
        label = question.question_no.strip()
        sub_no = (question.sub_question_no or "").strip()
        if sub_no:
            label = f"{label}-{sub_no}"
        for key in _item_score_lookup_keys(question):
            candidates.setdefault((key, question.question_no.strip()), set()).add(question.id)
            if key not in known_types:
                known_types.append(key)
            numbers = numbers_by_type.setdefault(key, [])
            if label not in numbers:
                numbers.append(label)

    def locate(row) -> tuple[ExamQuestion | None, str]:
        from ..question_types import canonical_question_type
        kind = canonical_question_type((row.question_type or "").strip())
        if kind:
            ids = candidates.get((kind, row.question_no.strip())) or set()
        else:
            ids = {q.id for q in questions if row.question_no.strip() in (q.question_no.strip(), question_label(q))}
        if row.sub_question_no is not None:
            wanted = row.sub_question_no.strip()
            ids = {
                qid for qid in ids
                if (question_by_id[qid].sub_question_no or "").strip() == wanted
            }
        if not ids:
            return None, "question_not_found"
        if len(ids) > 1:
            return None, "question_ambiguous"
        return question_by_id[next(iter(ids))], ""

    # --- 学生：只认当前学期在册名单，未在名单内的整批拒绝 ---
    id_filter = sorted({row.student_id for row in payload.rows if row.student_id is not None})
    no_filter = sorted({
        (row.student_no or "").strip() for row in payload.rows if (row.student_no or "").strip()
    })
    conditions = []
    if id_filter:
        conditions.append(Student.id.in_(id_filter))
    if no_filter:
        conditions.append(Student.student_no.in_(no_filter))
    students_by_id: dict[int, Student] = {}
    if conditions:
        students_by_id = {
            student.id: student
            for student in session.scalars(
                select(Student)
                .join(Enrollment, Enrollment.student_id == Student.id)
                .where(
                    Enrollment.term_id == exam.term_id,
                    Enrollment.status == "active",
                    or_(*conditions),
                )
                .distinct()
            )
        }
    students_by_no = {student.student_no: student for student in students_by_id.values()}

    scores_by_student: dict[int, ExamScore] = {}
    if students_by_id:
        scores_by_student = {
            item.student_id: item
            for item in session.scalars(
                select(ExamScore).where(
                    ExamScore.exam_id == exam.id,
                    ExamScore.student_id.in_(list(students_by_id)),
                )
            )
        }

    # --- 第一阶段：全量校验，不落库 ---
    problems: list[dict] = []
    prepared: list[tuple] = []
    seen_resolved = set()
    for index, row in enumerate(payload.rows):
        report = {
            "index": index,
            "student_no": (row.student_no or "").strip() or None,
            "question_type": row.question_type,
            "question_no": row.question_no,
        }
        student = (
            students_by_id.get(row.student_id) if row.student_id is not None
            else students_by_no.get((row.student_no or "").strip())
        )
        if student is None:
            problems.append({**report, "reason": "student_not_found",
                             "message": "学生不存在或不在当前学期的在册名单里"})
            continue
        report["student_no"] = student.student_no
        question, reason = locate(row)
        if question is None:
            if reason == "question_ambiguous":
                message = "该题型 + 题号在当前试卷里对应多道小题，请补上小题号（sub_question_no）"
            else:
                kind = (row.question_type or "").strip()
                numbers = numbers_by_type.get(kind)
                message = (
                    f"「{kind}」在当前试卷里没有题号"
                    f"「{row.question_no.strip()}」；可用题号：" + "、".join(numbers[:40])
                    if numbers
                    else ("当前试卷里没有这个题型；可用题型：" + "、".join(known_types) if kind
                          else "该题号未配置，请在成绩管理的“考试设置”中填写题型分布")
                )
            problems.append({**report, "reason": reason, "message": message})
            continue
        if question.max_score > 0 and row.score > question.max_score:
            problems.append({**report, "reason": "score_exceeds_max",
                             "message": f"该题满分 {question.max_score}，提交 {row.score}"})
            continue
        score = scores_by_student.get(student.id)
        if score is None:
            problems.append({**report, "reason": "score_row_missing",
                             "message": "该生在这场考试还没有成绩记录，请先录入总分"})
            continue
        if score.attendance_status != "present":
            problems.append({**report, "reason": "attendance_not_present",
                             "message": "该生在这场考试是缺考或免考状态，不能写入小分"})
            continue
        identity = (student.id, question.id)
        if identity in seen_resolved:
            problems.append({**report, "reason": "duplicate_question", "message": "同一学生的同一道题重复提交"})
            continue
        seen_resolved.add(identity)
        prepared.append((student, question, score, row))

    if problems:
        raise HTTPException(422, {
            "code": "item_score_rows_invalid",
            "message": f"{len(problems)} 条小分无法写入，已整批取消，未改动任何数据",
            "problems": problems[:50],
        })

    # --- 第二阶段：写入。已存在的小分只在未被保护时覆盖 ---
    existing_items = {
        (item.student_id, item.question_id): item
        for item in session.scalars(
            select(StudentItemResult).where(
                StudentItemResult.exam_id == exam.id,
                StudentItemResult.student_id.in_(list(students_by_id)) if students_by_id else False,
            )
        )
    }
    provenance = f"{ITEM_SCORE_SOURCE_PREFIX}{version.version}"
    written = 0
    overwritten_overrides = 0
    touched_students: set[int] = set()
    touched_questions: set[int] = set()
    skipped: list[dict] = []
    for student, question, _score, row in prepared:
        item = existing_items.get((student.id, question.id))
        if item is None:
            item = StudentItemResult(
                exam_id=exam.id, student_id=student.id, question_id=question.id,
            )
            session.add(item)
            existing_items[(student.id, question.id)] = item
        elif item.teacher_override and not payload.overwrite_teacher_override:
            same_score = item.score is not None and abs(float(item.score) - float(row.score)) < 1e-9
            skipped_item = {
                "student_no": student.student_no,
                "question_type": row.question_type or _section_key(question),
                "question_no": row.question_no,
                "reason": ("previous_upload" if same_score and str(item.source_record_id or "").startswith(ITEM_SCORE_SOURCE_PREFIX)
                           else "score_changed" if str(item.source_record_id or "").startswith(ITEM_SCORE_SOURCE_PREFIX)
                           else "teacher_override"),
            }
            if skipped_item["reason"] == "score_changed":
                skipped_item.update(existing_score=item.score, incoming_score=row.score)
            skipped.append(skipped_item)
            continue
        elif item.teacher_override:
            overwritten_overrides += 1
        item.score = row.score
        # 派生字段与单题修正接口同口径：知识点分析优先用 score_rate，错因判定依赖 correct。
        item.score_rate = round(row.score / question.max_score, 4) if question.max_score else None
        item.correct = row.score >= question.max_score if question.max_score > 0 else None
        item.attendance_status = "present"
        item.teacher_override = True
        item.override_note = payload.note
        item.source_record_id = provenance
        written += 1
        touched_students.add(student.id)
        touched_questions.add(question.id)
    session.flush()

    # --- 回显：本次涉及题型的落库情况，以及仍然没有总分的学生数 ---
    sections: list[dict] = []
    touched_section_keys: list[str] = []
    for question in questions:
        if question.id in touched_questions:
            key = _section_key(question)
            if key not in touched_section_keys:
                touched_section_keys.append(key)
    for key in touched_section_keys:
        section_questions = [item for item in questions if _section_key(item) == key]
        question_ids = [item.id for item in section_questions]
        rows = list(session.scalars(select(StudentItemResult).where(
            StudentItemResult.exam_id == exam.id,
            StudentItemResult.student_id.in_(sorted(touched_students)),
            StudentItemResult.question_id.in_(question_ids),
        )))
        by_student: dict[int, list[float | None]] = {}
        for item in rows:
            by_student.setdefault(item.student_id, []).append(item.score)
        # 只有该题型全部题目都有小分的学生才计入平均：缺失不补零，否则平均分会被压低。
        complete_totals = [
            sum(value for value in values if value is not None)
            for values in by_student.values()
            if len(values) == len(section_questions) and all(value is not None for value in values)
        ]
        sections.append({
            "section_name": key,
            "max_score": sum(item.max_score for item in section_questions),
            "expected_items": len(section_questions) * len(touched_students),
            "scored_items": sum(1 for item in rows if item.score is not None),
            "complete_student_count": len(complete_totals),
            "average_score": round(sum(complete_totals) / len(complete_totals), 2) if complete_totals else None,
        })

    session.add(ChangeLog(
        entity="student_item_result",
        entity_id=str(exam.id),
        action="batch_upload",
        detail_json={
            "paper_version": version.version,
            "written": written,
            "student_count": len(touched_students),
            "question_types": touched_section_keys,
            "skipped": len(skipped),
            "overwritten_overrides": overwritten_overrides,
            "note": payload.note,
        },
    ))
    session.flush()
    return {
        "exam_id": exam.id,
        "paper_version": version.version,
        "paper_status": paper_status,
        "written": written,
        "student_count": len(touched_students),
        "overwritten_overrides": overwritten_overrides,
        "total_score_missing_count": sum(
            1 for student_id in touched_students
            if scores_by_student[student_id].total_score is None
        ),
        "question_types": touched_section_keys,
        "sections": sections,
        "skipped": skipped,
    }
