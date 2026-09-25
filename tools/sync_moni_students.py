#!/usr/bin/env python3
"""从 MONI 只读 MCP 名册视图幂等同步学生到 Workbench。

令牌优先读取 MONI_MCP_TOKEN；未设置时，PluginManager 会读取本地 0600
保管库中的 ``moni`` profile。脚本只写入本地 Workbench 数据库，不把令牌
或学生明细打印到标准输出。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.config import get_settings
from backend.app.database import create_session_factory, run_migrations
from backend.app.schemas.school_sync import StudentRosterPayload
from backend.app.services.plugin_manager import PluginManager
from backend.app.services.school_sync import apply_student_roster


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="同步 MONI 学生名册到 Workbench")
    parser.add_argument("--file-path", required=True, help="MONI VFS 中已授权的 JSON/JSONL 文件路径")
    parser.add_argument("--term-code", required=True, help="WorkBench 学期代码")
    parser.add_argument("--term-name", required=True, help="WorkBench 学期名称")
    parser.add_argument("--class-name", default="", help="文件没有班级字段时使用的班级名称")
    parser.add_argument("--student-id-field", default="student_no", help="学生学号字段")
    parser.add_argument("--student-name-field", default="name", help="学生姓名字段")
    parser.add_argument("--class-field", default="class_name", help="班级名称字段")
    parser.add_argument("--external-id-field", default="external_id", help="外部稳定 ID 字段")
    parser.add_argument("--gender-field", default="gender", help="性别字段")
    parser.add_argument("--snapshot-id", default="", help="可选快照 ID；默认按文件内容生成")
    parser.add_argument("--dry-run", action="store_true", help="只读取和校验，不写入本地数据库")
    return parser.parse_args()


def _tool_text(result: dict[str, Any]) -> str:
    content = result.get("content") or []
    chunks = [item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"]
    if not chunks:
        raise RuntimeError("MONI 没有返回文本数据")
    return "\n".join(chunks)


def _decode_rows(text: str) -> tuple[list[dict[str, Any]], bytes]:
    raw = text.encode("utf-8")
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        value = rows
    if isinstance(value, dict):
        for key in ("rows", "data", "students", "items"):
            if isinstance(value.get(key), list):
                value = value[key]
                break
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise RuntimeError("MONI 文件不是对象数组或 JSONL")
    return value, raw


def _field(row: dict[str, Any], name: str, *, required: bool = True) -> str:
    value = row.get(name)
    if value is None:
        value = row.get(name.replace("_", ""))
    text = str(value or "").strip()
    if required and not text:
        raise RuntimeError(f"学生名册缺少字段：{name}")
    return text


def main() -> int:
    args = _parse_args()
    settings = get_settings()
    manager = PluginManager(settings.data_dir)
    plugin = manager.get_plugin("moni")
    if plugin is None:
        raise RuntimeError("内置 moni 插件不存在")
    text = _tool_text(manager.call_tool("moni", "moni_vfs_read", {"file_path": args.file_path}))
    rows, raw = _decode_rows(text)
    classes: dict[str, dict[str, str]] = {}
    students: list[dict[str, str]] = []
    for index, row in enumerate(rows, 1):
        class_name = _field(row, args.class_field, required=False) or args.class_name.strip()
        if not class_name:
            raise RuntimeError(f"第 {index} 行没有班级信息，请提供 --class-name 或 --class-field")
        class_id = _field(row, "class_external_id", required=False) or f"class:{class_name}"
        classes.setdefault(class_id, {"external_id": class_id, "name": class_name})
        external_id = _field(row, args.external_id_field, required=False) or _field(row, args.student_id_field)
        students.append({
            "external_id": external_id,
            "student_no": _field(row, args.student_id_field),
            "name": _field(row, args.student_name_field),
            "class_external_id": class_id,
            "gender": _field(row, args.gender_field, required=False) or None,
            "status": "active",
        })

    snapshot_id = args.snapshot_id.strip() or "moni-" + hashlib.sha256(raw).hexdigest()[:24]
    payload = StudentRosterPayload.model_validate({
        "source_key": "moni",
        "source_name": "MONI 学生数据",
        "snapshot_id": snapshot_id,
        "term": {"external_id": args.term_code, "code": args.term_code, "name": args.term_name},
        "classes": list(classes.values()),
        "students": students,
    })
    print(json.dumps({"snapshot_id": snapshot_id, "classes": len(classes), "students": len(students), "dry_run": args.dry_run}, ensure_ascii=False))
    if args.dry_run:
        return 0
    run_migrations(settings.database_url)
    session_factory = create_session_factory(settings.database_url)
    with session_factory() as session:
        summary = apply_student_roster(session, payload)
    print(json.dumps({"status": "completed", "counts": summary, "completed_at": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as error:
        print(f"同步失败：{error}", file=sys.stderr)
        raise SystemExit(2)
