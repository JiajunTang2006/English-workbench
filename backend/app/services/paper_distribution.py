"""Teacher-defined question ranges. No model calls or invented full marks.

Defaults are subject-specific; an exam's confirmed snapshot is independent.
Updating an exam creates a paper version and moves existing results by exact
printed question identity, preserving their scores and source provenance.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from ..models import AppSetting, ChangeLog, ExamQuestion, ExamPaperVersion, StudentItemResult
from ..models.agent_entities import ErrorCauseAssessment, QuestionKnowledgePoint
from .paper_versions import select_paper_version
from .subjects import get_selected_subject, ENGLISH_PAPER_QUESTION_TYPES
from ..question_types import canonical_question_type, merge_reading_rows


def distribution_types(subject):
    return ENGLISH_PAPER_QUESTION_TYPES if subject.key == "english" else subject.question_types


def question_label(question):
    return question.question_no + (f"-{question.sub_question_no}" if question.sub_question_no else "")


def parse_numbers(value: str, known_labels=()) -> list[str]:
    """1～20、25; also support printed group labels such as 1-1～1-10."""
    labels = []
    for token in re.split(r"[,，、;；\s]+", value.strip()):
        if not token:
            continue
        if token in known_labels:
            expanded = [token]
        else:
            group = re.fullmatch(r"(\d+)-(\d+)[~～至–—](\d+)-(\d+)", token)
            flat = re.fullmatch(r"(\d+)[~～至–—-](\d+)", token)
            if group:
                prefix, start, other_prefix, end = map(int, group.groups())
                if prefix != other_prefix:
                    raise HTTPException(422, "大题范围不能跨大题，请分开填写")
                expanded = [f"{prefix}-{n}" for n in _number_range(start, end)]
            elif flat:
                expanded = [str(n) for n in _number_range(*map(int, flat.groups()))]
            elif re.fullmatch(r"\d+", token):
                expanded = [str(n) for n in _number_range(int(token), int(token))]
            else:
                raise HTTPException(422, f"无法识别题号“{token}”，请填写如 1～20、25")
        labels.extend(expanded)
        if len(labels) > 500:
            raise HTTPException(422, "一份试卷最多配置500道题")
    return labels


def _number_range(start, end):
    if start < 1 or end < start or end > 1000 or end - start >= 500:
        raise HTTPException(422, "题号范围应从小到大，题号为1到1000，单段不超过500题")
    return range(start, end + 1)


def _key(subject, exam_id=None):
    return f"paper_distribution:exam:{exam_id}" if exam_id else f"paper_distribution:default:{subject.key}"


def _stored(session, key):
    item = session.get(AppSetting, key)
    return deepcopy(item.value_json) if item else {"revision": 0}


def _write(session, key, expected_revision, value):
    old = _stored(session, key)
    if old.get("revision", 0) != expected_revision:
        raise HTTPException(409, "试卷设置已被更新，请重新打开后再保存")
    value = {**value, "revision": expected_revision + 1}
    if expected_revision:
        result = session.execute(update(AppSetting).where(
            AppSetting.key == key,
            AppSetting.value_json["revision"].as_integer() == expected_revision,
        ).values(value_json=value))
        if result.rowcount != 1:
            raise HTTPException(409, "试卷设置已被更新，请重新打开后再保存")
    else:
        session.add(AppSetting(key=key, value_json=value))
    try:
        session.flush()
    except IntegrityError:
        raise HTTPException(409, "试卷设置已被更新，请重新打开后再保存")
    return value


def _normalize(session, payload, known_labels=()):
    subject = get_selected_subject(session)
    if payload.subject_key != subject.key:
        raise HTTPException(409, "任教学科已改变，请重新打开试卷设置")
    raw = [{"question_type": row.question_type, "question_numbers": row.question_numbers} for row in payload.rows]
    if len({row.question_type for row in payload.rows}) != len(payload.rows):
        raise HTTPException(422, "每个题型只填写一次")
    if subject.key == "english":
        raw = merge_reading_rows(raw)
    supplied = {row["question_type"]: row["question_numbers"].strip() for row in raw}
    if any(t not in distribution_types(subject) for t in supplied):
        raise HTTPException(422, "请使用当前学科已有的题型，每个题型只填写一次")
    rows = []
    mapping = {}
    for name in distribution_types(subject):
        text = supplied.get(name, "")
        for label in parse_numbers(text, known_labels):
            if label in mapping:
                raise HTTPException(422, f"题号 {label} 重复，不能同时属于多个题型")
            mapping[label] = name
        rows.append({"question_type": name, "question_numbers": text})
    if len(mapping) > 500:
        raise HTTPException(422, "一份试卷最多配置500道题")
    return subject, rows, mapping


def get_default(session):
    subject = get_selected_subject(session)
    saved = _stored(session, _key(subject))
    rows = saved.get("rows", [])
    if subject.key == "english":
        rows = merge_reading_rows(rows)
    by_type = {r["question_type"]: r["question_numbers"] for r in rows}
    return {"subject_key": subject.key, "revision": saved["revision"],
            "rows": [{"question_type": t, "question_numbers": by_type.get(t, "")} for t in distribution_types(subject)]}


def put_default(session, payload):
    subject, rows, _ = _normalize(session, payload)
    saved = _write(session, _key(subject), payload.expected_revision,
                   {"subject_key": subject.key, "rows": rows})
    session.add(ChangeLog(entity="paper_distribution", entity_id=subject.key,
                          action="save_default", detail_json=saved))
    return get_default(session)


def get_exam_distribution(session, exam):
    subject = get_selected_subject(session)
    saved = _stored(session, _key(subject, exam.id))
    version, _ = select_paper_version(session, exam.id)
    defaults = get_default(session)
    if saved.get("subject_key") not in (None, subject.key):
        raise HTTPException(409, "本场试卷属于其他学科，不能套用当前学科的分布")
    unassigned = []
    if version and saved.get("paper_version_id") == version.id:
        rows, source = saved["rows"], "exam"
        if subject.key == "english":
            merged = {r["question_type"]: r for r in merge_reading_rows(rows)}
            rows = [merged.get(t, {"question_type": t, "question_numbers": ""}) for t in distribution_types(subject)]
    elif version:
        # Match the existing ability-chart types; never infer knowledge tags.
        groups = {t: [] for t in distribution_types(subject)}
        aliases = {"听力": "听力理解", "作文": "书面表达", "语法": "语法填空",
                   "词汇": "词汇运用", "选词填空": "词汇运用", "单词拼写": "词汇运用"}
        for q in session.scalars(select(ExamQuestion).where(ExamQuestion.paper_version_id == version.id).order_by(ExamQuestion.id)):
            kind = canonical_question_type(q.question_type) if subject.key == "english" else q.question_type
            text = kind if kind in groups else re.sub(r"^第.+?部分\s*", "", q.section_name or "")
            if subject.key == "english":
                text = canonical_question_type(text)
            name = aliases.get(text, text) if subject.key == "english" else text
            label = question_label(q)
            if name in groups:
                # Singleton grouped labels must be distinguishable from flat ranges.
                groups[name].append(f"{label}～{label}"
                                    if q.sub_question_no or re.fullmatch(r"\d+-\d+", label) else label)
            else:
                unassigned.append(label)
        rows = [{"question_type": t, "question_numbers": "、".join(groups[t])} for t in groups]
        source = "paper"
    else:
        rows, source = defaults["rows"], "default"
    return {"exam_id": exam.id, "subject_key": subject.key, "revision": saved["revision"],
            "paper_version_id": version.id if version else None, "source": source,
            "rows": rows, "default_rows": defaults["rows"], "unassigned_questions": unassigned}


def put_exam_distribution(session, exam, payload):
    version, _ = select_paper_version(session, exam.id)
    if (version.id if version else None) != payload.expected_paper_version_id:
        raise HTTPException(409, "本场试卷结构已更新，请重新打开考试设置")
    old_questions = list(session.scalars(select(ExamQuestion).where(
        ExamQuestion.paper_version_id == version.id).order_by(ExamQuestion.id))) if version else []
    old_by_label = {}
    for q in old_questions:
        label = question_label(q)
        if label in old_by_label:
            raise HTTPException(422, f"原试卷题号 {label} 有歧义，暂不能通过范围设置调整")
        old_by_label[label] = q
    subject, rows, mapping = _normalize(session, payload, old_by_label)
    if not mapping:
        raise HTTPException(422, "请至少填写一个题型的题号")
    results = list(session.scalars(select(StudentItemResult).where(
        StudentItemResult.exam_id == exam.id,
        StudentItemResult.question_id.in_([q.id for q in old_questions])))) if old_questions else []
    used_ids = {r.question_id for r in results}
    omitted = [label for label, q in old_by_label.items() if q.id in used_ids and label not in mapping]
    if omitted:
        raise HTTPException(422, "以下题号已有小分，必须保留并分配题型：" + "、".join(omitted[:30]))
    # Save the revision before changing any facts; a rejected transaction rolls back everything.
    saved = _write(session, _key(subject, exam.id), payload.expected_revision,
                   {"subject_key": subject.key, "rows": rows, "paper_version_id": None})
    next_version = max((v for v in session.scalars(select(ExamPaperVersion.version).where(
        ExamPaperVersion.exam_id == exam.id))), default=0) + 1
    paper = ExamPaperVersion(exam_id=exam.id, version=next_version, status="confirmed",
        full_score=exam.full_score, extraction_provider="teacher_distribution",
        source_attachment_ids_json=deepcopy(version.source_attachment_ids_json) if version else [],
        confirmed_at=datetime.now(timezone.utc),
        structure_hash=hashlib.sha256(json.dumps({"mapping": mapping, "base_paper_id": version.id if version else None},
            sort_keys=True, ensure_ascii=False).encode()).hexdigest())
    session.add(paper)
    session.flush()
    old_to_new = {}
    for label, name in mapping.items():
        old = old_by_label.get(label)
        fields = {col.name: deepcopy(getattr(old, col.name)) for col in ExamQuestion.__table__.columns
                  if col.name not in {"id", "paper_version_id", "created_at"}} if old else {
                      "question_no": label, "max_score": 0.0}
        # 0 marks unknown full score internally; never derive a percentage/correctness from it.
        question = ExamQuestion(**fields, paper_version_id=paper.id)
        question.question_type = name
        question.section_name = name
        session.add(question)
        session.flush()
        if old:
            old_to_new[old.id] = question.id
            for link in old.knowledge_point_links:
                session.add(QuestionKnowledgePoint(question_id=question.id,
                    knowledge_point_id=link.knowledge_point_id, is_primary=link.is_primary,
                    source=link.source, confirmed_by_teacher=link.confirmed_by_teacher))
    for result in results:
        result.question_id = old_to_new[result.question_id]
    for old_id, new_id in old_to_new.items():
        session.execute(update(ErrorCauseAssessment).where(
            ErrorCauseAssessment.exam_id == exam.id, ErrorCauseAssessment.question_id == old_id
        ).values(question_id=new_id))
    for confirmed in session.scalars(select(ExamPaperVersion).where(
        ExamPaperVersion.exam_id == exam.id, ExamPaperVersion.status == "confirmed", ExamPaperVersion.id != paper.id)):
        confirmed.status = "superseded"
    saved["paper_version_id"] = paper.id
    session.get(AppSetting, _key(subject, exam.id)).value_json = saved
    session.add(ChangeLog(entity="paper_distribution", entity_id=str(exam.id),
                          action="save_exam", detail_json={**saved, "moved_results": len(results)}))
    session.flush()
    return get_exam_distribution(session, exam)


def ensure_exam_distribution(session, exam):
    """Freeze defaults on first item-score import if the exam has no paper yet."""
    version, _ = select_paper_version(session, exam.id)
    if version:
        return
    defaults = get_default(session)
    if not any(r["question_numbers"] for r in defaults["rows"]):
        return
    from ..schemas.exams import PaperDistributionWrite
    revision = _stored(session, _key(get_selected_subject(session), exam.id))["revision"]
    put_exam_distribution(session, exam, PaperDistributionWrite(
        subject_key=defaults["subject_key"], rows=defaults["rows"],
        expected_revision=revision, expected_paper_version_id=None))
