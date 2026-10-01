"""配置式字段映射。

换学校时真正变化的通常只有三件事：数据路径、字段名、取值口径。这里把「字段名」
抽象成一份可配置的映射表，让新学校不需要改代码——这也是本补丁的核心诉求。

映射规格（``field_map`` 的每一项）支持三种写法：

1. 单个路径字符串：``"studentId"``
2. 候选路径列表（按顺序取第一个非空）：``["studentId", "student_no", "id"]``
3. 带修饰的对象：

   .. code-block:: json

      {
        "path": ["tier", "level"],
        "enum": {"ELITE": "A", "甲": "A"},
        "enum_missing": "keep",
        "default": null
      }

修饰键：

- ``path``：字符串或候选列表（必填）。
- ``enum``：取值归一化表，命中则替换（例如把「甲/乙/丙」统一成 ``A/B/C``）。
  查表键做 NFKC 归一（全角转半角）、去首尾空白、大小写不敏感，所以
  ``ELITE`` / ``elite`` / ``ＥＬＩＴＥ`` 视为同一个上游值。
- ``enum_missing``：取值不在 ``enum`` 里时的策略，默认 ``"keep"``：

  * ``"keep"``：保留上游原值（不假装已归一，便于事后发现漏配的别名）；
  * ``"drop"``：视为缺失，继续尝试下一个候选路径；
  * ``"bucket"``：归入 ``enum_bucket`` 指定的桶（默认「未分类」）。

- ``enum_bucket``：``enum_missing="bucket"`` 时使用的桶名。
- ``list``：为真时先把取值规整成字符串列表（兼容真列表、字典、``a,b`` / ``a；b``
  分隔的字符串），再逐元素查表。标签类字段（知识点、能力点、易错标签、教学模块）
  必须打开它，否则「逗号分隔的字符串」会整串去查表，永远命不中。
- ``default``：全部候选都为空时使用的值（默认 ``None``，即「缺失」，不用 0 顶替）。
- ``required``：为真且取不到值时抛错，用于「没有它就不能安全入库」的关键字段。
- ``type``：``"int" | "float" | "str" | "bool"``，做显式类型转换（``list`` 为真时忽略）。

**列表型字段**（知识点、能力点、易错标签、教学模块）配置了 ``enum`` 时按元素逐个查表，
而不是把整个列表 ``str()`` 成一个字符串去匹配——后者永远不可能命中。归一后按顺序去重。

路径语法：``a.b.c``、``a[0].b``、``$.a.b``（``$`` 表示根，可省略）。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

# 支持 a.b、a[0].b、a["k"].b 这类路径片段。
_PATH_TOKEN_RE = re.compile(r"""
    (?P<key>[^.\[\]]+)      # 普通键
  | \[(?P<index>\d+)\]      # 数字下标
  | \[['"](?P<quoted>[^'"]+)['"]\]  # 带引号的键
""", re.VERBOSE)


class FieldMapError(ValueError):
    """映射规格本身不合法（配置错误，不是数据缺失）。"""


def _tokens(path: str) -> list[str | int]:
    cleaned = path.strip()
    if cleaned.startswith("$"):
        cleaned = cleaned[1:].lstrip(".")
    tokens: list[str | int] = []
    for match in _PATH_TOKEN_RE.finditer(cleaned):
        if match.group("index") is not None:
            tokens.append(int(match.group("index")))
        elif match.group("quoted") is not None:
            tokens.append(match.group("quoted"))
        else:
            tokens.append(match.group("key"))
    if not tokens and cleaned:
        raise FieldMapError(f"无法解析字段路径：{path}")
    return tokens


def get_path(row: Any, path: str) -> Any:
    """按路径取值；中途缺失时返回 ``None``（不抛错，交给候选列表继续尝试）。"""
    current = row
    for token in _tokens(path):
        if isinstance(token, int):
            if not isinstance(current, (list, tuple)) or token >= len(current):
                return None
            current = current[token]
        else:
            if not isinstance(current, dict) or token not in current:
                return None
            current = current[token]
    return current


def _is_blank(value: Any) -> bool:
    """空值判定：``None``、空串与空列表都视为「没有值」，与「值为 0」区分开。"""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict)):
        return len(value) == 0
    return False


def _coerce(value: Any, kind: str) -> Any:
    if kind == "str":
        return str(value).strip() if not isinstance(value, str) else value.strip()
    if kind == "int":
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None
    if kind == "float":
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    if kind == "bool":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"true", "1", "yes", "y", "是"}:
            return True
        if text in {"false", "0", "no", "n", "否"}:
            return False
        return None
    return value


ENUM_MISSING_POLICIES = ("keep", "drop", "bucket")
DEFAULT_ENUM_BUCKET = "未分类"

# 可以参与值域归一的基本类型；dict / list 元素原样透传（不猜语义）。
_SCALAR_TYPES = (str, int, float, bool)


def normalize_key(value: Any) -> str:
    """查表键：NFKC 归一（全角→半角）、去首尾空白、大小写不敏感。

    ``ELITE`` / ``elite`` / ``ＥＬＩＴＥ`` 因此落在同一个键上；中文不受影响。
    """
    text = str(value).strip()
    if not text:
        return ""
    return unicodedata.normalize("NFKC", text).casefold()


def as_str_list(value: Any) -> list[str]:
    """把上游的标签值统一成字符串列表。

    兼容三种常见形态：真列表、字典（取 value）、以及用 ``,`` / ``；`` 分隔的字符串。
    空值返回空列表——**不是** ``[""]``，避免下游把空串当成一个标签。
    """
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, dict):
        return [str(item).strip() for item in value.values() if str(item).strip()]
    return [item.strip() for item in str(value).replace("；", ",").split(",") if item.strip()]


def _enum_lookup(enum_map: dict[str, Any]) -> dict[str, Any]:
    """把配置里的 enum 表按归一键重建一次，避免逐行重复归一。"""
    return {normalize_key(key): mapped for key, mapped in enum_map.items()}


def _apply_enum_scalar(
    value: Any, lookup: dict[str, Any], missing: str, bucket: str
) -> tuple[Any, bool]:
    """标量查表；返回 ``(值, 是否判定为缺失)``。"""
    mapped = lookup.get(normalize_key(value))
    if mapped is not None:
        return mapped, False
    if missing == "drop":
        return None, True
    if missing == "bucket":
        return bucket, False
    return value, False


def _apply_enum_list(
    value: list[Any] | tuple[Any, ...], lookup: dict[str, Any], missing: str, bucket: str
) -> tuple[list[Any], bool]:
    """列表逐元素查表并按顺序去重；全部被丢弃时视为缺失。"""
    result: list[Any] = []
    for element in value:
        if _is_blank(element):
            continue
        if isinstance(element, _SCALAR_TYPES):
            mapped, dropped = _apply_enum_scalar(element, lookup, missing, bucket)
            if dropped:
                continue
            if _is_blank(mapped):
                continue
            item: Any = mapped
        else:
            # 嵌套结构不做猜测，原样保留——归一表只描述标量取值。
            item = element
        if item not in result:
            result.append(item)
    if not result and value:
        return [], True
    return result, False


def pick(row: Any, spec: Any, *, label: str | None = None) -> Any:
    """按映射规格从一行数据里取值。

    ``spec`` 为 ``None`` 时直接返回 ``None``；``required`` 为真且取不到值时报错，
    避免关键字段静默变成空值后写出难以排查的脏数据。
    """
    if spec is None:
        return None
    enum_map: dict[str, Any] | None = None
    enum_missing = "keep"
    enum_bucket = DEFAULT_ENUM_BUCKET
    default: Any = None
    required = False
    kind: str | None = None
    list_mode = False
    paths: list[str]
    if isinstance(spec, str):
        paths = [spec]
    elif isinstance(spec, (list, tuple)):
        paths = [str(item) for item in spec]
    elif isinstance(spec, dict):
        raw_paths = spec.get("path")
        if raw_paths is None:
            raise FieldMapError(f"{label or '字段'}的映射缺少 path")
        paths = [str(raw_paths)] if isinstance(raw_paths, str) else [str(item) for item in raw_paths]
        raw_enum = spec.get("enum")
        if raw_enum is not None:
            if not isinstance(raw_enum, dict):
                raise FieldMapError(f"{label or '字段'}的 enum 必须是对象")
            enum_map = {str(key): value for key, value in raw_enum.items()}
        enum_missing = str(spec.get("enum_missing") or "keep").strip().lower()
        if enum_missing not in ENUM_MISSING_POLICIES:
            raise FieldMapError(
                f"{label or '字段'}的 enum_missing 仅支持 {'/'.join(ENUM_MISSING_POLICIES)}"
            )
        raw_bucket = spec.get("enum_bucket")
        if raw_bucket is not None:
            enum_bucket = str(raw_bucket)
        list_mode = bool(spec.get("list", False))
        default = spec.get("default")
        required = bool(spec.get("required", False))
        kind = spec.get("type")
        if kind is not None and kind not in {"str", "int", "float", "bool"}:
            raise FieldMapError(f"{label or '字段'}的 type 仅支持 str/int/float/bool")
    else:
        raise FieldMapError(f"{label or '字段'}的映射必须是字符串、列表或对象")

    lookup = _enum_lookup(enum_map) if enum_map is not None else {}
    for path in paths:
        value = get_path(row, path)
        if list_mode and value is not None:
            # 先规整形态：上游可能给真列表、字典，或用逗号/分号分隔的字符串。
            value = as_str_list(value)
        if _is_blank(value):
            continue
        if enum_map is not None:
            if isinstance(value, (list, tuple)):
                value, dropped = _apply_enum_list(value, lookup, enum_missing, enum_bucket)
            else:
                value, dropped = _apply_enum_scalar(value, lookup, enum_missing, enum_bucket)
            # 这一路候选的取值口径对不上，继续试下一个候选路径。
            if dropped:
                continue
        if kind and not list_mode and not isinstance(value, (list, tuple)):
            coerced = _coerce(value, kind)
            # 转换失败说明这一路候选的取值口径不对，继续试下一个候选路径。
            if coerced is None:
                continue
            return coerced
        return value
    if required:
        raise FieldMapError(f"缺少必填字段：{label or paths[0]}（已尝试 {paths}）")
    return default


class FieldMap:
    """一份 ``field_map`` 配置的取值入口，按逻辑字段名索引。"""

    def __init__(self, spec: dict[str, Any] | None = None) -> None:
        if spec is not None and not isinstance(spec, dict):
            raise FieldMapError("field_map 必须是对象")
        self.spec: dict[str, Any] = dict(spec or {})

    def __bool__(self) -> bool:
        return bool(self.spec)

    def has(self, field: str) -> bool:
        return field in self.spec

    def value(self, row: Any, field: str, *, default: Any = None) -> Any:
        """取某个逻辑字段；未配置时返回 ``default``（不是 0）。"""
        if field not in self.spec:
            return default
        return pick(row, self.spec[field], label=field)
