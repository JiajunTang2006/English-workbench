"""配置驱动的通用 MCP 学校数据源。

这是本补丁要解决的核心问题：MONI 的路径、工具名与字段名都是学校专用的，换学校就
不能用。这里不假设任何具体字段名，全部由配置提供，取到的行再映射成统一的
``SchoolSyncPayload`` / ``StudentRosterPayload``，随后复用学校同步服务既有的校验、
幂等与教师覆盖保护——因此「换学校」只需要改配置。

取数原则：
- 只读，不做任何写入；
- 取不到的字段留空（``None``），不用 0 顶替；关键字段缺失时跳过该条并计入 warnings；
- 科目过滤必须显式配置，否则无法安全判断哪一列是英语，宁可不同步也不猜。
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any

from ...config import Settings
from ...schemas.school_sync import SchoolSyncPayload, StudentRosterPayload
from ..plugin_manager import PluginManager, PluginValidationError
from . import runner
from .config import GenericSourceConfig, SourceConfigError, default_token_profile, parse_config
from .fields import FieldMapError, as_str_list, pick
from .mcp_text import mcp_rows, mcp_text
from .tiers import derive_tier_cutoffs, normalize_tier


class GenericSourceError(RuntimeError):
    """通用数据源运行期错误（连接失败、返回结构不符合映射等）。"""


_VALID_STATUS = {"active", "inactive", "absent", "excused"}


def _as_date(value: Any) -> date | None:
    if value is None or isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for candidate in (text[:10], text):
        try:
            return date.fromisoformat(candidate)
        except ValueError:
            continue
    return None


def _as_status(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in _VALID_STATUS else "active"


# 分类字段与统一合同字段名的对应。这些字段在各学校的叫法差异最大，取值口径也
# 常不同——字段名靠 field_map 映射，取值靠 value_aliases（解析时并入 enum）归一。
_CATEGORY_FIELD_MAP = (
    ("question.difficulty", "difficulty_level"),
    ("question.cognitive", "cognitive_level"),
)

_TAG_FIELD_MAP = (
    ("knowledge", "knowledge_nodes"),
    ("ability", "ability_nodes"),
    ("pitfall", "pitfall_tags"),
    ("teaching_block", "teaching_blocks"),
)


def _tag_fields(field_map: Any, row: Any, *, prefix: str) -> dict[str, Any]:
    """取标签类字段（list[str]）。

    未配置的字段直接省略，让统一合同的默认空列表生效；配了但取不到值也省略，
    **不写空字符串占位**，避免下游把空串当成一个标签。
    """
    result: dict[str, Any] = {}
    for suffix, key in _TAG_FIELD_MAP:
        value = field_map.value(row, f"{prefix}.{suffix}")
        if value is None:
            continue
        tags = as_str_list(value)
        if tags:
            result[key] = tags
    return result


def _category_fields(field_map: Any, row: Any) -> dict[str, Any]:
    """题目的分类字段：难度、认知层级与四类标签。"""
    result: dict[str, Any] = {}
    for logical, key in _CATEGORY_FIELD_MAP:
        value = field_map.value(row, logical)
        if value is not None:
            result[key] = value
    result.update(_tag_fields(field_map, row, prefix="question"))
    return result


def _as_exam_kind(value: Any) -> str:
    """统一合同的 ``exam_kind`` 只有 entrance / regular 两种。

    取值已经过 ``value_aliases`` 归一；这里再兜一层常见中文叫法，其余一律按常规考试，
    避免把「入学考」当成普通月考、影响成长树里入学基线的取用。
    """
    text = str(value or "").strip().lower()
    if text in {"entrance", "entry"}:
        return "entrance"
    if text in {"入学", "入学考", "入学考试", "分班考", "分班考试"}:
        return "entrance"
    return "regular"


def _explicit_cutoffs(field_map: Any, row: Any) -> dict[str, float | None]:
    """上游直接给出分层线时按原值取用；取不到或明显越界一律留空，不猜。"""
    result: dict[str, float | None] = {}
    for logical, key in (
        ("exam.tier_a_cutoff", "tier_a_cutoff"),
        ("exam.tier_b_cutoff", "tier_b_cutoff"),
        ("exam.tier_c_cutoff", "tier_c_cutoff"),
    ):
        value = field_map.value(row, logical)
        number: float | None = None
        if value is not None:
            try:
                number = float(value)
            except (TypeError, ValueError):
                number = None
        result[key] = number if number is not None and 0 <= number <= 1000 else None
    return result


class GenericMcpSource:
    """一个「自定义 MCP 数据源」的适配器实例。"""

    kind = "generic_mcp"

    def __init__(self, settings: Settings, *, source_key: str, name: str,
                 raw_config: dict[str, Any] | None, enabled: bool = True) -> None:
        self.settings = settings
        self.source_key = source_key
        self.name = name
        self.enabled = enabled
        self.config: GenericSourceConfig = parse_config(raw_config, source_key=source_key)
        self.warnings: list[str] = []

    # ---- 与 MCP 宿主交互 ----

    @property
    def plugin_id(self) -> str:
        return self.config.plugin_id

    def _manager(self) -> PluginManager:
        return PluginManager(self.settings.data_dir)

    def _call(self, tool_key: str, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        manager = self._manager()
        tool_name = self.config.tool(tool_key)
        try:
            result = manager.call_tool(self.plugin_id, tool_name, arguments)
        except (KeyError, PluginValidationError) as exc:
            raise GenericSourceError(f"数据源调用失败：{exc}") from exc
        return mcp_rows(mcp_text(result))

    def _read(self, template: str, **values: str) -> list[dict[str, Any]]:
        path = self.config.expand(template, **values)
        return self._call("read", {"file_path": path})

    def _query(self, template: str, **values: str) -> list[dict[str, Any]]:
        path = self.config.expand(template, **values)
        # 与 MONI 适配器一致的兼容策略：先尝试整文件读取（服务端对 query 的封装
        # 存在差异），拿不到再退回分页查询。
        try:
            direct = self._read(template, **values)
            if direct:
                return direct
        except GenericSourceError:
            pass
        rows: list[dict[str, Any]] = []
        offset = 0
        limit = 200
        for _page in range(100):
            page = self._call("query", {
                "path": path,
                "query": {"predicates": []},
                "limit": limit,
                "offset": offset,
            })
            rows.extend(page)
            if len(page) < limit:
                return rows
            offset += limit
        raise GenericSourceError("数据源分页超过安全上限，服务端可能忽略了 offset")

    def health(self) -> dict[str, Any]:
        manager = self._manager()
        try:
            result = manager.health_check(self.plugin_id)
        except (KeyError, PluginValidationError) as exc:
            return {"health": "unavailable", "error": str(exc), "tool_count": 0}
        return {
            "health": result.get("health"),
            "error": result.get("error"),
            "tool_count": len(result.get("tools") or []),
            "tools": [item.get("name") for item in (result.get("tools") or []) if isinstance(item, dict)],
        }

    # ---- 取值与映射 ----

    def _resolve_term(self, class_rows: list[dict[str, Any]]) -> dict[str, Any]:
        """学期优先取配置里的 term 文件，其次取显式配置，最后从班级行里找。"""
        term_path = self.config.path("term")
        row: dict[str, Any] = {}
        if term_path:
            rows = self._read(term_path)
            row = rows[0] if rows else {}
        source = row or (class_rows[0] if class_rows else {})
        field_map = self.config.field_map
        external_id = (
            field_map.value(source, "term.external_id")
            or (self.config.term.external_id if self.config.term else None)
        )
        if not external_id:
            raise SourceConfigError(
                "无法确定学期：请配置 paths.term 或 term.external_id，"
                "否则导入的数据没有学期归属"
            )
        return {
            "external_id": str(external_id),
            "name": str(field_map.value(source, "term.name") or (
                self.config.term.name if self.config.term else external_id)),
            "code": field_map.value(source, "term.code") or (
                self.config.term.code if self.config.term else None) or str(external_id),
        }

    def _class_payload(self, row: dict[str, Any]) -> dict[str, Any] | None:
        field_map = self.config.field_map
        external_id = field_map.value(row, "class.external_id")
        if external_id is None:
            return None
        name = field_map.value(row, "class.name") or str(external_id)
        return {
            "external_id": str(external_id),
            "name": str(name),
            "grade": field_map.value(row, "class.grade"),
            "school_year": field_map.value(row, "class.school_year"),
        }

    def _question_payload(self, row: dict[str, Any]) -> dict[str, Any] | None:
        field_map = self.config.field_map
        external_id = field_map.value(row, "question.external_id")
        max_score = field_map.value(row, "question.max_score")
        if external_id is None or max_score is None:
            # 没有题号或满分就无法建立逐题证据链，跳过而不是编造一个满分。
            return None
        try:
            max_score_value = float(max_score)
        except (TypeError, ValueError):
            return None
        if not 0 < max_score_value <= 1000:
            return None
        return {
            "external_id": str(external_id),
            "question_no": str(field_map.value(row, "question.no") or external_id),
            "sub_question_no": field_map.value(row, "question.sub_no"),
            "section_name": field_map.value(row, "question.section"),
            "question_type": field_map.value(row, "question.type"),
            "content_text": field_map.value(row, "question.content"),
            "max_score": max_score_value,
            "correct_answer": field_map.value(row, "question.answer"),
            **_category_fields(field_map, row),
        }

    def _item_scores(self, student_row: dict[str, Any], known_questions: set[str]) -> list[dict[str, Any]]:
        field_map = self.config.field_map
        if not field_map.has("item.list"):
            return []
        raw_items = field_map.value(student_row, "item.list")
        if not isinstance(raw_items, list):
            return []
        items: list[dict[str, Any]] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            question_id = field_map.value(raw, "item.question_external_id")
            if question_id is None or str(question_id) not in known_questions:
                # 引用不存在的题目会在入库时整条失败，这里提前跳过并记警告。
                self.warnings.append("逐题得分引用了未同步的题目，已跳过该题")
                continue
            score = field_map.value(raw, "item.score")
            rate = field_map.value(raw, "item.score_rate")
            item: dict[str, Any] = {"question_external_id": str(question_id)}
            if score is not None:
                try:
                    item["score"] = float(score)
                except (TypeError, ValueError):
                    pass
            if rate is not None:
                try:
                    rate_value = float(rate)
                except (TypeError, ValueError):
                    rate_value = None
                if rate_value is not None and 0 <= rate_value <= 1:
                    item["score_rate"] = rate_value
            for field, key in (
                ("item.answer", "student_answer"),
                ("item.correct", "correct"),
                ("item.selected_option", "selected_option"),
            ):
                value = field_map.value(raw, field)
                if value is not None:
                    item[key] = value
            for field, key in (
                ("item.time_spent_ms", "time_spent_ms"),
                ("item.modify_count", "modify_count"),
                ("item.hesitation_time_ms", "hesitation_time_ms"),
            ):
                value = field_map.value(raw, field)
                if value is None:
                    continue
                try:
                    number = int(float(value))
                except (TypeError, ValueError):
                    continue
                # 耗时/修改次数/犹豫时间都是非负计数，负值说明口径不对，留空。
                if number >= 0:
                    item[key] = number
            item.update(_tag_fields(field_map, raw, prefix="item"))
            items.append(item)
        return items

    def _student_payload(self, row: dict[str, Any], *, class_external_id: str,
                         known_questions: set[str]) -> dict[str, Any] | None:
        field_map = self.config.field_map
        external_id = field_map.value(row, "student.external_id")
        if external_id is None:
            return None
        name = field_map.value(row, "student.name")
        if name is None:
            # 没有姓名无法在界面上呈现，也容易与同名学生混淆，跳过并记警告。
            self.warnings.append("有学生缺少姓名，已跳过该条")
            return None
        payload: dict[str, Any] = {
            "external_id": str(external_id),
            "student_no": str(field_map.value(row, "student.student_no") or external_id),
            "name": str(name),
            "gender": field_map.value(row, "student.gender"),
            "status": _as_status(field_map.value(row, "student.status")),
        }
        for field, key in (
            ("student.total_score", "total_score"),
            ("student.class_rank", "class_rank"),
            ("student.grade_rank", "grade_rank"),
            ("student.global_rank", "global_rank"),
        ):
            value = field_map.value(row, field)
            if value is None:
                continue
            try:
                payload[key] = float(value) if key == "total_score" else int(float(value))
            except (TypeError, ValueError):
                continue
        payload["item_scores"] = self._item_scores(row, known_questions)
        return payload

    # ---- 抓取 ----

    def fetch_roster(self) -> StudentRosterPayload | None:
        """只取名册（学生 + 班级），不取成绩。未配置名册路径时返回 ``None``。"""
        roster_path = self.config.path("roster")
        if not roster_path:
            return None
        class_rows = self._query(self.config.path("classes") or "")
        term = self._resolve_term(class_rows)
        classes: list[dict[str, Any]] = []
        students: list[dict[str, Any]] = []
        field_map = self.config.field_map
        for class_row in class_rows:
            class_payload = self._class_payload(class_row)
            if class_payload is None:
                self.warnings.append("有班级缺少 external_id，已跳过该班级")
                continue
            classes.append(class_payload)
            for student_row in self._read(roster_path, class_id=class_payload["external_id"]):
                external_id = field_map.value(student_row, "student.external_id")
                name = field_map.value(student_row, "student.name")
                if external_id is None or name is None:
                    self.warnings.append(f"班级 {class_payload['name']} 有学生缺少标识或姓名，已跳过")
                    continue
                students.append({
                    "external_id": str(external_id),
                    "student_no": str(field_map.value(student_row, "student.student_no") or external_id),
                    "name": str(name),
                    "class_external_id": class_payload["external_id"],
                    "gender": field_map.value(student_row, "student.gender"),
                    "status": _as_status(field_map.value(student_row, "student.status")),
                })
        if not classes or not students:
            return None
        return StudentRosterPayload.model_validate({
            "source_key": self.source_key,
            "source_name": self.name,
            "snapshot_id": self._snapshot_id("roster", term["external_id"], students),
            "term": term,
            "classes": classes,
            "students": students,
        })

    def fetch_exam_payloads(self) -> list[SchoolSyncPayload]:
        """按班级逐场考试取数并转成统一 payload。未配置考试路径时返回空列表。"""
        exam_path = self.config.path("exams")
        student_path = self.config.path("students")
        if not exam_path or not student_path:
            return []
        class_rows = self._query(self.config.path("classes") or "")
        term = self._resolve_term(class_rows)
        payloads: list[SchoolSyncPayload] = []
        for class_row in class_rows:
            class_payload = self._class_payload(class_row)
            if class_payload is None:
                self.warnings.append("有班级缺少 external_id，已跳过该班级")
                continue
            for exam_row in self._query(exam_path, class_id=class_payload["external_id"]):
                if self.config.subject_filter and not self.config.subject_filter.matches(exam_row):
                    continue
                payload = self._exam_payload(
                    exam_row, class_payload=class_payload, term=term,
                    student_path=student_path)
                if payload is not None:
                    payloads.append(payload)
        return payloads

    def _exam_payload(self, exam_row: dict[str, Any], *, class_payload: dict[str, Any],
                      term: dict[str, Any], student_path: str) -> SchoolSyncPayload | None:
        field_map = self.config.field_map
        exam_external_id = field_map.value(exam_row, "exam.external_id")
        if exam_external_id is None:
            self.warnings.append("有考试缺少 external_id，已跳过该场考试")
            return None
        class_id = class_payload["external_id"]
        exam_id = str(exam_external_id)
        raw_full_score = field_map.value(exam_row, "exam.full_score")
        try:
            full_score = float(raw_full_score) if raw_full_score is not None else None
        except (TypeError, ValueError):
            full_score = None
        if full_score is None:
            full_score = self.config.full_score
        if full_score is None or not 0 < full_score <= 1000:
            # 没有满分就无法计算得分率，也无法判断分层；宁可跳过并提示补配置。
            self.warnings.append(
                f"考试「{field_map.value(exam_row, 'exam.name') or exam_id}」缺少满分，"
                "请配置 exam.full_score 或顶层 full_score"
            )
            return None

        question_rows: list[dict[str, Any]] = []
        questions: list[dict[str, Any]] = []
        question_path = self.config.path("questions")
        if question_path:
            question_rows = self._read(question_path, class_id=class_id, exam_id=exam_id)
            for row in question_rows:
                built = self._question_payload(row)
                if built is not None:
                    questions.append(built)
        known_questions = {item["external_id"] for item in questions}

        students: list[dict[str, Any]] = []
        tier_pairs: list[tuple[str | None, float | None]] = []
        for student_row in self._read(student_path, class_id=class_id, exam_id=exam_id):
            built = self._student_payload(
                student_row, class_external_id=class_id, known_questions=known_questions)
            if built is not None:
                students.append(built)
            tier_value = field_map.value(student_row, "student.tier")
            if tier_value is not None:
                # 分层只用于推导 A/B/C 线，与 MONI 同口径；归一失败记 None 不猜层。
                tier_pairs.append((
                    normalize_tier(tier_value, self.config.tier_aliases),
                    built.get("total_score") if built else None,
                ))
        if not students:
            self.warnings.append(f"考试「{exam_id}」没有取到任何学生成绩，已跳过")
            return None
        if tier_pairs and not any(tier for tier, _ in tier_pairs):
            self.warnings.append(
                f"考试「{exam_id}」的分层字段有值，但没有一个能归一到 A/B/C/D，"
                "分层线保持为空（可在「分类值对照」里补别名）"
            )

        exam_name = field_map.value(exam_row, "exam.name") or exam_id
        cutoffs = _explicit_cutoffs(field_map, exam_row)
        if tier_pairs:
            derived = derive_tier_cutoffs(tier_pairs, full_score=full_score)
            for key, value in derived.items():
                # 上游直接给的分层线优先，推导只补空缺。
                if cutoffs.get(key) is None:
                    cutoffs[key] = value
        payload_data = {
            "source_key": self.source_key,
            "source_name": self.name,
            "snapshot_id": self._snapshot_id("exam", class_id, exam_id, students, questions),
            "term": term,
            "class_info": class_payload,
            "exam": {
                "external_id": exam_id,
                "name": str(exam_name),
                "exam_date": _as_date(field_map.value(exam_row, "exam.date")),
                "full_score": full_score,
                "paper_revision": field_map.value(exam_row, "exam.paper_revision"),
                "exam_kind": _as_exam_kind(field_map.value(exam_row, "exam.kind")),
                **cutoffs,
            },
            "questions": questions,
            "students": students,
        }
        try:
            return SchoolSyncPayload.model_validate(payload_data)
        except ValueError as exc:
            # 把 Pydantic 的校验失败翻译成「哪个考试有问题」，避免整批同步无提示失败。
            raise GenericSourceError(f"考试「{exam_name}」的数据不符合统一合同：{exc}") from exc

    def _snapshot_id(self, kind: str, *parts: Any) -> str:
        digest = hashlib.sha256(
            json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str).encode()
        ).hexdigest()[:32]
        return f"{self.source_key}:{kind}:{digest}"

    # ---- 同步 ----

    def sync(self, *, dry_run: bool = False) -> dict[str, Any]:
        """抓取并入库；``dry_run`` 时只返回统计，不写库。"""
        if not self.enabled:
            raise GenericSourceError("该数据源已停用")
        self.warnings = []
        totals: dict[str, Any] = {
            "source_key": self.source_key,
            "classes": 0,
            "exams": 0,
            "students": 0,
            "questions": 0,
            "score_students": 0,
            "dry_run": dry_run,
        }
        roster = self.fetch_roster()
        payloads = self.fetch_exam_payloads()
        if roster is not None:
            totals["roster_students"] = len(roster.students)
            totals["classes"] = len(roster.classes)
        elif payloads:
            totals["classes"] = len({payload.class_info.external_id for payload in payloads})
        for payload in payloads:
            totals["exams"] += 1
            totals["questions"] += len(payload.questions)
            totals["students"] += len(payload.students)
            totals["score_students"] += sum(
                1 for student in payload.students if student.total_score is not None)
        totals["warnings"] = list(dict.fromkeys(self.warnings))
        if dry_run:
            return totals
        if roster is not None:
            roster_summary = runner.apply_roster_payload(self.settings, roster)
            totals["roster_applied"] = {
                key: value for key, value in roster_summary.items() if isinstance(value, int)
            }
        if payloads:
            summaries = runner.apply_payload_batch(
                self.settings, payloads, term_code=payloads[0].term.code)
            totals["runs"] = len(summaries)
        totals["completed"] = True
        return totals


def build_source(settings: Settings, *, source_key: str, name: str,
                 raw_config: dict[str, Any] | None, enabled: bool = True) -> GenericMcpSource:
    """构造适配器；配置不合法时把 ``FieldMapError`` 统一成 ``SourceConfigError``。"""
    try:
        return GenericMcpSource(
            settings, source_key=source_key, name=name, raw_config=raw_config, enabled=enabled)
    except FieldMapError as exc:
        raise SourceConfigError(f"field_map 不合法：{exc}") from exc


__all__ = [
    "GenericMcpSource",
    "GenericSourceError",
    "build_source",
    "default_token_profile",
    "pick",
]
