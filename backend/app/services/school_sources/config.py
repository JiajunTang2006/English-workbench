"""自定义 MCP 数据源的配置解析与校验。

换学校时只需要改这份配置，不需要改代码。配置存在 ``SchoolDataSource.config_json``，
敏感令牌单独放 keyvault（配置里只留一个 profile 名），所以这里也负责给出
「可以安全展示给前端」的配置视图。

配置结构（全部为 JSON）::

    {
      "endpoint": "https://mcp.example.edu/api/mcp",
      "headers": {"X-Tenant": "hz-001"},
      "auth": {"type": "bearer", "token_profile": "school-hz-001"},
      "tools": {"list": "vfs_list", "read": "vfs_read", "query": "vfs_query_jsonl"},
      "paths": {
        "term":     "/school/current-term.json",
        "classes":  "/classes/.list.jsonl",
        "roster":   "/classes/{class_id}/students/.list.jsonl",
        "exams":    "/classes/{class_id}/exams/.list.jsonl",
        "students": "/classes/{class_id}/exams/{exam_id}/students/.list.jsonl"
      },
      "term": {"external_id": "2026-S1", "name": "2026学年第一学期", "code": "2026-S1"},
      "subject_filter": {"field": "subjectName", "any_of": ["英语", "English"]},
      "full_score": 100,
      "field_map": { "exam.external_id": ["examId", "id"], ... },
      "value_aliases": {
        "question.knowledge": {"宾语从句": ["宾语从句(that)", "Object Clause"]}
      },
      "tier_aliases": {"甲等": "A", "乙等": "B"}
    }

约定：
- 路径里的 ``{class_id}`` / ``{exam_id}`` 是占位符，由适配器逐层替换。
- ``paths.classes`` 必填；``paths.roster`` 与 ``paths.exams`` 至少提供一个，
  否则没有任何可同步的内容。
- 取不到的字段一律留空（``None``），不用 0 顶替；关键字段用 ``required`` 显式报错。
- ``value_aliases`` 是给教师看的「统一叫法 → 上游可能叫法」对照表，解析时反转成
  ``field_map`` 里引擎使用的 ``enum`` 表。同一个上游叫法被映射成两个不同统一值时
  直接报错，不做「谁先谁赢」的静默取舍。
- ``tier_aliases`` 覆盖 ``school_sources.tiers`` 的默认分层别名（叠加，同名覆盖）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from .fields import FieldMap, FieldMapError, normalize_key
from .tiers import TIER_LEVELS

# 会被遮蔽展示的请求头关键字：只要名字里带这些词就不回显原值。
_SECRET_HEADER_HINTS = (
    "authorization", "api-key", "apikey", "token", "secret", "password", "credential", "key",
)
SUPPORTED_TRANSPORTS = ("http",)
DEFAULT_TOOLS = {"list": "vfs_list", "read": "vfs_read", "query": "vfs_query_jsonl"}

# 内置来源的历史令牌档案名：MONI 早期直接用了 "moni"，不能改，否则已保存的
# 令牌会读不到。自定义数据源统一用 ``school-<source_key>``，彼此不互相覆盖。
BUILTIN_TOKEN_PROFILES = {"moni": "moni"}


class SourceConfigError(ValueError):
    """数据源配置不合法。消息面向教师，必须说明「改哪里」。"""


@dataclass(frozen=True)
class TermSpec:
    external_id: str
    name: str
    code: str | None = None


@dataclass(frozen=True)
class SubjectFilter:
    """只同步指定科目的成绩，避免把语数等其它科目当成英语导入。"""

    field: str
    any_of: tuple[str, ...]

    def matches(self, row: Any) -> bool:
        from .fields import get_path

        value = get_path(row, self.field)
        if value is None:
            return False
        text = str(value).strip().casefold()
        return any(text == candidate.strip().casefold() for candidate in self.any_of)


@dataclass
class GenericSourceConfig:
    endpoint: str
    headers: dict[str, str] = field(default_factory=dict)
    auth_type: str = "none"
    token_profile: str | None = None
    tools: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_TOOLS))
    paths: dict[str, str] = field(default_factory=dict)
    term: TermSpec | None = None
    subject_filter: SubjectFilter | None = None
    full_score: float | None = None
    field_map: FieldMap = field(default_factory=FieldMap)
    # 教师原样填写的 field_map（未并入 value_aliases 反推出的 enum 表）。
    # 回显给前端时用这一份，避免对照表在字段映射里再出现一次、越存越乱。
    raw_field_map: dict[str, Any] = field(default_factory=dict)
    value_aliases: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    tier_aliases: dict[str, str] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def plugin_id(self) -> str:
        return f"school-{self.extra['source_key']}"

    def path(self, key: str) -> str | None:
        value = self.paths.get(key)
        return value if isinstance(value, str) and value.strip() else None

    def tool(self, key: str) -> str:
        return self.tools.get(key) or DEFAULT_TOOLS[key]

    def expand(self, template: str, **values: str) -> str:
        try:
            return template.format(**values)
        except KeyError as exc:
            raise SourceConfigError(
                f"路径模板 {template} 使用了未定义的占位符 {exc.args[0]}"
            ) from exc

    def redacted_headers(self) -> dict[str, str]:
        return {
            name: ("********" if _is_secret_header(name) else value)
            for name, value in self.headers.items()
        }


def _is_secret_header(name: str) -> bool:
    lowered = name.lower()
    return any(hint in lowered for hint in _SECRET_HEADER_HINTS)


def _as_str_map(value: Any, label: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise SourceConfigError(f"{label} 必须是对象")
    return {str(key): str(item) for key, item in value.items()}


# ---- 逻辑字段清单 ----
#
# 与前端字段映射模板保持同一份命名。value_aliases 的键必须落在这里，否则
# 教师把字段名写错时不会有任何效果、也看不到提示。

KNOWN_FIELDS = (
    # 学期 / 班级
    "term.external_id", "term.name", "term.code",
    "class.external_id", "class.name", "class.grade",
    # 考试
    "exam.external_id", "exam.name", "exam.date", "exam.full_score",
    "exam.paper_revision", "exam.kind",
    "exam.tier_a_cutoff", "exam.tier_b_cutoff", "exam.tier_c_cutoff",
    # 题目（含分类字段）
    "question.external_id", "question.no", "question.sub_no", "question.section",
    "question.type", "question.content", "question.answer", "question.max_score",
    "question.difficulty", "question.cognitive", "question.knowledge",
    "question.ability", "question.pitfall", "question.teaching_block",
    # 逐题作答
    "item.list", "item.question_external_id", "item.score", "item.score_rate",
    "item.answer", "item.correct", "item.selected_option",
    "item.time_spent_ms", "item.modify_count", "item.hesitation_time_ms",
    "item.pitfall", "item.teaching_block",
    # 学生
    "student.external_id", "student.name", "student.student_no", "student.gender",
    "student.status", "student.total_score", "student.class_rank",
    "student.grade_rank", "student.global_rank", "student.tier",
)

# 这些字段在统一合同里是 ``list[str]``，归一必须逐元素做。
KNOWN_LIST_FIELDS = (
    "question.knowledge", "question.ability", "question.pitfall", "question.teaching_block",
    "item.pitfall", "item.teaching_block",
)


def _parse_value_aliases(raw: Any) -> dict[str, dict[str, list[str]]]:
    """解析「统一叫法 → 上游可能叫法」对照表，逐项校验并给出可执行的错误提示。"""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise SourceConfigError("value_aliases 必须是对象")
    parsed: dict[str, dict[str, list[str]]] = {}
    for raw_field, mapping in raw.items():
        name = str(raw_field).strip()
        if name not in KNOWN_FIELDS:
            raise SourceConfigError(
                f"value_aliases 里的「{name}」不是可用的字段名，请使用字段映射里的逻辑字段名"
            )
        if not isinstance(mapping, dict):
            raise SourceConfigError(
                f"value_aliases.{name} 必须是「统一叫法: [上游叫法, ...]」的对象"
            )
        groups: dict[str, list[str]] = {}
        for raw_canonical, raw_upstream in mapping.items():
            canonical = str(raw_canonical).strip()
            if not canonical:
                raise SourceConfigError(f"value_aliases.{name} 里的统一叫法不能为空")
            if not isinstance(raw_upstream, (list, tuple)) or not raw_upstream:
                raise SourceConfigError(
                    f"value_aliases.{name} 的「{canonical}」需要非空的上游叫法列表"
                )
            upstream_names = [str(item).strip() for item in raw_upstream if str(item).strip()]
            if not upstream_names:
                raise SourceConfigError(
                    f"value_aliases.{name} 的「{canonical}」需要非空的上游叫法列表"
                )
            groups[canonical] = upstream_names
        if groups:
            parsed[name] = groups
    return parsed


def _merge_value_aliases(field_map_raw: Any, aliases: dict[str, dict[str, list[str]]]) -> Any:
    """把对照表反转成 ``enum`` 表并合并进 ``field_map``（返回新对象，不改入参）。"""
    if not aliases:
        return field_map_raw
    if field_map_raw is None:
        spec: dict[str, Any] = {}
    elif isinstance(field_map_raw, dict):
        spec = {str(key): value for key, value in field_map_raw.items()}
    else:
        raise SourceConfigError("field_map 必须是对象")

    for name, groups in aliases.items():
        if name not in spec:
            raise SourceConfigError(
                f"value_aliases 里的「{name}」还没有在 field_map 里说明读哪一列，"
                "请先在字段映射里配置这个字段"
            )
        existing = spec[name]
        if isinstance(existing, str):
            entry: dict[str, Any] = {"path": existing}
        elif isinstance(existing, (list, tuple)):
            entry = {"path": [str(item) for item in existing]}
        elif isinstance(existing, dict):
            entry = dict(existing)
        else:
            raise SourceConfigError(f"field_map.{name} 必须是字符串、列表或对象")

        enum_table: dict[str, Any] = {}
        raw_enum = entry.get("enum")
        if raw_enum is not None:
            if not isinstance(raw_enum, dict):
                raise SourceConfigError(f"field_map.{name}.enum 必须是对象")
            enum_table.update({str(key): value for key, value in raw_enum.items()})

        for canonical, upstream_names in groups.items():
            for upstream in upstream_names:
                key = normalize_key(upstream)
                for existing_key, existing_value in enum_table.items():
                    if normalize_key(existing_key) == key and str(existing_value) != canonical:
                        raise SourceConfigError(
                            f"「{upstream}」在 {name} 里被同时映射成"
                            f"「{existing_value}」和「{canonical}」，请只保留一个"
                        )
                enum_table[upstream] = canonical

        entry["enum"] = enum_table
        if name in KNOWN_LIST_FIELDS:
            # 这些字段在统一合同里就是列表，逐元素归一才有意义。
            entry.setdefault("list", True)
        spec[name] = entry
    return spec


def _parse_tier_aliases(raw: Any) -> dict[str, str]:
    """解析分层别名覆盖表；目标层只允许 A/B/C/D。"""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise SourceConfigError("tier_aliases 必须是对象")
    result: dict[str, str] = {}
    for raw_label, raw_level in raw.items():
        label = str(raw_label).strip()
        if not label:
            raise SourceConfigError("tier_aliases 里的上游叫法不能为空")
        level = str(raw_level).strip().upper()
        if level not in TIER_LEVELS:
            raise SourceConfigError(
                f"tier_aliases 的「{label}」目标只能是 {'/'.join(TIER_LEVELS)}"
            )
        result[label] = level
    return result


def parse_config(raw: dict[str, Any] | None, *, source_key: str | None = None) -> GenericSourceConfig:
    """解析并校验配置；任何问题都抛 ``SourceConfigError`` 并说明改哪里。"""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise SourceConfigError("数据源配置必须是 JSON 对象")

    transport = str(raw.get("transport") or "http").strip().lower()
    if transport not in SUPPORTED_TRANSPORTS:
        raise SourceConfigError(f"transport 仅支持 {'、'.join(SUPPORTED_TRANSPORTS)}")

    endpoint = str(raw.get("endpoint") or raw.get("url") or "").strip()
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SourceConfigError("endpoint 必须是有效的 http/https 地址")

    headers = _as_str_map(raw.get("headers"), "headers")

    auth_raw = raw.get("auth")
    auth_type = "none"
    token_profile: str | None = None
    if auth_raw is not None:
        if not isinstance(auth_raw, dict):
            raise SourceConfigError("auth 必须是对象")
        auth_type = str(auth_raw.get("type") or "none").strip().lower()
        if auth_type not in {"none", "bearer"}:
            raise SourceConfigError("auth.type 目前仅支持 none 或 bearer")
        if auth_type == "bearer":
            token_profile = str(auth_raw.get("token_profile") or "").strip() or None
            if token_profile is None:
                raise SourceConfigError("auth.type=bearer 时必须填写 auth.token_profile")
            if source_key and token_profile != _default_token_profile(source_key):
                # 令牌档案名与数据源标识绑定，避免两个数据源互相覆盖令牌。
                raise SourceConfigError(
                    f"auth.token_profile 必须与本数据源标识一致（应为 {_default_token_profile(source_key)}）"
                )

    tools = dict(DEFAULT_TOOLS)
    tools.update({key: str(value) for key, value in _as_str_map(raw.get("tools"), "tools").items()})

    paths = _as_str_map(raw.get("paths"), "paths")
    if not paths.get("classes"):
        raise SourceConfigError("paths.classes 不能为空（至少需要一个班级列表路径）")
    if not paths.get("exams") and not paths.get("roster"):
        raise SourceConfigError("paths.exams 与 paths.roster 至少填写一个，否则没有可同步的内容")

    term: TermSpec | None = None
    term_raw = raw.get("term")
    if term_raw is not None:
        if not isinstance(term_raw, dict):
            raise SourceConfigError("term 必须是对象")
        external_id = str(term_raw.get("external_id") or term_raw.get("id") or "").strip()
        name = str(term_raw.get("name") or external_id).strip()
        if external_id:
            term = TermSpec(external_id=external_id, name=name or external_id,
                            code=str(term_raw.get("code") or "").strip() or None)

    subject_filter: SubjectFilter | None = None
    filter_raw = raw.get("subject_filter")
    if filter_raw is not None:
        if not isinstance(filter_raw, dict):
            raise SourceConfigError("subject_filter 必须是对象")
        filter_field = str(filter_raw.get("field") or "").strip()
        any_of = filter_raw.get("any_of")
        if not filter_field or not isinstance(any_of, list) or not any_of:
            raise SourceConfigError("subject_filter 需要 field 与非空 any_of 列表")
        subject_filter = SubjectFilter(
            field=filter_field, any_of=tuple(str(item) for item in any_of))

    full_score: float | None = None
    if raw.get("full_score") is not None:
        try:
            full_score = float(raw["full_score"])
        except (TypeError, ValueError) as exc:
            raise SourceConfigError("full_score 必须是数字") from exc
        if not 0 < full_score <= 1000:
            raise SourceConfigError("full_score 需在 0 到 1000 之间")

    # 值域对照表先解析成「统一叫法 → 上游叫法」，再反转合并进 field_map 的 enum 表。
    value_aliases = _parse_value_aliases(raw.get("value_aliases"))
    tier_aliases = _parse_tier_aliases(raw.get("tier_aliases"))
    raw_field_map = raw.get("field_map")
    if raw_field_map is None:
        raw_field_map = {}
    elif not isinstance(raw_field_map, dict):
        raise SourceConfigError("field_map 必须是对象")
    merged_field_map = _merge_value_aliases(raw_field_map, value_aliases)

    try:
        field_map = FieldMap(merged_field_map)
    except FieldMapError as exc:
        raise SourceConfigError(f"field_map 不合法：{exc}") from exc
    if not field_map:
        raise SourceConfigError("field_map 不能为空：至少要说明考试与学生的字段名")

    return GenericSourceConfig(
        endpoint=endpoint,
        headers=headers,
        auth_type=auth_type,
        token_profile=token_profile,
        tools=tools,
        paths=paths,
        term=term,
        subject_filter=subject_filter,
        full_score=full_score,
        field_map=field_map,
        raw_field_map={str(key): value for key, value in raw_field_map.items()},
        value_aliases=value_aliases,
        tier_aliases=tier_aliases,
        extra={"source_key": source_key} if source_key else {},
    )


def _default_token_profile(source_key: str) -> str:
    """令牌档案名由数据源标识派生，保证「一个数据源一份令牌」。"""
    if source_key in BUILTIN_TOKEN_PROFILES:
        return BUILTIN_TOKEN_PROFILES[source_key]
    return f"school-{source_key}"


def default_token_profile(source_key: str) -> str:
    return _default_token_profile(source_key)


def runtime_for(config: GenericSourceConfig) -> dict[str, Any]:
    """把配置翻译成 PluginManager 的 runtime 覆盖，复用既有 MCP 宿主。"""
    runtime: dict[str, Any] = {
        "transport": "http",
        "url": config.endpoint,
        "headers": dict(config.headers),
    }
    if config.auth_type == "bearer":
        runtime["bearer_token_profile"] = config.token_profile
        runtime["require_bearer_token"] = True
    return runtime


# ---- 字段映射的「最小必需集合」 ----

REQUIRED_EXAM_FIELDS = ("exam.external_id", "exam.name")
REQUIRED_STUDENT_FIELDS = ("student.external_id", "student.name")
REQUIRED_CLASS_FIELDS = ("class.external_id", "class.name")


def missing_required_fields(config: GenericSourceConfig, *, with_exams: bool) -> list[str]:
    """返回缺失的关键映射字段，供「保存前预检」提示教师补哪一项。"""
    required = list(REQUIRED_CLASS_FIELDS)
    if with_exams:
        required += list(REQUIRED_EXAM_FIELDS) + list(REQUIRED_STUDENT_FIELDS)
    else:
        required += ["student.external_id", "student.name"]
    return [item for item in required if not config.field_map.has(item)]


def display_config(raw: dict[str, Any] | None, *, source_key: str | None = None) -> dict[str, Any]:
    """返回可安全回显给前端的配置：遮蔽请求头里的凭证，令牌只给「是否已配置」。"""
    config = parse_config(raw, source_key=source_key)
    payload = {
        "transport": "http",
        "endpoint": config.endpoint,
        "headers": config.redacted_headers(),
        "auth": {"type": config.auth_type},
        "tools": dict(config.tools),
        "paths": dict(config.paths),
        "subject_filter": (
            {"field": config.subject_filter.field, "any_of": list(config.subject_filter.any_of)}
            if config.subject_filter else None
        ),
        "full_score": config.full_score,
        "field_map": dict(config.raw_field_map),
    }
    if config.value_aliases:
        payload["value_aliases"] = {
            name: {canonical: list(names) for canonical, names in groups.items()}
            for name, groups in config.value_aliases.items()
        }
    if config.tier_aliases:
        payload["tier_aliases"] = dict(config.tier_aliases)
    if config.token_profile:
        payload["auth"]["token_profile"] = config.token_profile
    if config.term:
        payload["term"] = {
            "external_id": config.term.external_id,
            "name": config.term.name,
            "code": config.term.code,
        }
    return payload
