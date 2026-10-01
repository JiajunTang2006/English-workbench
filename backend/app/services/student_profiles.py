"""教师确认的学生画像服务。

画像是教学连续性记录，不替代成绩、排名等事实表。学生诊断报告中的
证据支持变更会自动合并到当前画像快照，并保留可追溯的确认记录；教师
仍可在学生管理中继续编辑或拒绝人工草稿。
"""

from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.agent_entities import StudentLongitudinalProfile, StudentProfile, StudentProfileRevision
from ..models.entities import Enrollment, Student, Term


PROFILE_LIST_FIELDS = ("strengths", "weaknesses", "habits", "interventions", "goals", "watch_items")
PROFILE_DEFAULTS: dict[str, Any] = {
    "summary": "",
    "subject_key": None,
    "strengths": [],
    "weaknesses": [],
    "habits": [],
    "interventions": [],
    "goals": [],
    "watch_items": [],
}


def _clean_profile_summary(value: Any) -> str:
    """将模型摘要限制为教师可读的连续文字。

    画像摘要不应泄露 prompt 中的匿名占位符，也不应把“数据缺失”这类
    技术性局限当成学生特征；详细局限仍保留在报告本身。
    """
    text = str(value or "").strip()
    text = re.sub(r"\bstudent[ _-]?\d+\b", "该生", text, flags=re.IGNORECASE)
    text = re.sub(r"学生[ _-]?\d+", "该生", text)
    # 删除技术性短语而保留同一句中有价值的总体判断，避免把整段摘要一起删掉。
    technical = ("小分缺失", "小分未录入", "小分未", "逐题数据未录入", "逐题数据未",
                 "逐题得分未录入", "逐题得分未", "题目得分未录入", "题目得分未",
                 "知识点数据未录入", "知识点数据未", "试卷结构未录入", "试卷结构未",
                 "试卷结构与",
                 "数据未录入", "尚未录入", "缺少逐题数据", "缺少逐题", "缺失逐题",
                 "无法定位具体", "待数据补齐后再分析", "待数据补齐", "数据补齐后再分析",
                 "数据不足")
    for phrase in technical:
        text = text.replace(phrase, "")
    text = re.sub(r"[，,；]\s*[。！？]", "。", text)
    text = re.sub(r"([。！？])\s*[。！？]+", r"\1", text)
    text = re.sub(r"[，,；。]?\s*(?:后续|目前)\s*[。！？]?$", "", text)
    text = re.sub(r"该生\s+", "该生", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" \n")[:2000]


def empty_profile() -> dict[str, Any]:
    return copy.deepcopy(PROFILE_DEFAULTS)


def compact_profile(profile: dict[str, Any] | None) -> dict[str, Any]:
    """Keep the cross-term profile compact and deterministic.

    The model performs the semantic compression during the first analysis of
    a new term. This function is the storage guardrail: it removes duplicates,
    caps accumulated list items, and keeps the summary bounded.
    """
    result = empty_profile()
    if isinstance(profile, dict):
        result.update(copy.deepcopy(profile))
    result["summary"] = _clean_profile_summary(result.get("summary"))
    for field in PROFILE_LIST_FIELDS:
        values = result.get(field)
        if not isinstance(values, list):
            values = []
        unique: dict[str, Any] = {}
        for item in values:
            unique[_item_key(item)] = item
        result[field] = list(unique.values())[-30:]
    return result


def _profile_matches_subject(profile: dict[str, Any] | None, subject_key: str) -> bool:
    """Profiles created before subject tagging belong to the original English workspace."""
    if not isinstance(profile, dict):
        return False
    return (profile.get("subject_key") or "english") == subject_key


def get_profile(session: Session, student_id: int, term_id: int) -> StudentProfile | None:
    return session.scalar(select(StudentProfile).where(
        StudentProfile.student_id == student_id,
        StudentProfile.term_id == term_id,
    ))


def _latest_prior_profile(session: Session, student_id: int, term_id: int) -> StudentProfile | None:
    from .subjects import get_selected_subject
    subject_key = get_selected_subject(session).key
    for profile in session.scalars(
        select(StudentProfile)
        .join(Term, Term.id == StudentProfile.term_id)
        .where(StudentProfile.student_id == student_id, StudentProfile.term_id != term_id)
        .order_by(StudentProfile.updated_at.desc(), StudentProfile.id.desc())
    ):
        if _profile_matches_subject(profile.profile_json, subject_key):
            return profile
    return None


def get_longitudinal_profile(session: Session, student_id: int) -> StudentLongitudinalProfile | None:
    profile = session.scalar(select(StudentLongitudinalProfile).where(
        StudentLongitudinalProfile.student_id == student_id,
    ))
    from .subjects import get_selected_subject
    return profile if profile and _profile_matches_subject(
        profile.profile_json, get_selected_subject(session).key
    ) else None


def prepare_student_profile_inheritance(
    session: Session,
    source_term_id: int | None,
    target_term_id: int,
) -> int:
    """Prepare long-term profile continuity when entering a different term.

    No model call is made here. Existing confirmed profile material is copied
    into the student's longitudinal record and marked for compression on the
    first student diagnosis in the target term.
    """
    if source_term_id is None or source_term_id == target_term_id:
        return 0
    from .subjects import get_selected_subject
    subject_key = get_selected_subject(session).key
    source_term = session.get(Term, source_term_id)
    target_term = session.get(Term, target_term_id)
    if source_term is None or target_term is None:
        return 0
    if source_term.starts_on and target_term.starts_on:
        if target_term.starts_on <= source_term.starts_on:
            return 0
    elif target_term.id <= source_term.id:
        # 没有日期时，学期创建顺序是本地唯一可靠的时间线。
        return 0
    student_ids = list(session.scalars(
        select(Enrollment.student_id)
        .where(Enrollment.term_id == target_term_id, Enrollment.status == "active")
        .distinct()
    ))
    if not student_ids:
        return 0
    source_profiles = {
        item.student_id: item
        for item in session.scalars(select(StudentProfile).where(
            StudentProfile.term_id == source_term_id,
            StudentProfile.student_id.in_(student_ids),
        ))
        if _profile_matches_subject(item.profile_json, subject_key)
    }
    longitudinal = {
        item.student_id: item
        for item in session.scalars(select(StudentLongitudinalProfile).where(
            StudentLongitudinalProfile.student_id.in_(student_ids),
        ))
        if _profile_matches_subject(item.profile_json, subject_key)
    }
    prepared = 0
    for student_id in student_ids:
        current = longitudinal.get(student_id)
        source = source_profiles.get(student_id)
        if current is None:
            if source is None:
                continue
            current = StudentLongitudinalProfile(
                student_id=student_id,
                profile_json=compact_profile(source.profile_json),
                version=1,
                last_source_term_id=source_term_id,
                needs_compression=True,
            )
            session.add(current)
            longitudinal[student_id] = current
        else:
            current.last_source_term_id = source_term_id
            current.needs_compression = True
        prepared += 1
    session.flush()
    return prepared


def _ensure_term_profile(session: Session, student_id: int, term_id: int) -> StudentProfile | None:
    profile = get_profile(session, student_id, term_id)
    if profile is not None:
        from .subjects import get_selected_subject
        if not _profile_matches_subject(profile.profile_json, get_selected_subject(session).key):
            raise HTTPException(409, "该学期已有另一学科画像，请在原学科工作区查看")
        return profile
    longitudinal = get_longitudinal_profile(session, student_id)
    source_profile = _latest_prior_profile(session, student_id, term_id)
    seed = longitudinal.profile_json if longitudinal is not None else (
        source_profile.profile_json if source_profile is not None else None
    )
    if seed is None:
        return None
    profile = StudentProfile(
        student_id=student_id,
        term_id=term_id,
        profile_json=compact_profile(seed),
        version=0,
        updated_by="inherited",
    )
    session.add(profile)
    session.flush()
    return profile


def get_profile_payload(session: Session, student_id: int, term_id: int) -> dict[str, Any]:
    from .subjects import get_selected_subject
    subject_key = get_selected_subject(session).key
    profile = get_profile(session, student_id, term_id)
    longitudinal = get_longitudinal_profile(session, student_id)
    if profile and not _profile_matches_subject(profile.profile_json, subject_key):
        profile = None
    result = empty_profile()
    if profile and isinstance(profile.profile_json, dict):
        result.update(copy.deepcopy(profile.profile_json))
    elif longitudinal and isinstance(longitudinal.profile_json, dict):
        result.update(compact_profile(longitudinal.profile_json))
    else:
        inherited = _latest_prior_profile(session, student_id, term_id)
        if inherited and isinstance(inherited.profile_json, dict):
            result.update(compact_profile(inherited.profile_json))
    result["summary"] = _clean_profile_summary(result.get("summary"))
    return {
        "profile": result,
        "profile_id": profile.id if profile else None,
        "version": profile.version if profile else 0,
        "updated_at": profile.updated_at if profile else None,
        "updated_by": profile.updated_by if profile else None,
        "longitudinal_profile": compact_profile(longitudinal.profile_json) if longitudinal else empty_profile(),
        "longitudinal_version": longitudinal.version if longitudinal else 0,
        "needs_compression": bool(longitudinal and longitudinal.needs_compression),
        "inherited": profile is None and bool(longitudinal),
        "inherited_from_term_id": longitudinal.last_source_term_id if longitudinal else None,
    }


def _ensure_student_scope(session: Session, student_id: int, term_id: int) -> None:
    student = session.scalar(select(Student).where(Student.id == student_id))
    enrollment = session.scalar(select(Enrollment).where(
        Enrollment.student_id == student_id,
        Enrollment.term_id == term_id,
        Enrollment.status == "active",
    ))
    if student is None or enrollment is None:
        raise HTTPException(status_code=404, detail="学生不存在或不属于当前学期")


def normalize_patch(patch: dict[str, Any]) -> dict[str, Any]:
    """只允许画像字段和显式 add/remove 操作，防止模型写入任意 JSON。"""
    if not isinstance(patch, dict):
        raise ValueError("画像变更必须是对象")
    allowed = {"summary", "subject_key"}
    for field in PROFILE_LIST_FIELDS:
        allowed.update({field, f"{field}_add", f"{field}_remove"})
    unknown = set(patch) - allowed
    if unknown:
        raise ValueError(f"不支持的画像字段: {sorted(unknown)}")
    normalized: dict[str, Any] = {}
    if "summary" in patch:
        summary = str(patch.get("summary") or "").strip()
        if len(summary) > 2000:
            raise ValueError("画像摘要不能超过 2000 字")
        normalized["summary"] = summary
    if "subject_key" in patch:
        subject_key = str(patch.get("subject_key") or "").strip().lower()
        if subject_key:
            from .subjects import is_valid_subject_key
            if not is_valid_subject_key(subject_key):
                raise ValueError("画像来源学科无效")
            normalized["subject_key"] = subject_key
    for field in PROFILE_LIST_FIELDS:
        for key in (field, f"{field}_add", f"{field}_remove"):
            if key not in patch:
                continue
            value = patch[key]
            if not isinstance(value, list):
                raise ValueError(f"{key} 必须是数组")
            if len(value) > 30:
                raise ValueError(f"{key} 最多 30 项")
            cleaned = []
            for item in value:
                if isinstance(item, str):
                    item = item.strip()
                    if len(item) > 500:
                        raise ValueError(f"{key} 单项不能超过 500 字")
                    if item:
                        cleaned.append(item)
                elif field == "interventions" and isinstance(item, dict):
                    # 干预记录保留少量结构化字段，避免把任意模型输出写进画像。
                    allowed_item = {k: str(item[k]).strip()[:500] for k in ("action", "result", "status") if item.get(k) is not None}
                    if allowed_item.get("action"):
                        cleaned.append(allowed_item)
                else:
                    raise ValueError(f"{key} 只支持字符串（干预记录也可为对象）")
            normalized[key] = cleaned
    return normalized


def _item_key(item: Any) -> str:
    return json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def apply_patch(current: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = empty_profile()
    result.update(copy.deepcopy(current or {}))
    patch = normalize_patch(patch)
    if "subject_key" in patch and not _profile_matches_subject(result, patch["subject_key"]):
        # A change of subject must never carry old summaries or list items forward.
        result = empty_profile()
    if "summary" in patch:
        result["summary"] = patch["summary"]
    if "subject_key" in patch:
        result["subject_key"] = patch["subject_key"]
    for field in PROFILE_LIST_FIELDS:
        if field in patch:
            result[field] = copy.deepcopy(patch[field])
        values = list(result.get(field) or [])
        by_key = {_item_key(item): item for item in values}
        for item in patch.get(f"{field}_add", []):
            by_key[_item_key(item)] = item
        for item in patch.get(f"{field}_remove", []):
            by_key.pop(_item_key(item), None)
        result[field] = list(by_key.values())
    return result


def create_profile_revision(
    session: Session,
    *,
    student_id: int,
    term_id: int,
    patch: dict[str, Any],
    analysis_run_id: int | None = None,
    evidence_ids: list[str] | None = None,
) -> StudentProfileRevision:
    _ensure_student_scope(session, student_id, term_id)
    normalized = normalize_patch(patch)
    from .subjects import get_selected_subject
    subject_key = get_selected_subject(session).key
    if normalized.get("subject_key", subject_key) != subject_key:
        raise HTTPException(409, "画像学科与当前工作区不一致")
    normalized["subject_key"] = subject_key
    profile = _ensure_term_profile(session, student_id, term_id)
    base_version = profile.version if profile else 0
    revision = StudentProfileRevision(
        student_id=student_id,
        term_id=term_id,
        profile_id=profile.id if profile else None,
        analysis_run_id=analysis_run_id,
        base_version=base_version,
        proposed_patch_json=normalized,
        evidence_ids_json=list(evidence_ids or []),
        status="draft",
    )
    session.add(revision)
    session.commit()
    session.refresh(revision)
    return revision


def apply_profile_edit(
    session: Session,
    *,
    student_id: int,
    term_id: int,
    patch: dict[str, Any],
    expected_version: int | None = None,
    updated_by: str = "teacher",
) -> StudentProfileRevision:
    """直接写入教师编辑的正式画像，并保留一条已确认的审计记录。"""
    _ensure_student_scope(session, student_id, term_id)
    profile = get_profile(session, student_id, term_id)
    from .subjects import get_selected_subject
    if profile and not _profile_matches_subject(profile.profile_json, get_selected_subject(session).key):
        raise HTTPException(409, "画像学科与当前工作区不一致")
    current_version = profile.version if profile else 0
    if expected_version is not None and int(expected_version) != current_version:
        raise HTTPException(status_code=409, detail={
            "code": "PROFILE_VERSION_CONFLICT",
            "message": "画像已被其他修改更新，请刷新后再保存。",
        })
    revision = create_profile_revision(
        session,
        student_id=student_id,
        term_id=term_id,
        patch=patch,
        analysis_run_id=None,
        evidence_ids=[],
    )
    return confirm_revision(session, revision.id, confirmed_by=updated_by)


def revision_payload(revision: StudentProfileRevision, session: Session | None = None) -> dict[str, Any]:
    current = empty_profile()
    version = revision.base_version
    if session is not None:
        profile = session.get(StudentProfile, revision.profile_id) if revision.profile_id else get_profile(session, revision.student_id, revision.term_id)
        if profile:
            current.update(copy.deepcopy(profile.profile_json or {}))
            version = profile.version
    return {
        "id": revision.id,
        "student_id": revision.student_id,
        "term_id": revision.term_id,
        "analysis_run_id": revision.analysis_run_id,
        "base_version": revision.base_version,
        "current_version": version,
        "patch": revision.proposed_patch_json or {},
        "preview": apply_patch(current, revision.proposed_patch_json or {}),
        "evidence_ids": revision.evidence_ids_json or [],
        "status": revision.status,
        "created_at": revision.created_at,
        "confirmed_at": revision.confirmed_at,
        "confirmed_by": revision.confirmed_by,
        "rejection_reason": revision.rejection_reason,
    }


def list_revisions(
    session: Session,
    *,
    student_id: int | None = None,
    term_id: int | None = None,
    analysis_run_id: int | None = None,
    status: str | None = None,
) -> list[StudentProfileRevision]:
    stmt = select(StudentProfileRevision)
    if student_id is not None:
        stmt = stmt.where(StudentProfileRevision.student_id == student_id)
    if term_id is not None:
        stmt = stmt.where(StudentProfileRevision.term_id == term_id)
    if analysis_run_id is not None:
        stmt = stmt.where(StudentProfileRevision.analysis_run_id == analysis_run_id)
    if status is not None:
        stmt = stmt.where(StudentProfileRevision.status == status)
    return list(session.scalars(stmt.order_by(StudentProfileRevision.created_at.desc(), StudentProfileRevision.id.desc())))


def confirm_revision(session: Session, revision_id: int, *, confirmed_by: str = "teacher") -> StudentProfileRevision:
    revision = session.get(StudentProfileRevision, revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail="画像变更不存在")
    if revision.status != "draft":
        raise HTTPException(status_code=409, detail="该画像变更已处理")
    from .subjects import get_selected_subject
    if (revision.proposed_patch_json or {}).get("subject_key", "english") != get_selected_subject(session).key:
        raise HTTPException(status_code=409, detail="画像变更属于另一学科工作区")
    _ensure_student_scope(session, revision.student_id, revision.term_id)
    profile = get_profile(session, revision.student_id, revision.term_id)
    current_version = profile.version if profile else 0
    if current_version != revision.base_version:
        raise HTTPException(status_code=409, detail={
            "code": "PROFILE_VERSION_CONFLICT",
            "message": "画像已被其他修改更新，请重新生成变更建议。",
        })
    current = profile.profile_json if profile else empty_profile()
    merged = apply_patch(current, revision.proposed_patch_json or {})
    now = datetime.now(timezone.utc)
    if profile is None:
        profile = StudentProfile(
            student_id=revision.student_id,
            term_id=revision.term_id,
            profile_json=merged,
            version=1,
            last_analysis_run_id=revision.analysis_run_id,
            updated_by=confirmed_by,
            created_at=now,
            updated_at=now,
        )
        session.add(profile)
        session.flush()
    else:
        profile.profile_json = merged
        profile.version = current_version + 1
        profile.last_analysis_run_id = revision.analysis_run_id
        profile.updated_by = confirmed_by
        profile.updated_at = now
    revision.profile_id = profile.id
    revision.status = "confirmed"
    revision.confirmed_at = now
    revision.confirmed_by = confirmed_by
    longitudinal = get_longitudinal_profile(session, revision.student_id)
    if longitudinal is None:
        longitudinal = StudentLongitudinalProfile(
            student_id=revision.student_id,
            profile_json=compact_profile(merged),
            version=1,
            last_source_term_id=revision.term_id,
            last_analysis_run_id=revision.analysis_run_id,
            needs_compression=False,
            updated_by=confirmed_by,
        )
        session.add(longitudinal)
    else:
        # Merge the confirmed change into the long-term snapshot instead of
        # replacing it with an older term's complete profile.
        longitudinal.profile_json = compact_profile(
            apply_patch(longitudinal.profile_json or empty_profile(), revision.proposed_patch_json or {})
        )
        longitudinal.version += 1
        longitudinal.last_source_term_id = revision.term_id
        longitudinal.last_analysis_run_id = revision.analysis_run_id
        longitudinal.needs_compression = False
        longitudinal.updated_by = confirmed_by
        longitudinal.updated_at = now
    session.commit()
    session.refresh(revision)
    return revision


def apply_analysis_report_to_profile(
    session: Session,
    *,
    run: Any,
    report: dict[str, Any] | None,
    confirmed_by: str = "ai",
    target_student_id: int | None = None,
) -> StudentProfileRevision | None:
    """将已提交的学生诊断报告同步为正式画像。

    Managed Harness 的分析包路径只开放 ``submit_report``，不会经过旧的
    ``propose_student_profile_update`` 工具。因此在报告通过证据校验并落库
    后，由服务端保存模型返回的 ``profile_summary`` 自然语言段落，并自动确认。
    画像的其它 JSON 字段不会再由程序从 findings/recommendations 机械拆分；
    ``analysis_run_id`` 提供幂等键，重复提交或读取旧报告不会重复追加。
    """
    if getattr(run, "capability", None) not in {"student_diagnosis", "exam_analysis"}:
        return None
    student_id = target_student_id or getattr(run, "student_id", None)
    term_id = getattr(run, "term_id", None)
    run_id = getattr(run, "id", None)
    if not (isinstance(student_id, int) and student_id > 0 and
            isinstance(term_id, int) and term_id > 0 and
            isinstance(run_id, int) and run_id > 0):
        return None
    if not isinstance(report, dict):
        return None

    # 同一运行只允许生成一条确认记录，避免 submit_report 重试时重复写入。
    existing = session.scalar(select(StudentProfileRevision).where(
        StudentProfileRevision.analysis_run_id == run_id,
    ).order_by(StudentProfileRevision.id.desc()))
    if existing is not None:
        if existing.status == "confirmed":
            _record_run_growth_reference(session, run=run, student_id=student_id, term_id=term_id)
            return existing
        if existing.status == "draft":
            confirmed = confirm_revision(session, existing.id, confirmed_by=confirmed_by)
            _record_run_growth_reference(session, run=run, student_id=student_id, term_id=term_id)
            return confirmed

    # 画像是 AI 结合历史档案和本次证据重新写出的连贯摘要。只保存这一
    # 个自然语言字段，避免把报告的结构化发现/建议按关键词拆成互不相干的列表。
    explicit_profile_summary = bool(str(report.get("profile_summary") or "").strip())
    summary = _clean_profile_summary(report.get("profile_summary"))
    if explicit_profile_summary and not summary:
        summary = "本次诊断主要反映学生的整体表现，后续结合更多学习表现持续观察。"
    if not explicit_profile_summary:
        # 兼容旧版本模型：旧报告没有 profile_summary 时暂以报告摘要作为
        # 单段落过渡值；仅对没有新字段的历史报告保留旧列表迁移逻辑。
        summary = _clean_profile_summary(report.get("summary"))
    packet_stats = ((getattr(run, "input_summary_json", None) or {}).get("packet_stats") or {})
    # Prefer the immutable run subject; pre-migration runs without a marker
    # belong to the original English workspace.
    subject_key = getattr(run, "subject_key", None) or packet_stats.get("subject_key") or "english"
    from .subjects import get_selected_subject
    if subject_key != get_selected_subject(session).key:
        return None
    patch: dict[str, Any] = {"subject_key": subject_key}
    if summary:
        patch["summary"] = summary[:2000]

    if not explicit_profile_summary:
        # 历史报告兼容分支。新版本有 profile_summary 时绝不会执行这里，
        # 因而画像不会再被程序按关键词拆分。
        strengths: list[str] = []
        weaknesses: list[str] = []
        strength_markers = ("优势", "强项", "稳定", "良好", "较好", "突出")
        for finding in report.get("findings") or []:
            if not isinstance(finding, dict):
                continue
            title = str(finding.get("title") or "").strip()
            detail = str(finding.get("description") or finding.get("detail") or "").strip()
            text = title or detail
            if title and detail and detail != title:
                text = f"{title}：{detail}"
            if text:
                (strengths if any(marker in title for marker in strength_markers) else weaknesses).append(text[:500])
        if strengths:
            patch["strengths_add"] = strengths[:30]
        if weaknesses:
            patch["weaknesses_add"] = weaknesses[:30]
        interventions: list[str] = []
        for recommendation in report.get("recommendations") or []:
            if not isinstance(recommendation, dict):
                continue
            action = str(recommendation.get("action") or recommendation.get("title") or "").strip()
            rationale = str(recommendation.get("rationale") or "").strip()
            if action:
                interventions.append((f"{action}：{rationale}" if rationale else action)[:500])
        if interventions:
            patch["interventions_add"] = interventions[:30]
    if len(patch) == 1:
        return None

    evidence_ids: list[str] = []
    for item in (report.get("findings") or []):
        if isinstance(item, dict):
            refs = item.get("evidence_ids") or []
            if isinstance(refs, list):
                evidence_ids.extend(str(ref).strip() for ref in refs if str(ref).strip())
    for item in (report.get("recommendations") or []):
        if isinstance(item, dict):
            refs = item.get("supports") or item.get("evidence_ids") or []
            if isinstance(refs, list):
                evidence_ids.extend(str(ref).strip() for ref in refs if str(ref).strip())
    evidence_ids = list(dict.fromkeys(evidence_ids))[:50]

    revision = create_profile_revision(
        session,
        student_id=student_id,
        term_id=term_id,
        patch=patch,
        analysis_run_id=run_id,
        evidence_ids=evidence_ids,
    )
    confirmed = confirm_revision(session, revision.id, confirmed_by=confirmed_by)
    _record_run_growth_reference(session, run=run, student_id=student_id, term_id=term_id)
    return confirmed


def _record_run_growth_reference(session: Session, *, run: Any, student_id: int,
                                 term_id: int) -> dict[str, Any] | None:
    """把本次运行冻结的成长事实引用回写到画像（方案 §6.1 / §6.4）。

    只有学生诊断类运行、且组装分析包时确实冻结了成长引用时才写入；这样
    ``exam_analysis`` 等不引用成长事实的路径不会把画像误标为「依据为最新」。
    """
    if getattr(run, "capability", None) != "student_diagnosis":
        return None
    reference = (getattr(run, "input_summary_json", None) or {}).get("growth_reference")
    if not isinstance(reference, dict) or not reference.get("revision"):
        return None
    from .growth.summary import record_growth_reference

    return record_growth_reference(
        session, student_id=student_id, term_id=term_id, reference=reference)


def reject_revision(session: Session, revision_id: int, *, reason: str = "") -> StudentProfileRevision:
    revision = session.get(StudentProfileRevision, revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail="画像变更不存在")
    if revision.status != "draft":
        raise HTTPException(status_code=409, detail="该画像变更已处理")
    revision.status = "rejected"
    revision.rejection_reason = reason[:500] if reason else None
    session.commit()
    session.refresh(revision)
    return revision
