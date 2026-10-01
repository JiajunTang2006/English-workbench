"""对演示库跑一遍真实 HTTP 读路径（用副本，不碰线上数据目录）。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ["WORKBENCH_DATA_DIR"] = "/tmp/wb_smoke"
os.environ["WORKBENCH_TOKEN"] = "smoke-token"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))          # 让 alembic env.py 能 import backend.*
sys.path.insert(0, str(ROOT / "backend"))  # 让脚本能 import app.*

from fastapi.testclient import TestClient  # noqa: E402

from app.factory import create_app  # noqa: E402

app = create_app()
H = {"Authorization": "Bearer smoke-token"}


def show(label, resp, key=None, limit=3):
    ok = resp.status_code < 400
    print(f"[{'OK ' if ok else 'ERR'}] {label} -> {resp.status_code}")
    if not ok:
        print("      ", resp.text[:300])
        return None
    data = resp.json()
    if key is not None:
        val = data.get(key) if isinstance(data, dict) else None
        if isinstance(val, list):
            print(f"       {key}: {len(val)} 项", json.dumps(val[:limit], ensure_ascii=False)[:220])
        else:
            print(f"       {key}:", json.dumps(val, ensure_ascii=False)[:220])
    return data


with TestClient(app) as c:
    terms = show("GET /api/v1/terms", c.get("/api/v1/terms", headers=H))
    tid = terms[0]["id"] if terms else 1
    print("       当前 term_id =", tid)

    ws = show(f"GET /api/v1/terms/{tid}/workspace-state",
              c.get(f"/api/v1/terms/{tid}/workspace-state", headers=H))
    if ws and ws.get("state"):
        st = ws["state"]
        print("       revision:", ws.get("revision"),
              "| students:", len(st.get("students", [])),
              "| exams:", len(st.get("exams", [])),
              "| dictationNames:", st.get("dictationNames"),
              "| writings:", len(st.get("writings", [])),
              "| recitations:", len(st.get("recitations", [])))
        print("       teacher:", st.get("teacher"))

    show("GET /api/v1/growth/forest", c.get(f"/api/v1/growth/forest?term_id={tid}", headers=H))

    # 用规范化的学生表拿数字 id（workspace state 里的 id 是学号）
    stu = c.get("/api/v1/students", headers=H).json()
    print(f"       /api/v1/students -> {len(stu)} 人，示例:",
          json.dumps(stu[:2], ensure_ascii=False)[:200])
    if stu:
        sid = stu[0]["id"]
        show(f"GET /api/v1/growth/students/{sid}",
             c.get(f"/api/v1/growth/students/{sid}?term_id={tid}", headers=H))

    show("GET /api/v1/exams", c.get("/api/v1/exams", headers=H))
    show("GET /health", c.get("/health"))
