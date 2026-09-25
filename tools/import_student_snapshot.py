"""Apply a students-table JSON snapshot to an existing WorkBench term safely.

Only the entrance English score is updated. The roster must match exactly;
exam results, growth events and teacher notes are never read from the snapshot.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.auth import TOKEN
from backend.app.config import Settings
from backend.app.factory import create_app
from backend.app.services.backups import create_backup, verify_backup


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--no-backup", action="store_true", help="按用户要求跳过变更前备份")
    args = parser.parse_args()
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    rows = snapshot.get("students")
    if not isinstance(rows, list) or len(rows) != snapshot.get("total"):
        raise ValueError("学生记录数量与文件总数不一致")
    scores: dict[str, float | None] = {}
    for row in rows:
        number = str(row.get("student_no", "")).strip()
        if not number or number in scores or not str(row.get("name", "")).strip():
            raise ValueError("文件有重复或不完整的学号、姓名")
        raw = row.get("entrance_english")
        score = None if raw is None else float(raw)
        if score is not None and not 0 <= score <= 100:
            raise ValueError(f"学号 {number} 的入学英语分数超出 0–100")
        scores[number] = score

    settings = Settings(data_dir=args.data_dir.resolve())
    backup_dir = None
    if args.apply and not args.no_backup:
        # create_app may run a migration or recovery, so capture the untouched
        # database before opening the application as well as before the PUT.
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        backup_dir = settings.backups_dir / f"before_student_snapshot_{stamp}"
        create_backup(settings.data_dir / "workbench.db", backup_dir,
                      kind="before_student_snapshot",
                      attachments_dir=settings.attachments_dir,
                      exports_dir=settings.exports_dir)
        verify_backup(backup_dir)
    client = TestClient(create_app(settings))
    headers = {"Authorization": f"Bearer {TOKEN}"}
    term = client.get("/api/v1/terms/current", headers=headers)
    term.raise_for_status()
    term_id = term.json()["id"]
    endpoint = f"/api/v1/terms/{term_id}/workspace-state"
    response = client.get(endpoint, headers=headers)
    response.raise_for_status()
    payload = response.json()
    state = payload["state"]
    existing = {str(row["id"]): row for row in state["students"]}
    if set(existing) != set(scores):
        raise ValueError(f"当前学期名单与文件不一致：文件 {len(scores)} 人、工作台 {len(existing)} 人")
    file_names = {str(row["student_no"]): str(row["name"]).strip() for row in rows}
    if any(existing[number]["name"] != file_names[number] for number in scores):
        raise ValueError("文件与当前学期存在同学号不同姓名，已停止")
    changes = [(number, existing[number].get("english"), score)
               for number, score in scores.items()
               if score is not None and existing[number].get("english") != score]
    print(json.dumps({"term_id": term_id, "students": len(scores),
                      "entrance_score_changes": len(changes), "sample": changes[:5]}, ensure_ascii=False))
    if not args.apply or not changes:
        return

    for number, _old, score in changes:
        existing[number]["english"] = score
    result = client.put(endpoint, headers=headers,
                        json={"state": state, "expected_revision": payload["revision"]})
    result.raise_for_status()
    saved = client.get(endpoint, headers=headers)
    saved.raise_for_status()
    saved_rows = {str(row["id"]): row for row in saved.json()["state"]["students"]}
    if any(saved_rows[number].get("english") != score for number, _old, score in changes):
        raise RuntimeError(f"写入后校验失败；备份位置：{backup_dir}")
    forest = client.get("/api/v1/growth/forest", headers=headers, params={"term_id": term_id})
    forest.raise_for_status()
    print(json.dumps({"backup": str(backup_dir), "saved": len(changes),
                      "forest": forest.json()["summary"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
