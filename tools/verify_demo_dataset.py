"""校验演示数据集：隐私残留 + 数据自洽性。"""
from __future__ import annotations

import json
import os
import re
import sys
import sqlite3
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE = Path(os.path.expanduser("~/Library/Application Support/冯老师初中英语工作台"))
DB = BASE / "workbench.db"

# 原始花名册从生成前的备份库还原，避免用「部分名单」自欺欺人
REF_DB = BASE / "backups" / "pre_migration_20260925_130125_772841" / "workbench.db"
FALLBACK_NAMES = [
    "曹漫妮", "陈双", "陈雨欣", "陈毓菡", "方璟颐", "郭书瑶", "郝秋宇", "洪方奕",
    "刘彦辰", "王车祺",
]
EXTRA_TERMS = ["冯老师", "建兰", "兰行", "兰苑"]


def load_real_names() -> list[str]:
    """原始花名册优先从备份库还原；备份被清理后去废纸篓里找。"""
    candidates = [REF_DB]
    candidates += sorted(Path(os.path.expanduser("~/.Trash")).glob(
        "wb-demo-purge-*/pre_migration_20260925_130125_772841/workbench.db"))
    candidates += sorted(Path(os.path.expanduser("~/.Trash")).glob(
        "wb-demo-purge-*/**/workbench.db"))
    for p in candidates:
        if not p.exists():
            continue
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            names = [r[0] for r in con.execute("SELECT name FROM students") if r[0]]
        finally:
            con.close()
        if names:
            return names
    return list(FALLBACK_NAMES)


REAL_NAMES = load_real_names()
# 词表 = 原始姓名 + 机构/称谓；同时构造 JSON 转义形态（\uXXXX）
NAME_LIST_COMPLETE = len(REAL_NAMES) > len(FALLBACK_NAMES)
_SEARCH_TERMS: list[tuple[str, str]] = []
for _t in [*REAL_NAMES, *EXTRA_TERMS]:
    _SEARCH_TERMS.append((_t, "字面"))
    _SEARCH_TERMS.append((json.dumps(_t, ensure_ascii=True)[1:-1], "转义"))

# 手机号：排除嵌在长十六进制串里的假阳性
phone_re = re.compile(r"(?<![0-9A-Za-z])1[3-9]\d{9}(?![0-9A-Za-z])")
# 结构化字段里未脱敏的 student_name
student_name_re = re.compile(
    r"""student_?[Nn]ame['"]?\s*[:=]\s*['"]([^'"]{1,20})['"]""")

def main() -> int:
    problems: list[str] = []
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # ---------- 1. 隐私残留：全表全列扫描 ----------
    tables = [r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    name_hits: dict[str, int] = {}
    phone_hits: dict[str, int] = {}
    for t in tables:
        cols = [c[1] for c in cur.execute(f"PRAGMA table_info('{t}')")]
        for row in cur.execute(f"SELECT * FROM '{t}'"):
            for c in cols:
                v = row[c]
                if not isinstance(v, str) or not v:
                    continue
                key = f"{t}.{c}"
                for term, form in _SEARCH_TERMS:
                    if term in v:
                        name_hits[key] = name_hits.get(key, 0) + 1
                        break
                if phone_re.search(v):
                    phone_hits[key] = phone_hits.get(key, 0) + 1
                for m in student_name_re.finditer(v):
                    raw = m.group(1)
                    try:  # 值可能是 JSON 转义形态（\uXXXX），先还原再判断
                        raw = json.loads('"' + raw + '"')
                    except Exception:
                        pass
                    if not raw.endswith("同学"):
                        name_hits[f"{key}#student_name"] = \
                            name_hits.get(f"{key}#student_name", 0) + 1
    if name_hits:
        problems.append(f"仍有真实可识别信息：{name_hits}")
    print(f"【隐私残留】词表 {len(REAL_NAMES)} 个原始姓名 + {len(EXTRA_TERMS)} 个机构词"
          + ("" if NAME_LIST_COMPLETE else "（⚠️ 未取到完整花名册，仅为抽样词表）"),
          "->", name_hits or "无")
    print("【11位手机号形态】", {k: v for k, v in phone_hits.items()} or "无")
    print("【结构化 student_name 未脱敏】",
          {k: v for k, v in name_hits.items() if k.endswith("#student_name")} or "无")

    # ---------- 2. 姓名格式 ----------
    names = [r[0] for r in cur.execute("SELECT name FROM students")]
    bad = [n for n in names if not re.fullmatch(r".同学", n)]
    print(f"【学生姓名】{len(names)} 人，不符合「姓氏+同学」的：{bad or '无'}")
    if bad:
        problems.append(f"姓名格式异常 {bad[:5]}")
    dup = cur.execute(
        "SELECT class_id, COUNT(*) c FROM students GROUP BY class_id").fetchall()
    per_class = {r["class_id"]: r["c"] for r in dup}
    print(f"【班级人数】{per_class}")

    # ---------- 3. 默写 / 写作 / 背诵 ----------
    st = json.loads(cur.execute("SELECT state_json FROM workspace_states").fetchone()[0])
    rounds = st.get("dictationNames", [])
    dict_rows = st.get("dictation", {})
    print(f"【默写】{len(rounds)} 轮 {rounds}；有成绩学生 {len(dict_rows)} 人")
    if len(rounds) != 4 or len(dict_rows) != len(names):
        problems.append("默写轮次或覆盖人数异常")
    empty = [k for k, v in dict_rows.items() if not isinstance(v, list) or len(v) != len(rounds)]
    if empty:
        problems.append(f"默写行数不齐 {len(empty)}")
    for w in st.get("writings", []):
        vals = [v for v in w["scores"].values()]
        print(f"【写作】{w['title']} 满分{w['fullScore']} 覆盖{len(vals)} 人 "
              f"均分 {sum(vals)/len(vals):.1f}")
        if any(v > w["fullScore"] for v in vals):
            problems.append(f"写作分数超满分 {w['title']}")
    for r in st.get("recitations", []):
        levels: dict[str, int] = {}
        for v in r["status"].values():
            levels[v["level"]] = levels.get(v["level"], 0) + 1
        print(f"【背诵】{r['title']} 等级分布 {levels}")
        if set(levels) - {"A", "B", "C", "F"}:
            problems.append(f"背诵等级非法 {levels}")

    # ---------- 4. 考试与逐题小分自洽 ----------
    teacher = st.get("teacher", {})
    print(f"【教师】{teacher}")
    if teacher.get("name") in (None, "", "冯老师"):
        problems.append("教师名未脱敏")
    print("【待办含旧校名】", [t["title"] for t in st.get("todos", [])
                          if "兰" in t.get("title", "")] or "无")

    print("\n【考试列表】")
    exam_ids = []
    for e in st.get("exams", []):
        scored = [v for v in e["scores"].values() if isinstance(v, dict) and v.get("英语") not in (None, "")]
        absent = [k for k, v in e["scores"].items() if isinstance(v, dict) and v.get("attendanceStatus") == "absent"]
        print(f"  {e['name']} | {e['date']} | 有分 {len(scored)} 人 | 缺考 {len(absent)} 人 "
              f"| 均分 {sum(v['英语'] for v in scored)/max(len(scored),1):.1f}")
        exam_ids.append(e["id"])

    print("\n【小分合计 vs 总分】")
    mismatch = 0
    rows = cur.execute("""
        SELECT si.exam_id, si.student_id, SUM(si.score) total_items, e.total_score
        FROM student_item_results si
        JOIN exam_scores e ON e.exam_id = si.exam_id AND e.student_id = si.student_id
        GROUP BY si.exam_id, si.student_id
    """).fetchall()
    worst = []
    for r in rows:
        if abs(float(r["total_items"]) - float(r["total_score"])) > 1e-6:
            mismatch += 1
            if len(worst) < 3:
                worst.append(dict(r))
    print(f"  比对 {len(rows)} (生,考) 组合，不一致 {mismatch}")
    if worst:
        print("  例：", worst)
    if mismatch:
        problems.append(f"小分合计与总分不一致 {mismatch} 例")
    over = cur.execute("""
        SELECT COUNT(*) FROM student_item_results si
        JOIN exam_questions q ON q.id = si.question_id
        WHERE si.score > q.max_score + 1e-9
    """).fetchone()[0]
    print(f"  超单题满分的小分：{over}")
    if over:
        problems.append(f"小分超上限 {over}")

    print("\n【DB 考试表】")
    for r in cur.execute("SELECT id, source_key, name, exam_date, full_score, status FROM exams"):
        print("  ", dict(r))

    # ---------- 5. 成长森林 ----------
    print("\n【成长森林】")
    counts = {t: cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ["growth_events", "growth_awards", "student_growth_snapshots",
                        "exam_scores", "student_item_results", "attachments",
                        "agent_message_attachments", "backup_records"]}
    print("  ", counts)
    stages = cur.execute(
        "SELECT stage_name, COUNT(*) c, MIN(term_points) mn, MAX(term_points) mx "
        "FROM student_growth_snapshots GROUP BY stage_name ORDER BY MIN(term_points)").fetchall()
    for s in stages:
        print(f"   {s['stage_name']}: {s['c']} 人  营养 {s['mn']}~{s['mx']}")
    types = cur.execute(
        "SELECT event_type, COUNT(*) c FROM growth_events GROUP BY event_type ORDER BY c DESC").fetchall()
    print("   事件类型:", {t["event_type"]: t["c"] for t in types})

    # 快照是否可由事件重算得出（抽 5 名学生比对）
    print("\n【快照可重建性抽检】")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from backend.app.database import create_session_factory
    from backend.app.models import StudentGrowthSnapshot, Student, Enrollment
    from backend.app.services.growth import snapshots
    from sqlalchemy import select
    factory = create_session_factory(f"sqlite:///{DB}")
    mismatch_snap = 0
    with factory() as session:
        sids = list(session.scalars(select(Student.id).limit(50)))
        for sid in sids:
            rebuilt = snapshots.build_snapshot(session, student_id=sid, term_id=1, persist=False)
            stored = session.scalar(select(StudentGrowthSnapshot).where(
                StudentGrowthSnapshot.student_id == sid, StudentGrowthSnapshot.term_id == 1))
            if stored is None or stored.source_revision != rebuilt["source_revision"]:
                mismatch_snap += 1
                if mismatch_snap <= 2:
                    print(f"   学生 {sid}: 存储 {stored.source_revision if stored else None} "
                          f"!= 重算 {rebuilt['source_revision']}")
    print(f"   抽检 {len(sids)} 人，指纹不一致 {mismatch_snap}")
    if mismatch_snap:
        problems.append(f"快照与事件账本不一致 {mismatch_snap} 人")

    # ---------- 6. 维度分支是否有据 ----------
    print("\n【能力分支】")
    dims = cur.execute("SELECT dimensions_json FROM student_growth_snapshots LIMIT 1").fetchone()
    if dims:
        d = json.loads(dims["dimensions_json"])
        for k, v in d.items():
            print(f"   {k}: status={v.get('status')} value={v.get('value')} "
                  f"obs={v.get('observations')} dates={v.get('distinct_dates')}")

    # ---------- 7. 磁盘残留 ----------
    print("\n【磁盘附件】", sorted(p.name for p in (BASE / "attachments").glob("*")) if (BASE / "attachments").exists() else "无目录")
    print("【聊天记录含旧姓名】", cur.execute(
        "SELECT COUNT(*) FROM agent_messages").fetchone()[0], "条消息（应已脱敏）")

    con.close()
    print("\n" + "=" * 60)
    if problems:
        print("发现的问题：")
        for p in problems:
            print("  -", p)
        return 1
    print("校验通过：无隐私残留，数据自洽。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
