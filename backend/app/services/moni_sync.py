"""MONI 只读 MCP -> Workbench 考试事实同步。

这里不把 MONI 的原始对象直接暴露给前端，而是先转换成统一的
``SchoolSyncPayload``，再复用学校同步服务的校验、幂等和教师覆盖保护。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from ..models import AppSetting, Term
from ..schemas.school_sync import SchoolSyncPayload, StudentRosterPayload
from .plugin_manager import PluginManager
from .school_sync import apply_payload, apply_student_roster, rebuild_compat_exam_snapshots

log = logging.getLogger(__name__)


def _text(result: dict[str, Any]) -> str:
    chunks = [item.get("text", "") for item in result.get("content", []) if isinstance(item, dict) and item.get("type") == "text"]
    if not chunks:
        raise RuntimeError("MONI 没有返回数据")
    return "\n".join(chunks)


def _rows(text: str) -> list[dict[str, Any]]:
    try:
        value: Any = json.loads(text)
    except json.JSONDecodeError:
        value = []
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                value.append(parsed)
    if isinstance(value, dict):
        for key in ("rows", "data", "items", "students", "results"):
            if isinstance(value.get(key), list):
                value = value[key]
                break
    if isinstance(value, dict):
        return [value]
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _value(row: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return default


def _str(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value).strip() or None


def _num(value: Any) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    number = _num(value)
    return int(number) if number is not None and number >= 0 else None


def _semantic_key(value: Any) -> str:
    """将字段名/说明压平，供不同租户的同义字段识别。"""
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value or "").lower())


def _field_context(row: dict[str, Any]) -> str:
    return _semantic_key(" ".join(str(value) for value in row.values() if value not in (None, "")))


def _is_student_id_field(value: Any) -> bool:
    key = _semantic_key(value)
    subject = any(token in key for token in ("student", "learner", "pupil", "学生"))
    identity = any(token in key for token in ("id", "key", "code", "identifier", "编号", "标识"))
    return subject and identity and not any(token in key for token in ("name", "姓名"))


def _is_subject_score_field(value: Any) -> bool:
    key = _semantic_key(value)
    if any(token in key for token in ("total", "aggregate", "allsubject", "总分", "全科", "合计")):
        return False
    score = any(token in key for token in ("score", "result", "mark", "成绩", "分数", "得分"))
    subject = any(token in key for token in ("subject", "english", "英语", "单科", "科目"))
    return score and subject


def _is_subject_tier_field(value: Any) -> bool:
    key = _semantic_key(value)
    if any(token in key for token in ("total", "aggregate", "allsubject", "总分", "全科", "综合")):
        return False
    tier = any(token in key for token in ("tier", "level", "band", "层级", "分层", "等级"))
    subject = any(token in key for token in ("subject", "english", "英语", "单科", "科目"))
    return tier and subject


def _is_subject_grade_rank_field(value: Any) -> bool:
    key = _semantic_key(value)
    if any(token in key for token in ("class", "班级", "total", "aggregate", "总分", "全科", "global", "全局")):
        return False
    rank = any(token in key for token in ("rank", "position", "place", "排名", "名次", "位次"))
    grade = any(token in key for token in ("grade", "year", "年级"))
    return rank and grade


def _schema_field_name(reader: Any, path: str, predicate: Any) -> str | None:
    """只在常见字段无法识别时读取字段说明，避免额外访问 MONI。"""
    if not hasattr(reader, "read"):
        return None
    try:
        fields = reader.read(path)
    except Exception:
        return None
    for field in fields:
        name = _str(_value(field, "name", "field", "fieldName", "field_name", "key"))
        if not name:
            continue
        context = " ".join(filter(None, (
            name,
            _str(_value(field, "label", "title", "displayName", "display_name")),
            _str(_value(field, "description", "comment", "semanticName", "semantic_name")),
        )))
        if predicate(context):
            return name
    return None


def _student_id_value(row: dict[str, Any], preferred: str | None = None) -> str | None:
    aliases = tuple(filter(None, (
        preferred, "studentId", "student_id", "studentKey", "studentCode",
        "learnerId", "learner_id", "pupilId", "pupil_id", "externalId", "external_id",
    )))
    value = _str(_value(row, *aliases))
    if value:
        return value
    for key, candidate in row.items():
        if candidate not in (None, "") and _is_student_id_field(key):
            return _str(candidate)
    return None


_SUBJECT_TIER_ALIASES = {
    "ELITE": "A", "A": "A", "优": "A", "优秀": "A",
    "KEY": "B", "B": "B", "良": "B", "良好": "B",
    "GOOD": "C", "C": "C", "中": "C", "合格": "C",
    "REGULAR": "D", "D": "D", "普通": "D", "一般": "D",
}


def _tier_key(value: Any) -> str | None:
    """将 MONI 的 subjectTier 统一为 A/B/C/D；不使用 totalTier。"""
    text = _str(value)
    if not text:
        return None
    normalized = text.upper().replace(" ", "").replace("层", "")
    return _SUBJECT_TIER_ALIASES.get(normalized)


def _subject_tier_value(row: dict[str, Any], preferred: str | None = None) -> Any:
    """优先按稳定枚举值识别英语层级，同时排除总分层级。"""
    aliases = tuple(filter(None, (preferred, "subjectTier", "subject_tier", "englishTier", "english_tier")))
    value = _value(row, *aliases)
    if _tier_key(value):
        return value
    # ELITE/KEY/GOOD/REGULAR 是稳定业务值，可以在字段名变化时安全识别；
    # A/B/C/D 过于常见，仅允许出现在有明确单科层级语义的字段中。
    for key, candidate in row.items():
        normalized = (_str(candidate) or "").upper().replace(" ", "")
        if normalized in {"ELITE", "KEY", "GOOD", "REGULAR"} and "total" not in _semantic_key(key) and "总分" not in _semantic_key(key):
            return candidate
    for key, candidate in row.items():
        if _is_subject_tier_field(key) and _tier_key(candidate):
            return candidate
    return None


def _subject_grade_rank_value(row: dict[str, Any], preferred: str | None = None) -> int | None:
    aliases = tuple(filter(None, (
        preferred, "subjectGradeRank", "subject_grade_rank", "subjectRankInGrade",
        "subject_rank_in_grade", "rankInGrade", "gradeRank", "englishGradeRank",
    )))
    value = _int(_value(row, *aliases))
    if value is not None:
        return value
    for key, candidate in row.items():
        if _is_subject_grade_rank_field(key):
            value = _int(candidate)
            if value is not None:
                return value
    return None


def _subject_class_rank_value(row: dict[str, Any]) -> int | None:
    value = _int(_value(
        row, "subjectRankInClass", "subject_rank_in_class", "subjectClassRank",
        "classRank", "englishClassRank",
    ))
    if value is not None:
        return value
    for key, candidate in row.items():
        normalized = _semantic_key(key)
        if (
            any(token in normalized for token in ("rank", "position", "place", "排名", "名次", "位次"))
            and any(token in normalized for token in ("class", "班级"))
            and not any(token in normalized for token in ("total", "aggregate", "总分", "全科"))
        ):
            value = _int(candidate)
            if value is not None:
                return value
    return None


def _snap_half(value: float) -> float:
    """按 0.5 分粒度四舍五入，避免 Python round 的银行家舍入。"""
    return int(value * 2 + 0.5 + 1e-9) / 2


def _derive_tier_cutoffs(rows: list[dict[str, Any]], *, subject_id: str, subject_name: str, full_score: float) -> dict[str, float | None]:
    """从英语单科 subjectTier 计算 A/B/C 层最低分。

    每条线取该层实际出现的最低英语单科成绩，并吸附到 0.5 分；缺失层
    保持为空，避免用总分或其它科目推断。最后做单调约束，符合 WorkBench
    的 A ≥ B ≥ C 线模型。
    """
    by_tier: dict[str, list[float]] = {key: [] for key in ("A", "B", "C", "D")}
    for row in rows:
        tier = _tier_key(_subject_tier_value(row))
        score = _num(_subject_score(row, subject_id=subject_id, subject_name=subject_name))
        if tier and score is not None:
            by_tier[tier].append(score)
    raw = {key: (_snap_half(min(by_tier[key])) if by_tier[key] else None) for key in ("A", "B", "C")}
    # 仅对已知层做上限和单调约束；未知层仍为空。
    for key in raw:
        if raw[key] is not None:
            raw[key] = min(max(raw[key], 0.0), full_score)
    if raw["B"] is not None and raw["A"] is not None:
        raw["A"] = max(raw["A"], raw["B"])
    if raw["C"] is not None and raw["B"] is not None:
        raw["B"] = max(raw["B"], raw["C"])
    if raw["B"] is not None and raw["A"] is not None:
        raw["A"] = max(raw["A"], raw["B"])
    return {"tier_a_cutoff": raw["A"], "tier_b_cutoff": raw["B"], "tier_c_cutoff": raw["C"]}


def _list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, dict):
        return [str(item).strip() for item in value.values() if str(item).strip()]
    return [item.strip() for item in str(value).replace("；", ",").split(",") if item.strip()]


def _date(value: Any) -> date | None:
    text = _str(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _subject_score(
    row: dict[str, Any], *, subject_id: str | None = None,
    subject_name: str | None = None, preferred: str | None = None,
) -> Any:
    """读取单科成绩，避免把考试总分当成英语成绩。

    MONI 的考试总账同时可能包含 ``totalScore``/``subjectScore`` 等字段，
    但在不同版本的接口中 ``subjectScore`` 的含义并不稳定。科目学生视图
    本身已经限定在某一科，优先读取其中的 ``score`` 或显式的英语成绩字段；
    只有明确按科目键存放的字典才从总账取值，绝不使用泛化的 ``totalScore``。
    """
    aliases = [item for item in (subject_id, subject_name, "英语", "english", "English") if item]
    for container_key in ("subjectScores", "subject_scores", "scoresBySubject", "scoreBySubject"):
        container = row.get(container_key)
        if isinstance(container, dict):
            for alias in aliases:
                if alias in container and container[alias] not in (None, ""):
                    return container[alias]

    # 科目视图常见的专用字段。这里将 score 放在最前，避免被同一行中
    # 可能代表全科汇总的 subjectScore 覆盖。
    keys = tuple(filter(None, (preferred,
        "englishScore", "english_score", "英语成绩", "英语分数",
        "score", "subject_score", "subjectTotalScore", "subject_total_score",
        "subjectScore",
    )))
    value = _value(row, *keys)
    if _num(value) is not None:
        return value
    for key, candidate in row.items():
        if _is_subject_score_field(key) and _num(candidate) is not None:
            return candidate
    return None


class MoniReader:
    def __init__(self, manager: PluginManager):
        self.manager = manager

    def read(self, path: str) -> list[dict[str, Any]]:
        return _rows(_text(self.manager.call_tool("moni", "moni_vfs_read", {"file_path": path})))

    def query(self, path: str, *, limit: int = 100, predicates: list[dict[str, str]] | None = None) -> list[dict[str, Any]]:
        # MONI 的 .list.jsonl 文件支持完整只读读取；优先使用它可兼容服务端
        # 对 query_jsonl 的文本封装差异。大文件或无 read 权限时再走分页查询。
        if not predicates:
            try:
                direct = self.read(path)
                if direct:
                    return direct
            except Exception:
                pass
        result: list[dict[str, Any]] = []
        offset = 0
        max_pages = 100
        for _page in range(max_pages):
            args = {"path": path, "query": {"predicates": predicates or []}, "limit": limit, "offset": offset}
            rows = _rows(_text(self.manager.call_tool("moni", "moni_vfs_query_jsonl", args)))
            result.extend(rows)
            if len(rows) < limit:
                return result
            offset += limit
        raise RuntimeError("MONI 分页超过安全上限，服务端可能忽略 offset")


def _discover_class_root(reader: MoniReader) -> str:
    """发现 MONI 的班级根目录，避免把某个教师的路径写死。"""
    for root in ("/classes", "/class"):
        try:
            listing = _text(reader.manager.call_tool("moni", "moni_vfs_list", {"path": root}))
        except Exception:
            continue
        if "No Cym VFS node" not in listing and ".list.jsonl" in listing:
            return root
    raise RuntimeError("MONI 未找到班级目录（已尝试 /classes 和 /class）")


def _subject_id(row: dict[str, Any]) -> str | None:
    return _str(_value(row, "subjectId", "subject_id", "subjectKey", "subject_key", "subjectCode", "subject_code"))


def _is_english_subject(value: Any) -> bool:
    text = _str(value)
    if not text:
        # 部分旧 MONI 记录不带 subjectName；这时由调用方的英语工作台语境兜底。
        return True
    normalized = text.strip().lower().replace(" ", "")
    return any(token in normalized for token in ("英语", "英文", "english"))


def _subject_name_value(row: dict[str, Any]) -> str | None:
    value = _str(_value(row, "subjectName", "subject_name", "courseName", "course_name", "name"))
    if value and _is_english_subject(value):
        return value
    # 科目索引中“英语”这一业务值比字段名稳定；仅识别明确的英语文本，
    # 不会把其它普通字符串当成科目名称。
    for candidate in row.values():
        text = _str(candidate)
        if text and _is_english_subject(text):
            return text
    return value


def _analysis_grade_size(reader: MoniReader, class_root: str, class_id: str, exam_id: str, subject_id: str) -> int | None:
    """读取考试分析里声明的全年级覆盖规模（仅用于判断能否补算排名）。"""
    try:
        rows = reader.read(f"{class_root}/{class_id}/exams/{exam_id}/analysis.json")
    except Exception:
        return None
    def walk(value: Any) -> int | None:
        if isinstance(value, dict):
            sid = _str(value.get("subjectId") or value.get("subject_id"))
            name = _str(value.get("subjectName") or value.get("subject_name"))
            size = _int(value.get("gradePositionSize") or value.get("grade_position_size"))
            if size and sid == str(subject_id) and (not name or _is_english_subject(name)):
                return size
            for child in value.values():
                found = walk(child)
                if found:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = walk(child)
                if found:
                    return found
        return None
    return walk(rows)


def _make_payload(reader: MoniReader, class_row: dict[str, Any], exam_row: dict[str, Any], *, class_root: str = "/classes") -> SchoolSyncPayload | None:
    class_id = _str(_value(class_row, "classId", "class_id", "classKey", "class_key", "id"))
    exam_id = _str(_value(exam_row, "examId", "exam_id", "examKey", "exam_key", "id"))
    subject_id = _subject_id(exam_row)
    subject_name = _subject_name_value(exam_row) or "英语"
    if not class_id or not exam_id or not subject_id or not _is_english_subject(subject_name):
        return None
    base = f"{class_root}/{class_id}/exams/{exam_id}/subjects/{subject_id}"
    question_rows = reader.query(base + "/questions/.list.jsonl")
    score_rows = reader.query(base + "/question-scores/.list.jsonl")
    student_rows = reader.query(base + "/students/.list.jsonl")
    # 英语单科的官方年级排名不在 students 主表，而在同级的
    # student-progress 数据集，通过稳定 studentId 关联。
    student_progress_rows = reader.query(base + "/student-progress/.list.jsonl")
    if not student_rows:
        student_rows = [
            row for row in reader.query(f"{class_root}/{class_id}/student-data/exams/.list.jsonl")
            if _str(_value(row, "examId", "exam_id")) == exam_id and _subject_id(row) == subject_id
        ]

    # 常见键读不到时才读取字段说明。正常租户不会因此增加请求；字段名
    # 漂移的租户则可以按字段含义识别 learnerId/gradePosition 等同义键。
    student_id_field = None
    if student_rows and not any(_student_id_value(row) for row in student_rows):
        student_id_field = _schema_field_name(reader, base + "/students/.fields.jsonl", _is_student_id_field)
    student_score_field = None
    if student_rows and not any(
        _num(_subject_score(row, subject_id=subject_id, subject_name=subject_name)) is not None
        for row in student_rows
    ):
        student_score_field = _schema_field_name(reader, base + "/students/.fields.jsonl", _is_subject_score_field)
    student_tier_field = None
    if student_rows and not any(_tier_key(_subject_tier_value(row)) for row in student_rows):
        student_tier_field = _schema_field_name(reader, base + "/students/.fields.jsonl", _is_subject_tier_field)
    progress_id_field = None
    if student_progress_rows and not any(_student_id_value(row) for row in student_progress_rows):
        progress_id_field = _schema_field_name(reader, base + "/student-progress/.fields.jsonl", _is_student_id_field)
    progress_rank_field = None
    if student_progress_rows and not any(_subject_grade_rank_value(row) is not None for row in student_progress_rows):
        progress_rank_field = _schema_field_name(reader, base + "/student-progress/.fields.jsonl", _is_subject_grade_rank_field)
    progress_by_student = {
        _student_id_value(row, progress_id_field): row
        for row in student_progress_rows
        if _student_id_value(row, progress_id_field)
    }
    # 题目小分可能超过 MCP 单次窗口；空的首屏也不代表没有数据。
    # 按稳定学生 ID 补读，并再次在本地校验 ID，防止服务端忽略 predicate。
    #
    # 补读结果与首屏必须按“学生 + 题目稳定键”合并，而不是整体替换首屏：
    # 某个学生补读为空，只能说明这次没取到他这一页，不能据此丢掉其他
    # 学生已取到的小分，也不能把他本人首屏已有的事实清成空。
    score_id_field = None
    if question_rows and student_rows:
        if not score_rows or not any(_student_id_value(row) for row in score_rows):
            score_id_field = _schema_field_name(
                reader, base + "/question-scores/.fields.jsonl", _is_student_id_field,
            )
        predicate_field = score_id_field or "studentId"

        def _score_key(row: dict[str, Any]) -> tuple[str, str, str]:
            return (
                _student_id_value(row, score_id_field) or "",
                _str(_value(row, "questionNo", "question_no")),
                _str(_value(row, "subQuestionNo", "sub_question_no")),
            )

        merged_scores: dict[tuple[str, str, str], dict[str, Any]] = {}
        for row in score_rows:
            key = _score_key(row)
            if key[0]:
                merged_scores.setdefault(key, row)
        for student_row in student_rows:
            sid = _student_id_value(student_row, student_id_field)
            if not sid:
                continue
            matching = reader.query(
                base + "/question-scores/.list.jsonl",
                predicates=[{"field": predicate_field, "operator": "=", "value": sid}],
            )
            for row in matching:
                if _student_id_value(row, score_id_field) != sid:
                    continue
                merged_scores.setdefault(_score_key(row), row)
        score_rows = list(merged_scores.values())
    # 科目学生视图提供科目成绩，但排名通常在班级考试总账中；按稳定 studentId
    # 合并，不用姓名做关联。
    source_subject_id = _str(_value(exam_row, "sourceSubjectId", "subjectId"))
    aggregate_rows = reader.query(
        f"{class_root}/{class_id}/student-data/exams/.list.jsonl",
        predicates=[
            {"field": "examId", "operator": "=", "value": exam_id},
            {"field": "subjectId", "operator": "=", "value": source_subject_id or subject_id},
        ],
    )
    aggregate_by_student = {
        _student_id_value(row): row
        for row in aggregate_rows
        if _str(_value(row, "examId")) == exam_id
        and _str(_value(row, "subjectId")) in {source_subject_id, subject_id}
    }
    for row in student_rows:
        aggregate = aggregate_by_student.get(_student_id_value(row, student_id_field))
        # 总账默认是“总分”行（subjectName=总分），其中的排名不能冒充英语排名。
        # 只有接口明确标记为英语且科目 ID 对得上时才合并排名。
        aggregate_subject_id = _str(_value(aggregate or {}, "subjectId", "subject_id"))
        aggregate_name = _str(_value(aggregate or {}, "subjectName", "subject_name"))
        aggregate_is_english = bool(aggregate_name) and _is_english_subject(aggregate_name)
        aggregate_is_target = aggregate_subject_id in {subject_id, source_subject_id}
        if aggregate and aggregate_is_english and aggregate_is_target:
            for key in ("classRank", "gradeRank", "globalRank", "classSize", "gradeSize"):
                if _value(row, key) is None and _value(aggregate, key) is not None:
                    row[key] = aggregate[key]

    question_map: dict[str, dict[str, Any]] = {}
    for row in question_rows:
        qno = _str(_value(row, "questionNo", "question_no"))
        if not qno:
            continue
        sub = _str(_value(row, "subQuestionNo", "sub_question_no"))
        key = f"{qno}:{sub or ''}"
        question_map[key] = row
    for row in score_rows:
        qno = _str(_value(row, "questionNo", "question_no"))
        if not qno:
            continue
        sub = _str(_value(row, "subQuestionNo", "sub_question_no"))
        key = f"{qno}:{sub or ''}"
        question_map.setdefault(key, row)
    # MONI 可能先发布考试/学生分析元数据，题目与得分稍后才生成。
    # 这类考试仍然应导入名称、日期和学生名单；成绩保持为空。

    questions = []
    for key, row in sorted(question_map.items(), key=lambda item: (item[1].get("questionNo", ""), item[1].get("subQuestionNo", ""))):
        qno, _, sub = key.partition(":")
        max_score = _num(_value(row, "fullScore", "maxScore")) or 0
        if max_score <= 0:
            continue
        questions.append({
            "external_id": f"{exam_id}:{subject_id}:{qno}:{sub}",
            "question_no": qno,
            "sub_question_no": sub or None,
            "section_name": _str(_value(row, "sectionName")),
            "question_type": _str(_value(row, "questionType")),
            "max_score": max_score,
            "difficulty_level": _str(_value(row, "difficultyLevel")),
            "cognitive_level": _str(_value(row, "cognitiveLevel")),
            "knowledge_nodes": _list(_value(row, "knowledgeNodes")),
            "ability_nodes": _list(_value(row, "abilityNodes")),
            "pitfall_tags": _list(_value(row, "pitfallTags")),
            "teaching_blocks": _list(_value(row, "teachingBlocks")),
        })
    # questions 为空时保留 metadata-only payload，避免把全科总分误填为英语。
    question_ids = {item["question_no"] + ":" + (item["sub_question_no"] or ""): item["external_id"] for item in questions}
    item_by_student: dict[str, list[dict[str, Any]]] = {}
    for row in score_rows:
        sid = _student_id_value(row, score_id_field)
        qkey = f"{_str(_value(row, 'questionNo', 'question_no')) or ''}:{_str(_value(row, 'subQuestionNo', 'sub_question_no')) or ''}"
        if sid and qkey in question_ids:
            score = _num(_value(row, "score"))
            full = _num(_value(row, "fullScore"))
            rate = _num(_value(row, "scoreRate"))
            if rate is None and score is not None and full:
                rate = score / full
            item_by_student.setdefault(sid, []).append({
                "question_external_id": question_ids[qkey],
                "score": score,
                "score_rate": rate,
                "correct": _value(row, "correct"),
                "selected_option": _str(_value(row, "selectedOption")),
                "time_spent_ms": _int(_value(row, "timeSpent")),
                "modify_count": _int(_value(row, "modifyCount")),
                "hesitation_time_ms": _int(_value(row, "hesitationTime")),
                "teaching_blocks": _list(_value(row, "teachingBlocks")),
                "pitfall_tags": _list(_value(row, "pitfallTags")),
            })

    students = []
    for row in student_rows:
        sid = _student_id_value(row, student_id_field)
        if not sid:
            continue
        progress = progress_by_student.get(sid, {})
        absent = bool(_value(row, "absent")) or str(_value(row, "status", default="")).lower() in {"absent", "缺考"}
        total = _num(_subject_score(
            row, subject_id=subject_id, subject_name=subject_name, preferred=student_score_field,
        ))
        student = {
            "external_id": sid,
            "student_no": _str(_value(row, "studentNo", "student_no")) or sid,
            "name": _str(_value(row, "studentName", "name")) or sid,
            "status": "absent" if absent else "active",
            "total_score": None if absent else total,
            "class_rank": _subject_class_rank_value(row),
            "grade_rank": _subject_grade_rank_value(row) or _subject_grade_rank_value(progress, progress_rank_field),
            "global_rank": _int(_value(row, "globalRank")),
            "item_scores": item_by_student.get(sid, []),
        }
        students.append(student)
    if not students:
        return None
    # 根考试的 fullScore 可能是语数英等全科合计；单科英语满分以
    # subjectFullScore 或题目满分之和为准。
    full_score = _num(_value(exam_row, "subjectFullScore", "subject_full_score")) or sum(item["max_score"] for item in questions) or 100
    tier_rows = student_rows
    if student_tier_field or student_score_field:
        tier_rows = []
        for row in student_rows:
            normalized_row = dict(row)
            if student_tier_field:
                normalized_row["subjectTier"] = _value(row, student_tier_field)
            if student_score_field:
                normalized_row["subjectScore"] = _value(row, student_score_field)
            tier_rows.append(normalized_row)
    tier_cutoffs = _derive_tier_cutoffs(
        tier_rows, subject_id=subject_id, subject_name=subject_name, full_score=full_score,
    )
    name = (_str(_value(exam_row, "examName", "name")) or "MONI考试") + f"·{subject_name}"
    payload_data = {
        "source_key": "moni",
        "source_name": "MONI 学生数据",
        "snapshot_id": "moni:" + hashlib.sha256(json.dumps({"class": class_id, "exam": exam_id, "subject": subject_id, "questions": questions, "students": students}, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:32],
        "term": {"external_id": _str(_value(exam_row, "termId", "termCode")) or "moni-current", "code": _str(_value(exam_row, "termCode")), "name": _str(_value(exam_row, "termName")) or "MONI当前学期"},
        "class_info": {"external_id": class_id, "name": _str(_value(class_row, "className", "name")) or class_id},
        "exam": {"external_id": f"{exam_id}:subject:{subject_id}", "name": name, "exam_date": _date(_value(exam_row, "examDate")), "full_score": max(full_score, 0.01), "paper_revision": _str(_value(exam_row, "paperId")), **tier_cutoffs},
        "questions": questions,
        "students": students,
    }
    return SchoolSyncPayload.model_validate(payload_data)


def _make_roster(reader: MoniReader, term: dict[str, Any], classes: list[dict[str, Any]], *, class_root: str = "/classes") -> StudentRosterPayload | None:
    term_id = _str(_value(term, "termId", "term_id", "termKey", "term_key", "id")) or "moni-current"
    term_name = _str(_value(term, "termName", "term_name", "name")) or "MONI当前学期"
    class_payloads: list[dict[str, Any]] = []
    students: list[dict[str, Any]] = []
    for class_row in classes:
        class_id = _str(_value(class_row, "classId", "class_id", "classKey", "class_key", "id"))
        if not class_id:
            continue
        class_name = _str(_value(class_row, "className", "class_name", "name")) or class_id
        class_payloads.append({"external_id": class_id, "name": class_name})
        roster_rows = reader.query(f"{class_root}/{class_id}/students/.list.jsonl")
        roster_id_field = None
        if roster_rows and not any(_student_id_value(row) or _str(_value(row, "id")) for row in roster_rows):
            roster_id_field = _schema_field_name(
                reader, f"{class_root}/{class_id}/students/.fields.jsonl", _is_student_id_field,
            )
        for row in roster_rows:
            student_id = _student_id_value(row, roster_id_field) or _str(_value(row, "id"))
            if not student_id:
                continue
            students.append({
                "external_id": student_id,
                "student_no": _str(_value(row, "bizId", "studentNo", "student_no", "learnerNo", "pupilNo")) or student_id,
                "name": _str(_value(row, "name", "studentName", "student_name", "learnerName", "pupilName")) or student_id,
                "class_external_id": class_id,
                "gender": _str(_value(row, "gender")),
                "status": "active",
            })
    if not class_payloads or not students:
        return None
    return StudentRosterPayload.model_validate({
        "source_key": "moni",
        "source_name": "MONI 学生数据",
        "snapshot_id": "moni-roster:" + hashlib.sha256(json.dumps(students, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:32],
        "term": {"external_id": term_id, "code": _str(_value(term, "termCode")) or term_id, "name": term_name},
        "classes": class_payloads,
        "students": students,
    })


def _set_active_term(session: Session, term_id: int) -> None:
    from .student_profiles import prepare_student_profile_inheritance
    from .terms import current_term_id
    try:
        previous_term_id = current_term_id(session)
    except Exception:
        previous_term_id = None
    if previous_term_id != term_id:
        prepare_student_profile_inheritance(session, previous_term_id, term_id)
    setting = session.get(AppSetting, "active_term_id")
    if setting is None:
        session.add(AppSetting(key="active_term_id", value_json=term_id))
    else:
        setting.value_json = term_id


def sync_moni_roster(settings: Settings, *, dry_run: bool = False) -> dict[str, Any]:
    manager = PluginManager(settings.data_dir)
    reader = MoniReader(manager)
    class_root = _discover_class_root(reader)
    term_rows = reader.read("/school/current-term.json")
    term = term_rows[0] if term_rows else {}
    classes = reader.query(f"{class_root}/.list.jsonl")
    payload = _make_roster(reader, term, classes, class_root=class_root)
    summary = {"classes": len(payload.classes) if payload else 0, "students": len(payload.students) if payload else 0, "dry_run": dry_run}
    if dry_run or payload is None:
        return summary
    from ..database import create_session_factory, run_migrations
    run_migrations(settings.database_url)
    factory = create_session_factory(settings.database_url)
    with factory() as session:
        summary.update(apply_student_roster(session, payload))
        term_id = summary.get("term_id")
        if term_id is not None:
            _set_active_term(session, int(term_id))
            session.commit()
    summary["completed"] = True
    return summary


def _merge_exam_tier_cutoffs(payloads: list[SchoolSyncPayload]) -> None:
    """就地合并同一考试跨班级返回的 A/B/C 英语分层线。"""
    cutoffs_by_exam: dict[str, dict[str, float]] = {}
    for payload in payloads:
        merged = cutoffs_by_exam.setdefault(payload.exam.external_id, {})
        for field in ("tier_a_cutoff", "tier_b_cutoff", "tier_c_cutoff"):
            value = getattr(payload.exam, field)
            if value is not None:
                merged[field] = min(merged.get(field, value), value)
    for payload in payloads:
        for field, value in cutoffs_by_exam.get(payload.exam.external_id, {}).items():
            setattr(payload.exam, field, value)


def _derive_exam_grade_ranks(
    payloads: list[SchoolSyncPayload],
    *,
    complete_grade_scope: dict[str, bool] | None = None,
) -> None:
    """当 MONI 未返回英语年级名次时，按已授权班级的英语成绩补算名次。

    使用竞赛排名（同分并列、后续名次跳号），且不覆盖接口已经明确提供的
    英语年级名次。这样 711/712 分批同步后，WorkBench 不再显示空白名次。
    """
    by_exam: dict[str, list[tuple[float, Any]]] = {}
    for payload in payloads:
        for student in payload.students:
            if student.status in {"absent", "inactive"} or student.total_score is None:
                continue
            by_exam.setdefault(payload.exam.external_id, []).append((student.total_score, student))
    for exam_key, records in by_exam.items():
        # 仅有部分班级时，排序结果只是“已授权班级内排名”，不能写进
        # 年级排名列。官方 grade_rank 仍会保留，因为它不依赖本地覆盖范围。
        if complete_grade_scope is not None and not complete_grade_scope.get(exam_key, False):
            continue
        records.sort(key=lambda item: item[0], reverse=True)
        previous: float | None = None
        current_rank = 0
        for index, (score, student) in enumerate(records, start=1):
            if previous is None or score != previous:
                current_rank = index
                previous = score
            if student.grade_rank is None:
                student.grade_rank = current_rank


def sync_current_term(settings: Settings, *, dry_run: bool = False) -> dict[str, Any]:
    roster_summary = sync_moni_roster(settings, dry_run=dry_run)
    manager = PluginManager(settings.data_dir)
    if manager.get_plugin("moni") is None:
        raise RuntimeError("内置 MONI 插件不存在")
    reader = MoniReader(manager)
    class_root = _discover_class_root(reader)
    term_rows = reader.read("/school/current-term.json")
    term = term_rows[0] if term_rows else {}
    term_id = _str(_value(term, "termId", "term_id", "termKey", "term_key", "id"))
    classes = reader.query(f"{class_root}/.list.jsonl")
    totals = {"classes": 0, "exams": 0, "questions": 0, "students": 0, "score_students": 0, "item_scores": 0, "skipped": 0, "dry_run": dry_run, "roster_students": int(roster_summary.get("students", 0))}
    payloads: list[SchoolSyncPayload] = []
    exam_class_ids: dict[str, set[str]] = {}
    exam_grade_sizes: dict[str, int | None] = {}
    for class_row in classes:
        class_id = _str(_value(class_row, "classId", "class_id", "classKey", "class_key", "id"))
        if not class_id:
            continue
        exams = [
            row for row in reader.query(f"{class_root}/{class_id}/exams/.list.jsonl")
            if not term_id or _str(_value(row, "termId", "term_id", "termKey", "term_key")) in {None, term_id}
        ]
        for exam_row in exams:
            exam_id = _str(_value(exam_row, "examId", "exam_id", "examKey", "exam_key", "id"))
            if not exam_id:
                totals["skipped"] += 1
                continue
            subject_root = f"{class_root}/{class_id}/exams/{exam_id}/subjects"
            try:
                listing = _text(manager.call_tool("moni", "moni_vfs_list", {"path": subject_root}))
                # 科目 ID 通常是数字，但不同教师租户也可能使用字符串/UUID
                # 风格的稳定 ID；只提取目录项，不假设具体编号。
                subject_ids = re.findall(r"(?m)^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*[/\\]\s*$", listing)
            except Exception:
                subject_ids = []
            # 目录编号不是业务 subjectId；先读取科目索引，按 subjectName 精确筛选英语。
            subject_names: dict[str, str] = {}
            try:
                for subject_row in reader.query(subject_root + "/.list.jsonl"):
                    sid = _subject_id(subject_row) or _str(_value(subject_row, "id"))
                    if not sid:
                        row_values = {_str(value) for value in subject_row.values()}
                        sid = next((candidate for candidate in subject_ids if candidate in row_values), None)
                    sname = _subject_name_value(subject_row)
                    if sid and sname:
                        subject_names[sid] = sname
            except Exception:
                subject_names = {}
            # root exam 的 subjectId 是业务科目 ID，分析目录使用 subjectExamId；
            # 以服务端实际返回的数值目录为准，避免猜测目录名称。
            for subject_id in subject_ids or [_str(_value(exam_row, "subjectId"))]:
                if not subject_id:
                    continue
                subject_name = subject_names.get(str(subject_id)) or _subject_name_value(exam_row)
                # 没有科目名称时不能安全判断这是英语；跳过该节点，避免
                # 科目索引短暂不可用时把语数等其它科目导入成英语。
                if not subject_name or not _is_english_subject(subject_name):
                    continue
                row_for_subject = dict(exam_row)
                row_for_subject["sourceSubjectId"] = _str(_value(exam_row, "subjectId"))
                row_for_subject["subjectId"] = subject_id
                row_for_subject["subjectName"] = subject_name or "英语"
                payload = _make_payload(reader, class_row, row_for_subject, class_root=class_root)
                if payload is None:
                    totals["skipped"] += 1
                    continue
                payloads.append(payload)
                exam_key = payload.exam.external_id
                exam_class_ids.setdefault(exam_key, set()).add(class_id)
                if exam_key not in exam_grade_sizes:
                    exam_grade_sizes[exam_key] = _analysis_grade_size(
                        reader, class_root, class_id, exam_id, str(subject_id),
                    )
                totals["exams"] += 1
                totals["questions"] += len(payload.questions)
                totals["students"] += len(payload.students)
                totals["score_students"] += sum(1 for student in payload.students if student.total_score is not None)
                totals["item_scores"] += sum(len(student.item_scores) for student in payload.students)
        totals["classes"] += 1
    # 同一场考试可能按班级分批返回；分层线属于考试级配置，先跨班级
    # 汇总各层最低英语成绩，再交给入库层统一写入，避免 711/712 的处理
    # 顺序影响最终 A/B/C 线。
    _merge_exam_tier_cutoffs(payloads)
    complete_grade_scope = {
        exam_key: expected is not None and len(exam_class_ids.get(exam_key, set())) >= expected
        for exam_key, expected in exam_grade_sizes.items()
    }
    # 没有分析覆盖规模时保留旧的补算能力；一旦 MONI 明确声明全年级规模，
    # 就严格要求本地已授权班级覆盖该规模，避免把部分班级排名伪装成全年级排名。
    for exam_key in exam_class_ids:
        complete_grade_scope.setdefault(exam_key, True)
    _derive_exam_grade_ranks(payloads, complete_grade_scope=complete_grade_scope)
    totals["tiered_exams"] = len({
        payload.exam.external_id for payload in payloads
        if all(getattr(payload.exam, field) is not None for field in ("tier_a_cutoff", "tier_b_cutoff", "tier_c_cutoff"))
    })
    totals["grade_rank_students"] = sum(
        1 for payload in payloads for student in payload.students if student.grade_rank is not None
    )
    totals["grade_rank_scope"] = (
        "grade" if complete_grade_scope and all(complete_grade_scope.values())
        else "authorized_classes"
    )
    if dry_run or not payloads:
        return totals
    from ..database import create_session_factory, run_migrations
    run_migrations(settings.database_url)
    factory: sessionmaker[Session] = create_session_factory(settings.database_url)
    summaries = []
    with factory() as session:
        for payload in payloads:
            _, summary = apply_payload(session, payload)
            summaries.append(summary)
        term_key = _str(_value(term, "termCode")) or term_id or "moni-current"
        imported_term = session.scalar(select(Term).where(Term.code == term_key))
        if imported_term is not None:
            _set_active_term(session, imported_term.id)
        # 两个班级的 payload 分别提交后，再统一回填一次旧前端快照，
        # 确保成绩页同时覆盖 711 和 712，而不是只保留最后一批。
        rebuild_compat_exam_snapshots(
            session,
            term_key,
        )
        session.commit()
    totals["runs"] = len(summaries)
    totals["completed"] = True
    return totals
