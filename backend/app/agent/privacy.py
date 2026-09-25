"""
隐私映射服务

每个分析运行生成一次性匿名编号（如 student_01）。
不使用真实学号哈希作为外部编号。

职责：
- 生成随机匿名编号
- 匿名模式下模型输入移除姓名、电话、座位、本地 ID
- 教师工作模式仅允许发送学生姓名，其他个人信息仍然移除
- 返回后只在本地作用域恢复姓名
- 日志输出再次脱敏
- 任务恢复时映射仍可用
- 任务归档后按策略清理

匿名模式下云端模型不得接收：
- 学生姓名
- 真实学号
- 家长电话
- 座位
- 本地数据库 ID
- 无关的教师或学校信息
- 临时编号与真实身份映射表

教师工作模式（``allow_student_names=True``）是有意的例外：为生成可直接交给老师执行的
个体化反馈，可以发送学生姓名；学号、电话、座位、本地 ID 和身份映射仍不会发送。
"""

from __future__ import annotations

import random
import re
import string
from dataclasses import dataclass, field
from typing import Any


class PrivacyViolationError(Exception):
    """隐私违规错误：尝试将未脱敏的真实身份发送给模型。"""
    pass


@dataclass
class PrivacyMapper:
    """隐私映射器。

    为每次分析运行生成一次性匿名编号映射。
    映射表只存在本地受控数据中。

    B2-03：除 student_id 外，还维护运行内身份词典：
    - 真实姓名 -> 匿名编号（文本替换，不删除）；
    - 学校名称 -> 统一脱敏标记；
    - 教师姓名等受保护文本 -> 统一脱敏标记。
    """

    # {local_student_id: "student_01"}
    _to_anonymous: dict[int, str] = field(default_factory=dict)
    # {"student_01": local_student_id}
    _to_real: dict[str, int] = field(default_factory=dict)
    # 真实姓名 -> 匿名编号（文本替换用；同一姓名可能对应学生）
    _name_to_anonymous: dict[str, str] = field(default_factory=dict)
    # 匿名编号 -> 真实姓名（仅用于本地教师界面恢复）
    _anonymous_to_name: dict[str, str] = field(default_factory=dict)
    # 学校/组织名称集合（统一替换为标记）
    _school_names: set[str] = field(default_factory=set)
    # 其他受保护文本（如教师姓名）
    _protected_terms: set[str] = field(default_factory=set)
    _counter: int = 0
    # 教师工作模式允许模型在教学分析中看到学生姓名，以便生成可直接执行的建议。
    # 默认仍为 False，保持匿名模式和历史调用方的安全行为；正式 Agent 运行由运行入口显式开启。
    allow_student_names: bool = False

    def register_student(self, student_id: int) -> str:
        """注册学生并返回匿名编号。

        如果已注册则返回已有编号。
        """
        if student_id in self._to_anonymous:
            return self._to_anonymous[student_id]
        self._counter += 1
        # 生成 student_01, student_02, ...
        suffix = f"{self._counter:02d}"
        anonymous_id = f"student_{suffix}"
        self._to_anonymous[student_id] = anonymous_id
        self._to_real[anonymous_id] = student_id
        return anonymous_id

    def to_anonymous(self, student_id: int) -> str | None:
        """本地 ID -> 匿名编号。"""
        return self._to_anonymous.get(student_id)

    def to_real(self, anonymous_id: str) -> int | None:
        """匿名编号 -> 本地 ID（仅在本地作用域使用）。"""
        return self._to_real.get(anonymous_id)

    def register_name(self, name: str, *, student_id: int | None = None) -> str | None:
        """注册真实姓名；返回将用于文本替换的匿名编号。

        - 提供 student_id 时沿用该学生的匿名编号（避免姓名与编号不一致）；
        - 未提供时生成独立匿名编号；
        - 过短/空姓名忽略。
        """
        name = (name or "").strip()
        if len(name) < 2:
            return None
        anonymous = self.to_anonymous(student_id) if student_id is not None else None
        if anonymous is None:
            anon = self._name_to_anonymous.get(name)
            if anon is not None:
                return anon
            self._counter += 1
            anonymous = f"student_{self._counter:02d}"
        self._name_to_anonymous[name] = anonymous
        # 只在本地保存反向映射。若同名学生共享一个匿名编号，保留首次
        # 注册的姓名，避免后续运行中的文本恢复发生漂移。
        self._anonymous_to_name.setdefault(anonymous, name)
        return anonymous

    def register_names(self, names: list[str]) -> int:
        """批量注册姓名，返回新增数量。"""
        added = 0
        for name in names:
            if self.register_name(name) is not None:
                added += 1
        return added

    def register_school_names(self, names: list[str]) -> None:
        """注册学校/组织名称集合。"""
        for name in names:
            name = (name or "").strip()
            if len(name) >= 2:
                self._school_names.add(name)

    def register_protected_terms(self, terms: list[str]) -> None:
        """注册其他受保护文本（如教师姓名）。"""
        for term in terms:
            term = (term or "").strip()
            if len(term) >= 2:
                self._protected_terms.add(term)

    @property
    def known_real_names(self) -> set[str]:
        """当前运行身份词典中的真实姓名（供输出验证反向检查）。"""
        return set(self._name_to_anonymous.keys())

    @property
    def known_school_names(self) -> set[str]:
        """已注册学校/组织名称。"""
        return set(self._school_names)

    @property
    def known_protected_terms(self) -> set[str]:
        """已注册的其他受保护文本（如教师姓名、学号）。"""
        return set(self._protected_terms)

    def anonymize(self, name: str) -> str:
        """返回姓名对应的匿名编号（未注册时返回占位标记）。"""
        return self._name_to_anonymous.get(name, "[姓名已脱敏]")

    def sanitize(self, data: dict | list | Any) -> dict | list | Any:
        """兼容教育桥接层的工具级脱敏入口。

        桥接层历史上使用独立的 ``_Anonymizer``，会产生 S1/S2 编号。
        统一改由本类处理后，工具实现仍可调用 ``anon.sanitize(...)``，
        但编号与主 Agent 的 ``student_XX`` 映射保持一致。
        """
        return self.sanitize_for_model(data)

    # 敏感字段集合（小写匹配）
    SENSITIVE_KEYS: frozenset = frozenset({
        "name", "student_name", "student_no", "parent_phone",
        "phone", "seat", "student_id", "class_id", "exam_id", "id",
        "attachment_id", "storage_name", "original_name", "url",
    })

    def sanitize_for_model(self, data: dict | list | Any) -> dict | list | Any:
        """递归脱敏数据，移除真实身份字段，替换为匿名编号。

        递归处理字典、列表和嵌套记录。
        移除：姓名、学号、电话、座位、本地 ID、附件路径等。

        安全规则：遇到未注册的 student_id 时 fail closed（抛异常），
        绝不将原始 ID 或可推断 ID 的字符串发送给模型。
        """
        if isinstance(data, list):
            return [self.sanitize_for_model(item) for item in data]
        if not isinstance(data, dict):
            return data

        sanitized: dict[str, Any] = {}
        for key, value in data.items():
            key_lower = key.lower()
            if key_lower in self.SENSITIVE_KEYS:
                if self.allow_student_names and key_lower in {"name", "student_name"}:
                    sanitized[key] = self.sanitize_for_model(value)
                    continue
                if key_lower == "student_id" and isinstance(value, int):
                    anon = self.to_anonymous(value)
                    if anon is None:
                        raise PrivacyViolationError(
                            "检测到未注册的学生 ID，拒绝发送给模型。"
                            "请在工具执行前调用 pre_register_students() 完成匿名映射注册。"
                        )
                    sanitized["anonymous_id"] = anon
                # 其他敏感字段直接跳过
                continue
            # 递归处理嵌套结构
            if isinstance(value, (dict, list)):
                sanitized[key] = self.sanitize_for_model(value)
            else:
                sanitized[key] = value
        return sanitized

    def pre_register_students(self, student_ids: list[int]) -> None:
        """批量预注册学生匿名映射。

        在工具执行前调用，确保所有可能出现的学生 ID
        都已建立匿名映射，避免 sanitize_for_model 时 fail closed。
        """
        for sid in student_ids:
            self.register_student(sid)

    def extract_student_ids(self, data: dict | list | Any) -> list[int]:
        """从数据结构中提取所有 student_id 值（递归）。

        用于工具执行后、脱敏前检查是否有未注册的学生 ID。
        """
        found: list[int] = []
        if isinstance(data, list):
            for item in data:
                found.extend(self.extract_student_ids(item))
        elif isinstance(data, dict):
            for key, value in data.items():
                if key.lower() == "student_id" and isinstance(value, int):
                    found.append(value)
                if isinstance(value, (dict, list)):
                    found.extend(self.extract_student_ids(value))
        return found

    def restore_text_for_display(self, text: str) -> str:
        """在本地教师界面恢复匿名编号，绝不用于 Provider 出站内容。"""
        if not text:
            return text
        for anonymous, name in sorted(
            self._anonymous_to_name.items(), key=lambda item: len(item[0]), reverse=True
        ):
            text = text.replace(anonymous, name)
        return text

    def restore_for_display(self, data: Any, student_resolver=None) -> Any:
        """递归恢复教师界面数据中的匿名编号和身份字段。

        ``student_resolver`` 仅用于从本地 ID 查询最新姓名；没有提供时，
        使用本次运行注册的本地反向映射。该方法不应在发送模型前调用。
        """
        if isinstance(data, list):
            return [self.restore_for_display(item, student_resolver) for item in data]
        if isinstance(data, str):
            return self.restore_text_for_display(data)
        if not isinstance(data, dict):
            return data

        restored: dict[str, Any] = {}
        anonymous_values: list[str] = []
        for key, value in data.items():
            if key.lower() in {"anonymous_id", "student_ref", "student_anon_id"}:
                if isinstance(value, str):
                    anonymous_values.append(value)
                # 保留匿名引用，便于审计和问题排查；只在本地补充姓名。
                restored[key] = value
            else:
                restored[key] = self.restore_for_display(value, student_resolver)

        for anonymous in anonymous_values:
            real_id = self.to_real(anonymous)
            if real_id is None:
                continue
            restored.setdefault("student_id", real_id)
            name = None
            if student_resolver:
                try:
                    name = student_resolver(real_id)
                except Exception:
                    name = None
            name = name or self._anonymous_to_name.get(anonymous)
            if name:
                restored.setdefault("student_name", name)
        return restored

    def clear(self) -> None:
        """清理映射表。任务归档后调用。"""
        self._to_anonymous.clear()
        self._to_real.clear()
        self._name_to_anonymous.clear()
        self._anonymous_to_name.clear()
        self._school_names.clear()
        self._protected_terms.clear()
        self._counter = 0

    # 需要从文本中脱敏的正则模式
    # 学号：仅在明确的学号/学生编号标签附近脱敏，避免把年份、分数或题号
    # 等普通 6-10 位数字误判为身份信息；结构化字段仍由 SENSITIVE_KEYS 保护。
    _STUDENT_NO_PATTERN = re.compile(
        r"(?i)(?P<prefix>学号|学生编号|student\s*(?:no|number|id))\s*[:：#]?\s*(?P<number>\d{6,10})"
    )
    _PHONE_PATTERN = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")

    def sanitize_text(self, text: str) -> str:
        """脱敏自由文本。

        B2-03：覆盖当前运行身份词典中的真实姓名（替换为匿名编号，不删除）、
        学校/组织名称与教师等受保护文本，并保留学号/电话正则脱敏。

        本地保留原文；返回给 Provider 的只能是匿名副本。
        """
        if not text:
            return text
        # 最长优先替换，避免短姓名先命中造成部分替换（如"陈双"命中"陈双喜"）
        def _replace_ordered(replacements: list[tuple[str, str]], content: str) -> str:
            for src, dst in replacements:
                content = content.replace(src, dst)
            return content

        replacements: list[tuple[str, str]] = []
        if not self.allow_student_names:
            for name in sorted(self._name_to_anonymous, key=len, reverse=True):
                replacements.append((name, self._name_to_anonymous[name]))
        for school in sorted(self._school_names, key=len, reverse=True):
            replacements.append((school, "[学校已脱敏]"))
        for term in sorted(self._protected_terms, key=len, reverse=True):
            replacements.append((term, "[姓名已脱敏]"))

        text = _replace_ordered(replacements, text)
        text = self._STUDENT_NO_PATTERN.sub(lambda match: f"{match.group('prefix')}[学号已脱敏]", text)
        text = self._PHONE_PATTERN.sub("[电话已脱敏]", text)
        return text
