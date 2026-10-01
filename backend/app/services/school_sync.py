"""学校成绩同步核心服务。

这里故意不包含 MCP/HTTP 传输代码：任何外部来源先转换成 ``SchoolSyncPayload``，
后续的预览、校验、幂等写库和审计都复用本模块。
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import date, datetime, timezone
from typing import Any, Callable

from sqlalchemy import func, select, or_
from sqlalchemy.orm import Session

from ..models import (
    Class, Enrollment, Exam, ExamDimensionScore, ExamPaperVersion, ExamQuestion,
    ExamScore, ExternalEntityMapping, ScoreDimension, SchoolDataSource,
    SchoolSyncRun, Student, Term, StudentItemResult, WorkspaceState,
)
from ..schemas.school_sync import SchoolSyncPayload, StudentRosterPayload, SyncPreviewResponse


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _structure_hash(payload: SchoolSyncPayload) -> str:
    value = [
        {
            "id": q.external_id,
            "no": q.question_no,
            "section": q.section_name,
            "type": q.question_type,
            "max": q.max_score,
            "sub": q.sub_question_no,
            "difficulty": q.difficulty_level,
            "cognitive": q.cognitive_level,
            "knowledge": q.knowledge_nodes,
            "ability": q.ability_nodes,
            "pitfalls": q.pitfall_tags,
            "blocks": q.teaching_blocks,
        }
        for q in payload.questions
    ]
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def ensure_source(session: Session, *, source_key: str, name: str, kind: str = "mock", config: dict | None = None) -> SchoolDataSource:
    """登记导入来源，只补不覆盖。

    ``kind`` 与 ``config_json`` 决定一个自定义 MCP 数据源之后还能不能被同步
    （``school_sources.registry.resolve_source`` 按 kind 解析适配器），而导入路径
    只关心「这条数据来自谁」。因此已存在的登记行只更新显示名，**不改类型、不清配置**；
    否则第一次同步就会把教师填好的端点与字段映射冲掉。
    """
    source = session.scalar(select(SchoolDataSource).where(SchoolDataSource.source_key == source_key))
    if source is None:
        source = SchoolDataSource(source_key=source_key, name=name, kind=kind, config_json=config or {})
        session.add(source)
        session.flush()
        return source
    source.name = name
    return source


def mock_payload() -> SchoolSyncPayload:
    """提供一份可重复运行的示例班级成绩，供开发和验收使用。"""
    return SchoolSyncPayload.model_validate({
        "source_key": "mock-school",
        "source_name": "模拟学校成绩接口",
        "snapshot_id": "mock-2026-08-24-u6-u7",
        "term": {"external_id": "2025-2026-1", "code": "2025-1", "name": "2025学年第一学期"},
        "class_info": {"external_id": "grade9-class5", "name": "九年级5班", "grade": "九年级", "school_year": "2025"},
        "exam": {"external_id": "english-u6-u7", "name": "英语U6-7学科整理", "full_score": 100, "paper_revision": "v1"},
        "questions": [
            {"external_id": "q-1", "question_no": "1", "section_name": "听力", "question_type": "选择题", "max_score": 2, "correct_answer": "A"},
            {"external_id": "q-2", "question_no": "2", "section_name": "词汇", "question_type": "填空题", "max_score": 2},
            {"external_id": "q-3", "question_no": "3", "section_name": "阅读", "question_type": "阅读理解", "max_score": 6},
            {"external_id": "q-4", "question_no": "4", "section_name": "写作", "question_type": "书面表达", "max_score": 10},
        ],
        "students": [
            {"external_id": "student-10086", "student_no": "2025090501", "name": "张三", "gender": "男", "total_score": 17, "item_scores": [
                {"question_external_id": "q-1", "score": 2, "student_answer": "A"},
                {"question_external_id": "q-2", "score": 1}, {"question_external_id": "q-3", "score": 5}, {"question_external_id": "q-4", "score": 9},
            ]},
            {"external_id": "student-10087", "student_no": "2025090502", "name": "李四", "gender": "女", "total_score": 13, "item_scores": [
                {"question_external_id": "q-1", "score": 2}, {"question_external_id": "q-2", "score": 0}, {"question_external_id": "q-3", "score": 4}, {"question_external_id": "q-4", "score": 7},
            ]},
        ],
    })


def preview_payload(payload: SchoolSyncPayload) -> SyncPreviewResponse:
    warnings: list[str] = []
    errors: list[str] = []
    question_map = {q.external_id: q for q in payload.questions}
    if abs(sum(q.max_score for q in payload.questions) - payload.exam.full_score) > 1e-6:
        warnings.append("试卷题目满分之和与考试满分不一致，可能存在附加分或未同步题目")
    seen_nos: set[tuple[str, str]] = set()
    for q in payload.questions:
        question_key = (q.question_no, q.sub_question_no or "")
        if question_key in seen_nos:
            label = f"{q.question_no}-{q.sub_question_no}" if q.sub_question_no else q.question_no
            errors.append(f"题号重复：{label}")
        seen_nos.add(question_key)
    seen_students: set[str] = set()
    for student in payload.students:
        if student.student_no in seen_students:
            errors.append(f"学号重复：{student.student_no}")
        seen_students.add(student.student_no)
        scored = [item.score for item in student.item_scores if item.score is not None]
        for item in student.item_scores:
            question = question_map.get(item.question_external_id)
            if question is None:
                errors.append(f"{student.name} 引用了不存在的题目 {item.question_external_id}")
                continue
            if item.score is not None and item.score > question.max_score:
                errors.append(f"{student.name} 题目 {question.question_no} 得分超过满分")
        if student.total_score is not None and scored and abs(sum(scored) - student.total_score) > 1e-6:
            warnings.append(f"{student.name} 总分与逐题合计不一致：{student.total_score} vs {sum(scored):g}")
    return SyncPreviewResponse(
        source_key=payload.source_key,
        snapshot_id=payload.snapshot_id,
        counts={"classes": 1, "students": len(payload.students), "exams": 1, "questions": len(payload.questions), "item_scores": sum(len(s.item_scores) for s in payload.students)},
        warnings=warnings,
        errors=errors,
    )


def _mapping(session: Session, source_id: int, entity_type: str, external_id: str, local_id: int) -> None:
    item = session.scalar(select(ExternalEntityMapping).where(
        ExternalEntityMapping.source_id == source_id,
        ExternalEntityMapping.entity_type == entity_type,
        ExternalEntityMapping.external_id == external_id,
    ))
    if item is None:
        session.add(ExternalEntityMapping(source_id=source_id, entity_type=entity_type, external_id=external_id, local_id=local_id))
    else:
        item.local_id = local_id


def _mapped_entity(session: Session, source_id: int, entity_type: str, external_id: str, model):
    """优先按外部系统稳定 ID 找本地实体，避免依赖姓名/班级名称匹配。"""
    mapping = session.scalar(select(ExternalEntityMapping).where(
        ExternalEntityMapping.source_id == source_id,
        ExternalEntityMapping.entity_type == entity_type,
        ExternalEntityMapping.external_id == external_id,
    ))
    return session.get(model, mapping.local_id) if mapping else None


def _compat_workspace(session: Session, term_id: int) -> tuple[WorkspaceState, dict[str, Any]]:
    """Return the legacy WorkBench snapshot that the current UI still renders."""
    workspace = session.get(WorkspaceState, term_id)
    if workspace is None:
        workspace = WorkspaceState(term_id=term_id, state_json={"classes": [], "students": [], "exams": []}, revision=1)
        session.add(workspace)
        session.flush()
    # JSON 字段必须深复制；浅复制会让嵌套 scores 的修改绕过 ORM 的变更检测。
    state = deepcopy(workspace.state_json or {})
    state.setdefault("classes", [])
    state.setdefault("students", [])
    state.setdefault("exams", [])
    return workspace, state


def _sync_compat_roster(
    session: Session,
    term_id: int,
    class_map: dict[str, Class],
    students: list[Any],
    *,
    replace_moni_snapshot: bool = False,
    managed_class_names: set[str] | None = None,
) -> None:
    """Mirror normalized roster rows into the snapshot used by WorkBench pages."""
    workspace, state = _compat_workspace(session, term_id)
    class_names = [item.name for item in class_map.values()]
    existing_classes = [str(item).strip() for item in state.get("classes", []) if str(item).strip()]
    if replace_moni_snapshot:
        managed_names = managed_class_names or set()
        preserved_classes = [name for name in existing_classes if name not in managed_names]
        state["classes"] = list(dict.fromkeys(preserved_classes + class_names))
    else:
        state["classes"] = list(dict.fromkeys(existing_classes + class_names))

    existing_students = [
        item for item in state.get("students", [])
        if isinstance(item, dict) and item.get("id")
    ]
    archived_students = state.setdefault("archivedStudents", [])
    if replace_moni_snapshot:
        managed_names = managed_class_names or set()
        incoming_ids = {str(item.student_no) for item in students}
        retained_students = []
        for item in existing_students:
            if str(item.get("class", "")) in managed_names and str(item.get("id")) not in incoming_ids:
                archived = dict(item)
                archived["status"] = "archived"
                archived.setdefault("archivedAt", _now().isoformat())
                if not any(str(old.get("id")) == str(archived.get("id")) for old in archived_students if isinstance(old, dict)):
                    archived_students.append(archived)
            else:
                retained_students.append(item)
        by_id = {str(item.get("id")): item for item in retained_students}
    else:
        by_id = {str(item.get("id")): item for item in existing_students}
    class_by_external = {str(key): value.name for key, value in class_map.items()}
    for incoming in students:
        record = dict(by_id.get(str(incoming.student_no), {}))
        class_external_id = getattr(incoming, "class_external_id", next(iter(class_map), ""))
        record.update({
            "id": incoming.student_no,
            "name": incoming.name,
            "class": class_by_external.get(str(class_external_id), ""),
            "status": "active" if incoming.status != "inactive" else "archived",
        })
        by_id[str(incoming.student_no)] = record
    state["students"] = list(by_id.values())
    workspace.state_json = state
    workspace.revision += 1


def _reconcile_moni_roster(
    session: Session,
    source: SchoolDataSource,
    term: Term,
    class_map: dict[str, Class],
    seen_student_ids: set[int],
) -> tuple[int, int]:
    """Converge the current MONI term to the latest authorized roster snapshot.

    MONI-managed classes are identified by their external class mapping. This
    leaves manually-created local classes untouched while archiving students
    and classes that disappeared from the current MONI snapshot.
    """
    managed_classes = list(session.scalars(
        select(Class)
        .join(ExternalEntityMapping, ExternalEntityMapping.local_id == Class.id)
        .where(
            ExternalEntityMapping.source_id == source.id,
            ExternalEntityMapping.entity_type == "class",
            Class.term_id == term.id,
        )
    ))
    managed_classes_by_id = {item.id: item for item in managed_classes}
    current_class_ids = {item.id for item in class_map.values()}
    managed_class_ids = set(managed_classes_by_id) | current_class_ids
    archived_students = 0
    if managed_class_ids:
        stale_enrollments = session.scalars(
            select(Enrollment).where(
                Enrollment.term_id == term.id,
                Enrollment.class_id.in_(managed_class_ids),
                Enrollment.status == "active",
                ~Enrollment.student_id.in_(seen_student_ids),
            )
        ).all()
        for enrollment in stale_enrollments:
            enrollment.status = "archived"
            enrollment.left_on = enrollment.left_on or date.today()
            archived_students += 1

    archived_classes = 0
    for classroom in managed_classes_by_id.values():
        if classroom.id not in current_class_ids and classroom.status != "archived":
            classroom.status = "archived"
            archived_classes += 1
    return archived_students, archived_classes


def _sync_compat_exam(session: Session, term_id: int, classroom: Class, exam: Exam, payload: SchoolSyncPayload) -> None:
    """Mirror imported exam facts into the legacy score page snapshot."""
    workspace, state = _compat_workspace(session, term_id)
    exam_key = f"moni:{payload.exam.external_id}"
    exams = [item for item in state.get("exams", []) if isinstance(item, dict)]
    record = next((item for item in exams if str(item.get("externalId") or item.get("id")) == payload.exam.external_id), None)
    if record is None:
        record = {"id": exam_key, "externalId": payload.exam.external_id, "scores": {}}
        exams.append(record)
    record.update({
        "name": payload.exam.name,
        "date": payload.exam.exam_date.isoformat() if payload.exam.exam_date else "",
        "fullScore": payload.exam.full_score,
        "examKind": payload.exam.exam_kind,
        "type": "english_total",
        "tierLines": {
            "a": exam.tier_a_cutoff,
            "b": exam.tier_b_cutoff,
            "c": exam.tier_c_cutoff,
        } if all(value is not None for value in (
            exam.tier_a_cutoff,
            exam.tier_b_cutoff,
            exam.tier_c_cutoff,
        )) else None,
    })
    scores = record.setdefault("scores", {})
    student_by_id = {str(item.get("id")): item for item in state.get("students", []) if isinstance(item, dict) and item.get("id")}
    for incoming in payload.students:
        score = incoming.total_score
        scores[str(incoming.student_no)] = {
            "英语": score if score is not None else "",
            "gradeRank": incoming.grade_rank or "",
            "classRank": incoming.class_rank or "",
            "attendanceStatus": "absent" if incoming.status == "absent" else "present",
            "classAtExam": classroom.name,
        }
        if str(incoming.student_no) in student_by_id and score is not None:
            student_by_id[str(incoming.student_no)]["english"] = score
    # 用规范化成绩表回填快照，覆盖分班同步时的部分批次，确保两 个班级
    # 都能在成绩页看到总分和班级/年级排名。
    normalized_rows = session.execute(
        select(ExamScore, Student).join(Student, Student.id == ExamScore.student_id).where(ExamScore.exam_id == exam.id)
    ).all()
    for score_row, student in normalized_rows:
        scores[str(student.student_no)] = {
            "英语": score_row.total_score if score_row.total_score is not None else "",
            "gradeRank": score_row.grade_rank or "",
            "classRank": score_row.class_rank or "",
            "attendanceStatus": score_row.attendance_status,
            "classAtExam": session.get(Class, score_row.class_id_at_exam).name if score_row.class_id_at_exam and session.get(Class, score_row.class_id_at_exam) else "",
        }
        if str(student.student_no) in student_by_id and score_row.total_score is not None:
            student_by_id[str(student.student_no)]["english"] = score_row.total_score
    state["exams"] = exams
    if not state.get("currentExamId"):
        state["currentExamId"] = exam_key
    workspace.state_json = state
    workspace.revision += 1


def rebuild_compat_exam_snapshots(session: Session, term_external_id: str) -> None:
    """After a multi-class import, rebuild every exam snapshot from normalized rows.

    Each class payload is committed independently. Rebuilding once at the end
    prevents the legacy score page from retaining only the last class batch.
    """
    term = session.scalar(select(Term).where(Term.code == term_external_id))
    if term is None:
        return
    workspace, state = _compat_workspace(session, term.id)
    records = [item for item in state.get("exams", []) if isinstance(item, dict)]
    for record in records:
        external_id = str(record.get("externalId") or "")
        if not external_id:
            continue
        exam = session.scalar(select(Exam).where(Exam.term_id == term.id, Exam.source_key == external_id))
        if exam is None:
            continue
        scores: dict[str, dict[str, Any]] = {}
        rows = session.execute(
            select(ExamScore, Student).join(Student, Student.id == ExamScore.student_id).where(ExamScore.exam_id == exam.id)
        ).all()
        for score_row, student in rows:
            classroom = session.get(Class, score_row.class_id_at_exam) if score_row.class_id_at_exam else None
            scores[str(student.student_no)] = {
                "英语": score_row.total_score if score_row.total_score is not None else "",
                "gradeRank": score_row.grade_rank or "",
                "classRank": score_row.class_rank or "",
                "attendanceStatus": score_row.attendance_status,
                "classAtExam": classroom.name if classroom else "",
            }
        record["scores"] = scores
        tier_lines = {
            "a": exam.tier_a_cutoff,
            "b": exam.tier_b_cutoff,
            "c": exam.tier_c_cutoff,
        }
        record["tierLines"] = tier_lines if all(value is not None for value in tier_lines.values()) else None
    state["exams"] = records
    workspace.state_json = state
    workspace.revision += 1


def apply_payload(
    session: Session,
    payload: SchoolSyncPayload,
    *,
    progress_callback: Callable[..., Any] | None = None,
) -> tuple[SchoolSyncRun, dict[str, Any]]:
    if payload.source_key == "moni":
        from .subjects import get_selected_subject
        if get_selected_subject(session).key != "english":
            raise ValueError("MONI 当前只支持英语工作区")
    preview = preview_payload(payload)
    if preview.errors:
        raise ValueError("同步数据未通过校验：" + "；".join(preview.errors))

    total_students = len(payload.students)
    total_items = sum(len(student.item_scores) for student in payload.students)

    def emit(progress: float, *, phase: str, phase_label: str, message: str, processed: int = 0, items_written: int = 0, conflicts: int = 0) -> None:
        if progress_callback is None:
            return
        # 后台进度必须在独立事务中可见；同步本身按学生粒度提交，失败时可明确展示部分成功。
        session.commit()
        progress_callback(
            progress,
            checkpoint={
                "phase": phase,
                "phase_label": phase_label,
                "message": message,
                "processed": processed,
                "total": total_students,
                "items_written": items_written,
                "total_items": total_items,
                "conflicts": conflicts,
            },
            phase=phase,
            phase_label=phase_label,
            message=message,
        )

    emit(0.02, phase="validating", phase_label="校验同步数据", message="正在检查学生、题目和成绩引用…")

    source = ensure_source(session, source_key=payload.source_key, name=payload.source_name)
    session.commit()
    run = SchoolSyncRun(source_id=source.id, snapshot_id=payload.snapshot_id, status="running", scope_json={"term": payload.term.external_id, "class": payload.class_info.external_id, "exam": payload.exam.external_id}, summary_json={})
    session.add(run)
    session.commit()
    try:
        term = _mapped_entity(session, source.id, "term", payload.term.external_id, Term)
        if term is None:
            term = session.scalar(select(Term).where(Term.code == (payload.term.code or payload.term.external_id)))
        if term is None:
            term = Term(code=payload.term.code or payload.term.external_id, name=payload.term.name, status="active")
            session.add(term)
            session.flush()
        _mapping(session, source.id, "term", payload.term.external_id, term.id)

        classroom = _mapped_entity(session, source.id, "class", payload.class_info.external_id, Class)
        if classroom is not None and classroom.term_id != term.id:
            classroom = None
        if classroom is None:
            classroom = session.scalar(select(Class).where(Class.term_id == term.id, Class.name == payload.class_info.name))
        if classroom is None:
            classroom = Class(term_id=term.id, name=payload.class_info.name, grade=payload.class_info.grade, school_year=payload.class_info.school_year)
            session.add(classroom)
            session.flush()
        else:
            classroom.name = payload.class_info.name
            classroom.grade = payload.class_info.grade
            classroom.school_year = payload.class_info.school_year
        _mapping(session, source.id, "class", payload.class_info.external_id, classroom.id)

        exam = _mapped_entity(session, source.id, "exam", payload.exam.external_id, Exam)
        if exam is not None and exam.term_id != term.id:
            exam = None
        if exam is None:
            exam = session.scalar(select(Exam).where(Exam.term_id == term.id, Exam.source_key == payload.exam.external_id))
        if exam is None:
            exam = Exam(term_id=term.id, source_key=payload.exam.external_id, name=payload.exam.name, exam_date=payload.exam.exam_date, full_score=payload.exam.full_score, exam_kind=payload.exam.exam_kind)
            session.add(exam)
            session.flush()
        else:
            exam.name = payload.exam.name
            exam.exam_date = payload.exam.exam_date
            exam.full_score = payload.exam.full_score
        # MONI 首次导入时自动填充分层线；已有值视为老师确认/调整过的配置，
        # 后续增量同步不覆盖，避免打开考试设置后被接口数据改回去。
        for field in ("tier_a_cutoff", "tier_b_cutoff", "tier_c_cutoff"):
            incoming = getattr(payload.exam, field)
            if getattr(exam, field) is None and incoming is not None:
                setattr(exam, field, incoming)
        _mapping(session, source.id, "exam", payload.exam.external_id, exam.id)

        structure_hash = _structure_hash(payload)
        version = session.scalar(select(ExamPaperVersion).where(ExamPaperVersion.exam_id == exam.id, ExamPaperVersion.structure_hash == structure_hash).order_by(ExamPaperVersion.version.desc()).limit(1))
        if version is None:
            max_version = session.scalar(select(func.max(ExamPaperVersion.version)).where(ExamPaperVersion.exam_id == exam.id)) or 0
            version = ExamPaperVersion(exam_id=exam.id, version=max_version + 1, status="confirmed", full_score=payload.exam.full_score, extraction_provider="school_sync", external_revision=payload.exam.paper_revision, structure_hash=structure_hash, source_sync_run_id=run.id, confirmed_at=_now())
            session.add(version)
            session.flush()
        question_by_external: dict[str, ExamQuestion] = {}
        for q in payload.questions:
            question = session.scalar(select(ExamQuestion).where(ExamQuestion.paper_version_id == version.id, ExamQuestion.external_id == q.external_id))
            if question is None:
                question = ExamQuestion(paper_version_id=version.id, external_id=q.external_id, question_no=q.question_no, sub_question_no=q.sub_question_no, section_name=q.section_name, question_type=q.question_type, content_text=q.content_text, max_score=q.max_score, correct_answer_json={"value": q.correct_answer} if q.correct_answer is not None else {}, difficulty_level=q.difficulty_level, cognitive_level=q.cognitive_level, knowledge_nodes_json=q.knowledge_nodes, ability_nodes_json=q.ability_nodes, pitfall_tags_json=q.pitfall_tags, teaching_blocks_json=q.teaching_blocks)
                session.add(question)
                session.flush()
            else:
                question.question_no = q.question_no
                question.sub_question_no = q.sub_question_no
                question.section_name = q.section_name
                question.question_type = q.question_type
                question.content_text = q.content_text
                question.max_score = q.max_score
                question.difficulty_level = q.difficulty_level
                question.cognitive_level = q.cognitive_level
                question.knowledge_nodes_json = q.knowledge_nodes
                question.ability_nodes_json = q.ability_nodes
                question.pitfall_tags_json = q.pitfall_tags
                question.teaching_blocks_json = q.teaching_blocks
            question_by_external[q.external_id] = question
            _mapping(session, source.id, "question", q.external_id, question.id)

        dimension_by_name: dict[str, ScoreDimension] = {}
        for name, max_score in _dimension_totals(payload).items():
            code = "sync_" + hashlib.sha1(name.encode()).hexdigest()[:12]
            dimension = session.scalar(select(ScoreDimension).where(ScoreDimension.exam_id == exam.id,
                or_(ScoreDimension.code == code, ScoreDimension.name == name)))
            if dimension is None:
                dimension = ScoreDimension(exam_id=exam.id, code=code, name=name, max_score=max_score, position=len(dimension_by_name))
                session.add(dimension)
                session.flush()
            dimension.max_score = max_score
            dimension_by_name[name] = dimension

        changed_students = 0
        changed_items = 0
        conflicts = 0
        for incoming in payload.students:
            student = _mapped_entity(session, source.id, "student", incoming.external_id, Student)
            if student is None:
                student = session.scalar(select(Student).where(Student.student_no == incoming.student_no))
            if student is None:
                student = Student(student_no=incoming.student_no, name=incoming.name, gender=incoming.gender, status="active")
                session.add(student)
                session.flush()
            else:
                student.name = incoming.name
                student.gender = incoming.gender
                student.status = "active" if incoming.status in {"active", "absent", "excused"} else "archived"
            enrollment = session.scalar(select(Enrollment).where(Enrollment.term_id == term.id, Enrollment.student_id == student.id))
            if enrollment is None:
                enrollment = Enrollment(term_id=term.id, class_id=classroom.id, student_id=student.id, status="active")
                session.add(enrollment)
            else:
                enrollment.class_id = classroom.id
                enrollment.status = "active" if incoming.status != "inactive" else "archived"
            student.class_id = classroom.id
            _mapping(session, source.id, "student", incoming.external_id, student.id)

            attendance = incoming.status if incoming.status in {"absent", "excused"} else "present"
            item_values = {item.question_external_id: item for item in incoming.item_scores}
            total_score = incoming.total_score
            # 没有单科总分、也没有逐题成绩时，保持 NULL（前端显示“—”）。
            # 不能把“接口未提供成绩”误写成 0 分。
            if total_score is None and attendance == "present" and item_values:
                total_score = sum(item.score or 0 for item in incoming.item_scores)
            score = session.scalar(select(ExamScore).where(ExamScore.exam_id == exam.id, ExamScore.student_id == student.id))
            if score is None:
                score = ExamScore(exam_id=exam.id, student_id=student.id, class_id_at_exam=classroom.id)
                session.add(score)
            score.source_sync_run_id = run.id
            if score.teacher_override:
                conflicts += 1
            else:
                score.class_id_at_exam = classroom.id
                if attendance != "present":
                    # 缺考/请假是明确的出勤事实：清空成绩与排名。
                    score.total_score = None
                    score.class_rank = None
                    score.grade_rank = None
                    score.global_rank = None
                else:
                    # 只有本次批次确实提供（或可由逐题成绩推导）总分时才覆盖，
                    # 避免不完整批次把已有成绩覆盖为空。
                    if total_score is not None or score.total_score is None:
                        score.total_score = total_score
                    if incoming.class_rank is not None or score.class_rank is None:
                        score.class_rank = incoming.class_rank
                    if incoming.grade_rank is not None or score.grade_rank is None:
                        score.grade_rank = incoming.grade_rank
                    if incoming.global_rank is not None or score.global_rank is None:
                        score.global_rank = incoming.global_rank
                score.attendance_status = attendance
            session.flush()
            for q_ext, question in question_by_external.items():
                incoming_item = item_values.get(q_ext)
                item = session.scalar(select(StudentItemResult).where(StudentItemResult.exam_id == exam.id, StudentItemResult.student_id == student.id, StudentItemResult.question_id == question.id))
                if item is None:
                    item = StudentItemResult(exam_id=exam.id, student_id=student.id, question_id=question.id)
                    session.add(item)
                item.source_sync_run_id = run.id
                item.source_record_id = incoming.external_id + ":" + q_ext
                if item.teacher_override:
                    conflicts += 1
                    continue
                item.attendance_status = attendance
                if attendance != "present":
                    # 缺考/请假：该题分数与作答明确清空。
                    item.score = None
                    item.score_rate = None
                    item.correct = None
                    item.selected_option = None
                    item.student_answer_text = None
                    item.time_spent_ms = None
                    item.modify_count = None
                    item.hesitation_time_ms = None
                    item.teaching_blocks_json = []
                    item.pitfall_tags_json = []
                    changed_items += 1
                elif incoming_item is not None:
                    # 本次批次确实带到了这道题的小分：写入并同步派生字段。
                    item.score = incoming_item.score
                    item.student_answer_text = incoming_item.student_answer
                    item.score_rate = incoming_item.score_rate
                    item.correct = incoming_item.correct
                    item.selected_option = incoming_item.selected_option
                    item.time_spent_ms = incoming_item.time_spent_ms
                    item.modify_count = incoming_item.modify_count
                    item.hesitation_time_ms = incoming_item.hesitation_time_ms
                    item.teaching_blocks_json = incoming_item.teaching_blocks
                    item.pitfall_tags_json = incoming_item.pitfall_tags
                    changed_items += 1
                # 学生到场但本次批次没取到该题（incoming_item 为 None）：保留已有
                # 小分事实，不写空，也不计入 changed_items。区分“明确清空”与“未取到”。
            for name, dimension in dimension_by_name.items():
                if score.teacher_override:
                    continue
                from ..question_types import canonical_question_type
                dim_score = sum((item_values[q.external_id].score or 0) for q in payload.questions if canonical_question_type(q.section_name or q.question_type or "其他") == name and q.external_id in item_values)
                record = session.scalar(select(ExamDimensionScore).where(ExamDimensionScore.exam_score_id == score.id, ExamDimensionScore.dimension_id == dimension.id))
                if record is None:
                    record = ExamDimensionScore(exam_score_id=score.id, dimension_id=dimension.id, score=dim_score)
                    session.add(record)
                else:
                    record.score = dim_score
            changed_students += 1
            emit(
                min(0.95, changed_students / max(total_students, 1) * 0.9 + 0.05),
                phase="writing",
                phase_label="写入成绩数据",
                message=f"已处理 {changed_students}/{total_students} 名学生，逐题成绩 {changed_items}/{total_items}",
                processed=changed_students,
                items_written=changed_items,
                conflicts=conflicts,
            )

        # 兼容当前 WorkBench 页面仍使用的 workspace snapshot；规范化表和
        # 快照同时更新，避免“同步成功但学生/成绩页面为空”。
        _sync_compat_roster(session, term.id, {payload.class_info.external_id: classroom}, payload.students)
        _sync_compat_exam(session, term.id, classroom, exam, payload)
        from .growth.exam_events import sync_exam_growth_events
        sync_exam_growth_events(session, term_id=term.id)
        summary = {**preview.counts, "students_written": changed_students, "items_written": changed_items, "conflicts": conflicts, "paper_version": version.version, "warnings": preview.warnings}
        run.status = "completed"
        run.summary_json = summary
        run.completed_at = _now()
        source.last_sync_at = _now()
        session.commit()
        if progress_callback is not None:
            progress_callback(
                1.0,
                checkpoint={
                    "phase": "completed",
                    "phase_label": "同步完成",
                    "message": "数据已写入本地数据库。",
                    "processed": changed_students,
                    "total": total_students,
                    "items_written": changed_items,
                    "total_items": total_items,
                    "conflicts": conflicts,
                    "summary": summary,
                },
                phase="completed",
                phase_label="同步完成",
                message="数据已写入本地数据库。",
            )
        return run, summary
    except Exception as exc:
        session.rollback()
        failed = session.get(SchoolSyncRun, run.id)
        if failed is not None:
            failed.status = "failed"
            failed.error_message = str(exc)[:2000]
            failed.completed_at = _now()
            session.commit()
        raise


def _dimension_totals(payload: SchoolSyncPayload) -> dict[str, float]:
    from ..question_types import canonical_question_type
    result: dict[str, float] = {}
    for question in payload.questions:
        name = canonical_question_type(question.section_name or question.question_type or "其他")
        result[name] = result.get(name, 0.0) + question.max_score
    return result


def apply_student_roster(session: Session, payload: StudentRosterPayload) -> dict[str, Any]:
    """幂等写入外部学生名册；MONI 当前快照会归档已离开名单的记录。"""
    if payload.source_key == "moni":
        from .subjects import get_selected_subject
        if get_selected_subject(session).key != "english":
            raise ValueError("MONI 当前只支持英语工作区")
    source = ensure_source(
        session,
        source_key=payload.source_key,
        name=payload.source_name,
        kind="mcp",
        config={"transport": "mcp", "snapshot_id": payload.snapshot_id},
    )
    run = SchoolSyncRun(
        source_id=source.id,
        snapshot_id=payload.snapshot_id,
        status="running",
        scope_json={"term": payload.term.external_id, "classes": [item.external_id for item in payload.classes]},
        summary_json={},
    )
    session.add(run)
    session.flush()
    try:
        term = _mapped_entity(session, source.id, "term", payload.term.external_id, Term)
        if term is None:
            term = session.scalar(select(Term).where(Term.code == (payload.term.code or payload.term.external_id)))
        if term is None:
            term = Term(code=payload.term.code or payload.term.external_id, name=payload.term.name, status="active")
            session.add(term)
            session.flush()
        else:
            term.name = payload.term.name
            term.status = "active"
        _mapping(session, source.id, "term", payload.term.external_id, term.id)

        class_map: dict[str, Class] = {}
        for incoming in payload.classes:
            classroom = _mapped_entity(session, source.id, "class", incoming.external_id, Class)
            if classroom is not None and classroom.term_id != term.id:
                classroom = None
            if classroom is None:
                classroom = session.scalar(select(Class).where(Class.term_id == term.id, Class.name == incoming.name))
            if classroom is None:
                classroom = Class(term_id=term.id, name=incoming.name, grade=incoming.grade, school_year=incoming.school_year)
                session.add(classroom)
                session.flush()
            else:
                classroom.name = incoming.name
                classroom.grade = incoming.grade
                classroom.school_year = incoming.school_year
                classroom.status = "active"
            class_map[incoming.external_id] = classroom
            _mapping(session, source.id, "class", incoming.external_id, classroom.id)

        changed = 0
        seen_student_ids: set[int] = set()
        for incoming in payload.students:
            classroom = class_map[incoming.class_external_id]
            student = _mapped_entity(session, source.id, "student", incoming.external_id, Student)
            if student is None:
                student = session.scalar(select(Student).where(Student.student_no == incoming.student_no))
            if student is None:
                student = Student(student_no=incoming.student_no, name=incoming.name, gender=incoming.gender, status="active")
                session.add(student)
                session.flush()
            else:
                student.student_no = incoming.student_no
                student.name = incoming.name
                student.gender = incoming.gender
                student.status = "active" if incoming.status != "inactive" else "archived"
            student.class_id = classroom.id
            seen_student_ids.add(student.id)
            enrollment = session.scalar(select(Enrollment).where(Enrollment.term_id == term.id, Enrollment.student_id == student.id))
            if enrollment is None:
                enrollment = Enrollment(term_id=term.id, class_id=classroom.id, student_id=student.id, status="active")
                session.add(enrollment)
            else:
                enrollment.class_id = classroom.id
                enrollment.status = "active" if incoming.status != "inactive" else "archived"
            _mapping(session, source.id, "student", incoming.external_id, student.id)
            changed += 1

        archived_students = 0
        archived_classes = 0
        if payload.source_key == "moni":
            archived_students, archived_classes = _reconcile_moni_roster(
                session, source, term, class_map, seen_student_ids,
            )
        managed_class_names = {
            classroom.name for classroom in session.scalars(
                select(Class)
                .join(ExternalEntityMapping, ExternalEntityMapping.local_id == Class.id)
                .where(
                    ExternalEntityMapping.source_id == source.id,
                    ExternalEntityMapping.entity_type == "class",
                    Class.term_id == term.id,
                )
            )
        } if payload.source_key == "moni" else None
        _sync_compat_roster(
            session,
            term.id,
            class_map,
            payload.students,
            replace_moni_snapshot=payload.source_key == "moni",
            managed_class_names=managed_class_names,
        )
        summary = {
            "term_id": term.id,
            "classes": len(class_map),
            "students": len(payload.students),
            "students_written": changed,
            "archived_students": archived_students,
            "archived_classes": archived_classes,
        }
        run.status = "completed"
        run.summary_json = summary
        run.completed_at = _now()
        source.last_sync_at = _now()
        session.commit()
        return summary
    except Exception as exc:
        session.rollback()
        failed = session.get(SchoolSyncRun, run.id)
        if failed is not None:
            failed.status = "failed"
            failed.error_message = str(exc)[:2000]
            failed.completed_at = _now()
            session.commit()
        raise
