"""生成一套可对外演示的脱敏实验数据集（一次性工具）。

做两件事：
1. 脱敏：把库里的真实学生姓名换成「姓氏+同学」，替换教师名，删除含真实学校名
   与真实姓名的附件（数据库记录 + 磁盘文件）。
2. 造数据：补齐默写 / 写作 / 背诵成绩，新增 2 场考试与逐题小分，并用后端
   **真实规则引擎**（services.growth）重建成长森林事件账本与快照——数值与
   App 自身计算结果完全一致，前端不会出现第二套口径。

设计约束（与项目约定一致）：
- 只改数据，不改任何业务表结构与计分公式；
- 保留学号与全部外键关系，避免破坏引用完整性；
- 缺失一律显式留空，不补 0；撤销走追加事件，不删原记录。

用法：
    .venv/bin/python tools/gen_demo_dataset.py            # 直接写入
    .venv/bin/python tools/gen_demo_dataset.py --dry-run  # 只打印计划，不落库
"""

from __future__ import annotations

import argparse
import random
import re
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import delete, select  # noqa: E402

from backend.app.config import get_settings  # noqa: E402
from backend.app.database import create_session_factory  # noqa: E402
from backend.app.models import (  # noqa: E402
    AgentMessage,
    AgentMessageAttachment,
    Attachment,
    BackupRecord,
    Class,
    Enrollment,
    Exam,
    ExamPaperVersion,
    ExamQuestion,
    ExamScore,
    GrowthAward,
    GrowthEvent,
    Student,
    StudentGrowthSnapshot,
    StudentItemResult,
    WorkspaceState,
)
from backend.app.models.entities import utcnow  # noqa: E402
from backend.app.services.growth import events as growth_events  # noqa: E402
from backend.app.services.growth import snapshots  # noqa: E402
from backend.app.services.growth.exam_events import sync_exam_growth_events  # noqa: E402
from backend.app.services.workspace_sync import sync_workspace_domains  # noqa: E402

TERM_ID = 1
SEED = 20260926
WINDOW_START = date(2026, 7, 13)   # 暑期衔接期（周一）
WINDOW_END = date(2026, 9, 25)     # 昨天（周五）

SURNAME_POOL = list(
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜戚谢邹喻"
    "柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳唐罗薛伍余米贝姚孟高夏蔡"
    "田樊胡凌霍虞万支柯管卢莫房裘缪解应宗丁宣邓郁单杭洪包诸左石崔吉龚程邢裴陆"
    "荣翁荀羊惠甄曲封芮储靳段富巫乌焦巴弓牧山谷车侯全班仰秋仲伊宫宁仇栾甘祖武"
    "符刘景詹束龙叶幸司韶黎白怀蒲赖卓蔺屠蒙池乔闻党翟谭贡劳姬申扶冉雍桑桂牛通"
    "边燕冀浦尚农温别庄晏柴瞿阎连茹习艾容向古易慎戈廖庾居衡步都耿满弘匡国文寇"
    "广东欧沃利蔚越隆师巩聂晁勾敖融冷辛那简饶曾沙鞠须丰巢关查后荆红游竺权盖益"
)

# 试卷结构：section_name 决定能力分支归属（见 growth/performance.classify_dimension）
SECTIONS = [
    ("第一部分 听力", 20.0, 10),          # listening
    ("第二部分 语法", 15.0, 10),          # grammar
    ("第三部分 完形填空", 15.0, 10),      # reading
    ("第四部分 阅读理解", 15.0, 10),      # reading
    ("第五部分 词汇", 15.0, 10),          # vocabulary
    ("第六部分 书面表达", 20.0, 1),       # writing
]
DIM_OF_SECTION = ["listening", "grammar", "reading", "reading", "vocabulary", "writing"]
DIMS = ["listening", "grammar", "reading", "vocabulary", "writing"]

DICTATION_ROUNDS = [
    ("暑期衔接词汇默写", date(2026, 8, 3)),
    ("Unit 1 单词默写", date(2026, 9, 7)),
    ("Unit 2 单词默写", date(2026, 9, 14)),
    ("Unit 3 单词默写", date(2026, 9, 21)),
]
DICTATION_RANGES = [
    {"label": "优秀", "min": 90, "max": None},
    {"label": "合格", "min": 60, "max": 89},
    {"label": "待订正", "min": None, "max": 59},
]

WRITING_TASKS = [
    ("写作一：My Self-introduction", date(2026, 9, 4), 15.0),
    ("写作二：My Family", date(2026, 9, 16), 15.0),
    ("写作三：My School Day", date(2026, 9, 25), 15.0),
]

RECITE_TASKS = [
    ("Unit 1 课文背诵", "Unit 1 Section A 2d"),
    ("Unit 2 课文背诵", "Unit 2 Section B 2b"),
    ("Unit 3 课文背诵", "Unit 3 Section A 3a"),
]

NEW_EXAMS = [
    ("demo-shift-20260807", "2026学年七年级暑期衔接测评·英语", date(2026, 8, 7)),
    ("demo-monthly1-20260918", "2026-2027学年第一学期七年级第一次月考·英语", date(2026, 9, 18)),
]

NOTES = {
    "task_completed": ["完成 Unit {n} 单词默写", "完成《{w}》写作", "完成 Unit {n} 课文背诵", "完成周末作业并提交"],
    "spaced_review": ["Unit {n} 词汇达标复测", "默写错词重测达标", "写作语法点复测达标"],
    "correction_verified": ["订正 U{n} 默写错词并确认", "订正写作语法错误并确认", "订正作业错题并确认"],
    "teacher_observation": ["课堂朗读表现积极", "小组对话参与度高", "课堂提问回答到位"],
    "teacher_bonus": ["主动帮同学讲解错题", "课后主动补交作业", "主动整理错题本"],
    "weekly_goal": ["达成本周个人学习目标"],
}


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def allocate(total_units: int, weights: list[float], caps: list[int]) -> list[int]:
    """最大余额法：把 total_units 按权重分配到各槽位，不超过 cap，合计精确相等。"""
    out = [0] * len(weights)
    remaining = total_units
    active = [i for i in range(len(weights)) if caps[i] > 0]
    guard = 0
    while remaining > 0 and active and guard < 10000:
        guard += 1
        weight_sum = sum(weights[i] for i in active) or 1.0
        quota = {i: remaining * weights[i] / weight_sum for i in active}
        moved = False
        for i in active:
            give = min(int(quota[i]), caps[i] - out[i])
            if give > 0:
                out[i] += give
                remaining -= give
                moved = True
        if remaining <= 0:
            break
        for i in sorted(active, key=lambda k: quota[k] - int(quota[k]), reverse=True):
            if remaining <= 0:
                break
            if out[i] < caps[i]:
                out[i] += 1
                remaining -= 1
                moved = True
        active = [i for i in active if out[i] < caps[i]]
        if not moved:
            break
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不写库")
    args = parser.parse_args()

    rng = random.Random(SEED)
    settings = get_settings()
    factory = create_session_factory(settings.database_url)
    print(f"数据库：{settings.database_url}")

    with factory() as session:
        # ---------------- 阶段 0：读取现有数据 ----------------
        students = list(session.scalars(select(Student).order_by(Student.id)))
        enrollments = {
            e.student_id: e
            for e in session.scalars(select(Enrollment).where(
                Enrollment.term_id == TERM_ID, Enrollment.status == "active"))
        }
        workspace = session.get(WorkspaceState, TERM_ID)
        state = dict(workspace.state_json or {})
        print(f"学生 {len(students)} 人，workspace revision={workspace.revision}")

        state_students = [s for s in state.get("students", []) if isinstance(s, dict)]
        by_no = {str(s.get("id", "")).strip(): s for s in state_students}
        class_of_no = {s.student_no: enrollments[s.id].class_id
                       for s in students if s.id in enrollments}

        # ---------------- 阶段 1：姓名脱敏（姓氏+同学，班内不重名） ----------------
        name_map: dict[str, str] = {}
        new_name_by_no: dict[str, str] = {}
        for class_id in sorted(set(class_of_no.values())):
            pool = SURNAME_POOL[:]
            rng.shuffle(pool)
            used: set[str] = set()
            cursor = 0
            for student in students:
                if class_of_no.get(student.student_no) != class_id:
                    continue
                while cursor < len(pool) and pool[cursor] in used:
                    cursor += 1
                surname = pool[cursor] if cursor < len(pool) else rng.choice(SURNAME_POOL)
                cursor += 1
                used.add(surname)
                fake = f"{surname}同学"
                if not re.fullmatch(r".同学", student.name or ""):
                    name_map[student.name] = fake
                new_name_by_no[student.student_no] = fake
        print(f"姓名映射 {len(new_name_by_no)} 条（例：{list(new_name_by_no.items())[:3]}）")

        # ---------------- 阶段 2：学生生成模型 ----------------
        profiles: dict[int, dict] = {}
        for student in students:
            baseline = float(student.entrance_english or 60.0)
            ability = clamp(baseline / 100.0, 0.05, 1.0)
            engagement = clamp(rng.gauss(0.5, 0.24) * 0.75 + ability * 0.25, 0.02, 1.0)
            profiles[student.id] = {
                "baseline": baseline,
                "ability": ability,
                "engagement": engagement,
                "dim_offsets": {d: rng.gauss(0.0, 0.09) for d in DIMS},
            }

        # ---------------- 阶段 3：默写 / 写作 / 背诵 ----------------
        dictation: dict[str, list] = {}
        for student in students:
            p = profiles[student.id]
            row = []
            for round_index in range(len(DICTATION_ROUNDS)):
                score = p["ability"] * 0.9 + 0.03 * round_index + rng.gauss(0, 0.09)
                if rng.random() < 0.10:
                    score -= rng.uniform(0.12, 0.3)
                row.append(int(round(clamp(score, 0.15, 1.0) * 100 / 5) * 5))
            dictation[student.student_no] = row

        writings = []
        for index, (title, day, full) in enumerate(WRITING_TASKS):
            scores = {}
            for student in students:
                p = profiles[student.id]
                rate = clamp(p["ability"] + p["dim_offsets"]["writing"] + 0.02 * index
                             + rng.gauss(0, 0.08), 0.1, 1.0)
                scores[student.student_no] = round(rate * full * 2) / 2
            writings.append({"id": f"demo-writing-{index + 1}", "title": title,
                             "date": day.isoformat(), "fullScore": full, "scores": scores})

        recitations = []
        for index, (title, scope) in enumerate(RECITE_TASKS):
            status = {}
            for student in students:
                p = profiles[student.id]
                roll = p["ability"] * 0.7 + p["engagement"] * 0.3 + rng.gauss(0, 0.12)
                if roll >= 0.82:
                    level, retake = "A", ""
                elif roll >= 0.66:
                    level, retake = "B", ""
                elif roll >= 0.48:
                    level, retake = "C", ""
                else:
                    level = "F"
                    retake = "passed" if rng.random() < 0.55 else "not_passed"
                status[student.student_no] = {"level": level, "retake": retake}
            recitations.append({"id": f"demo-recite-{index + 1}", "title": title,
                                "scope": scope, "status": status})

        # ---------------- 阶段 4：新增考试与成绩 ----------------
        exams = [e for e in state.get("exams", []) if isinstance(e, dict)]
        exam_keys = {str(e.get("id")) for e in exams}
        added_exams = 0
        for source_key, exam_name, exam_day in NEW_EXAMS:
            if source_key in exam_keys:
                continue
            span = (exam_day - date(2026, 7, 8)).days / 72.0
            scores = {}
            for student in students:
                p = profiles[student.id]
                rate = clamp(p["ability"] + rng.gauss(0.03, 0.05) * span * 2, 0.12, 1.0)
                scores[student.student_no] = round(rate * 100 * 2) / 2
            exams.append({
                "id": source_key, "externalId": source_key, "scores": scores,
                "name": exam_name, "date": exam_day.isoformat(), "fullScore": 100,
                "examKind": "regular", "type": "english_total",
                "tierLines": {"a": 94, "b": 87, "c": 78}, "classGradeRanks": {},
            })
            added_exams += 1

        # 月考两名学生缺考（演示「缺失不补零」）
        monthly = next((e for e in exams if e.get("id") == "demo-monthly1-20260918"), None)
        absent_nos: list[str] = []
        already_absent = bool(monthly) and any(
            isinstance(item, dict) and item.get("attendanceStatus") == "absent"
            for item in monthly["scores"].values())
        if monthly and not already_absent:
            absent_nos = rng.sample([s.student_no for s in students], 2)
            for no in absent_nos:
                monthly["scores"][no] = {"attendanceStatus": "absent"}

        # 重算班内名次 / 年级名次 / 班级年级排名
        for exam in exams:
            raw = exam.get("scores", {})
            norm: dict[str, dict] = {}
            for no, item in raw.items():
                norm[no] = dict(item) if isinstance(item, dict) else {"英语": item}
            class_values: dict[str, list[tuple[str, float]]] = {}
            for no, entry in norm.items():
                value = entry.get("英语")
                if value in (None, ""):
                    continue
                cls = str((by_no.get(no) or {}).get("class", "")).strip()
                class_values.setdefault(cls, []).append((no, float(value)))
            for cls, pairs in class_values.items():
                for rank, (no, _v) in enumerate(sorted(pairs, key=lambda x: -x[1]), start=1):
                    norm[no]["classRank"] = rank
            all_pairs = [(no, float(e["英语"])) for no, e in norm.items() if e.get("英语") not in (None, "")]
            for rank, (no, _v) in enumerate(sorted(all_pairs, key=lambda x: -x[1]), start=1):
                norm[no]["gradeRank"] = rank
            for no, entry in norm.items():
                entry.setdefault("attendanceStatus", "present")
                entry.setdefault("classRank", None)
                entry.setdefault("gradeRank", None)
                entry.setdefault("classAtExam",
                                 str((by_no.get(no) or {}).get("class", "")).strip() or None)
            exam["scores"] = norm
            avgs = {cls: sum(v for _n, v in pairs) / len(pairs)
                    for cls, pairs in class_values.items() if pairs}
            ordered = sorted(avgs.items(), key=lambda kv: -kv[1])
            exam["classGradeRanks"] = {cls: i + 1 for i, (cls, _a) in enumerate(ordered)}

        state["exams"] = exams
        state["currentExamId"] = "demo-monthly1-20260918"
        print(f"新增考试 {added_exams} 场，默写 {len(DICTATION_ROUNDS)} 轮，"
              f"写作 {len(writings)} 次，背诵 {len(recitations)} 次，缺考 {len(absent_nos)} 人")

        # ---------------- 阶段 5：回写 workspace_state ----------------
        for item in state_students:
            no = str(item.get("id", "")).strip()
            if no in new_name_by_no:
                item["name"] = new_name_by_no[no]
            item["phone"] = ""
        state["teacher"] = {"name": "示范老师", "subject": "初中英语"}
        state["dictation"] = dictation
        state["dictationNames"] = [title for title, _day in DICTATION_ROUNDS]
        state["dictationRanges"] = [[dict(r) for r in DICTATION_RANGES] for _ in DICTATION_ROUNDS]
        state["writings"] = writings
        state["recitations"] = recitations
        for todo in state.get("todos", []):
            title = str(todo.get("title", ""))
            todo["title"] = title.replace("兰行·红迹", "红色研学").replace("兰苑农田", "学农基地")

        if args.dry_run:
            session.rollback()
            print("\n--dry-run：未写入数据库。")
            return

        # ---------------- 阶段 6：写回数据库（复用 App 兼容桥） ----------------
        workspace.state_json = state
        workspace.revision = int(workspace.revision or 1) + 1
        workspace.updated_at = utcnow()
        session.flush()
        sync_workspace_domains(session, TERM_ID, state)
        session.flush()
        for student in students:
            fake = new_name_by_no.get(student.student_no)
            if fake:
                student.name = fake
                student.parent_phone = None
                student.seat = None
        session.flush()
        print("已同步姓名 / 班级 / 考试与成绩到规范化表")

        # ---------------- 阶段 7：清理重复考试 ----------------
        keep_keys = {str(e["id"]) for e in exams}
        dropped = 0
        for exam in list(session.scalars(select(Exam).where(Exam.term_id == TERM_ID))):
            if exam.source_key in keep_keys:
                continue
            session.execute(delete(StudentItemResult).where(StudentItemResult.exam_id == exam.id))
            session.execute(delete(ExamQuestion).where(ExamQuestion.paper_version_id.in_(
                select(ExamPaperVersion.id).where(ExamPaperVersion.exam_id == exam.id))))
            session.execute(delete(ExamPaperVersion).where(ExamPaperVersion.exam_id == exam.id))
            session.execute(delete(ExamScore).where(ExamScore.exam_id == exam.id))
            session.delete(exam)
            dropped += 1
        session.flush()
        print(f"清理重复/多余考试 {dropped} 场")

        # ---------------- 阶段 8：逐题小分 ----------------
        session.execute(delete(StudentItemResult))
        session.execute(delete(ExamQuestion))
        session.execute(delete(ExamPaperVersion))
        session.flush()

        kept_exams = list(session.scalars(select(Exam).where(
            Exam.term_id == TERM_ID, Exam.status == "active").order_by(Exam.exam_date, Exam.id)))
        section_max_units = [int(round(total * 2)) for _n, total, _q in SECTIONS]
        item_count = 0
        for exam in kept_exams:
            paper = ExamPaperVersion(exam_id=exam.id, version=1, status="confirmed",
                                     full_score=exam.full_score, extraction_provider="demo",
                                     extraction_model="demo-generator", confirmed_at=utcnow())
            session.add(paper)
            session.flush()
            questions: list[tuple[ExamQuestion, int, int]] = []
            for s_index, (section_name, _total, n_questions) in enumerate(SECTIONS):
                per_units = section_max_units[s_index] // n_questions
                for q in range(n_questions):
                    question = ExamQuestion(
                        paper_version_id=paper.id, question_no=f"{s_index + 1}-{q + 1}",
                        section_name=section_name,
                        question_type="客观题" if n_questions > 1 else "主观题",
                        content_text=f"{section_name} 第 {q + 1} 题",
                        max_score=per_units / 2, included_in_analysis=True)
                    session.add(question)
                    questions.append((question, per_units, s_index))
            session.flush()
            section_idx: dict[int, list[int]] = {}
            for qi, (_q, _u, si) in enumerate(questions):
                section_idx.setdefault(si, []).append(qi)

            for student in students:
                score_row = session.scalar(select(ExamScore).where(
                    ExamScore.exam_id == exam.id, ExamScore.student_id == student.id))
                if score_row is None or score_row.attendance_status != "present" or score_row.total_score is None:
                    continue
                p = profiles[student.id]
                total = int(round(float(score_row.total_score) * 2))
                strengths = [clamp(p["ability"] + p["dim_offsets"][DIM_OF_SECTION[si]]
                                   + rng.gauss(0, 0.05), 0.1, 1.0)
                             for si in range(len(SECTIONS))]
                section_units = allocate(total, strengths, section_max_units)
                item_units = [0] * len(questions)
                for si in range(len(SECTIONS)):
                    idxs = section_idx[si]
                    caps = [questions[qi][1] for qi in idxs]
                    alloc = allocate(section_units[si], [1.0] * len(idxs), caps)
                    for k, qi in enumerate(idxs):
                        item_units[qi] = alloc[k]
                for qi, (question, max_units, _si) in enumerate(questions):
                    units = item_units[qi]
                    score = units / 2
                    session.add(StudentItemResult(
                        exam_id=exam.id, student_id=student.id, question_id=question.id,
                        score=score,
                        score_rate=round(score / (max_units / 2), 4) if max_units else None,
                        correct=(units == max_units), attendance_status="present"))
                    item_count += 1
            session.flush()
        print(f"生成试卷结构 {len(kept_exams)} 套，逐题小分 {item_count} 条")

        # ---------------- 阶段 9：成长森林事件账本 ----------------
        session.execute(delete(GrowthAward))
        session.execute(delete(GrowthEvent))
        session.execute(delete(StudentGrowthSnapshot))
        session.flush()

        exam_result = sync_exam_growth_events(session, term_id=TERM_ID)
        print(f"考试成长事件 {exam_result['created']} 条（跳过 {exam_result['skipped']}）")

        weeks = []
        cursor_day = WINDOW_START
        while cursor_day <= WINDOW_END:
            weeks.append(cursor_day)
            cursor_day += timedelta(days=7)

        manual_count = 0
        for student in students:
            p = profiles[student.id]
            engagement = p["engagement"]
            enrollment = enrollments.get(student.id)
            class_id = enrollment.class_id if enrollment else None
            for week_start in weeks:
                active_days = max(1, round(1 + engagement * 4))
                days = [week_start + timedelta(days=offset) for offset in range(5)]
                rng.shuffle(days)
                for day in sorted(days[:active_days]):
                    if day > WINDOW_END:
                        continue
                    plan = ["task_completed"] * (2 if engagement > 0.5 else 1)
                    if rng.random() < 0.6 * engagement:
                        plan.append("spaced_review")
                    if rng.random() < 0.7 * engagement:
                        plan.append("correction_verified")
                    if rng.random() < 0.35 * engagement:
                        plan.append("teacher_observation")
                    if rng.random() < 0.3 * engagement:
                        plan.append("teacher_bonus")
                    if day.weekday() == 4 and rng.random() < (0.2 + engagement * 0.75):
                        plan.append("weekly_goal")
                    for event_type in plan:
                        note = rng.choice(NOTES[event_type]).format(
                            n=rng.randint(1, 6), w=rng.choice(WRITING_TASKS)[0].split("：")[-1])
                        occurred = datetime.combine(day, time(rng.randint(8, 20), rng.randint(0, 59)),
                                                    tzinfo=timezone.utc)
                        event = growth_events.record_event(
                            session, student_id=student.id, term_id=TERM_ID,
                            event_type=event_type, occurred_at=occurred,
                            class_id_at_event=class_id, source_type="teacher",
                            source_id=f"demo-{event_type}-{student.id}-{day.isoformat()}",
                            business_date=day,
                            payload={"note": note,
                                     "request_id": f"demo-{student.id}-{day.isoformat()}"},
                            actor="teacher",
                            idempotency_key=growth_events.make_idempotency_key(
                                "demo", student.id, event_type, day.isoformat(), manual_count))
                        event.recorded_at = occurred + timedelta(hours=rng.randint(1, 20))
                        manual_count += 1
            if student.id % 20 == 0:
                session.flush()
        print(f"手工学习事件 {manual_count} 条")

        reversals = 0
        for student in [s for s in students if profiles[s.id]["engagement"] > 0.4][:3]:
            target = session.scalar(select(GrowthEvent).where(
                GrowthEvent.student_id == student.id, GrowthEvent.term_id == TERM_ID,
                GrowthEvent.event_type == "teacher_bonus",
                GrowthEvent.reverses_event_id.is_(None)).order_by(GrowthEvent.id).limit(1))
            if target is None:
                continue
            reversal = growth_events.record_event(
                session, student_id=student.id, term_id=TERM_ID, event_type="reversal",
                occurred_at=datetime.combine(target.business_date + timedelta(days=1), time(9, 0),
                                             tzinfo=timezone.utc),
                class_id_at_event=target.class_id_at_event, source_type="correction",
                source_id=str(target.id),
                payload={"note": "误录撤销：加分重复登记", "reverses_event_id": target.id},
                actor="teacher", reverses_event_id=target.id,
                idempotency_key=growth_events.make_idempotency_key("demo-reversal", target.id))
            reversal.recorded_at = reversal.occurred_at
            reversals += 1
        session.flush()
        print(f"撤销事件 {reversals} 条")

        for student in students:
            snapshots.build_snapshot(session, student_id=student.id, term_id=TERM_ID, persist=True)
        session.flush()
        print("已重建全部学生成长快照")

        # ---------------- 阶段 10：聊天记录脱敏 + 清理附件 ----------------
        message_fixed = 0
        if name_map:
            for message in session.scalars(select(AgentMessage)):
                text = message.content_text or ""
                if not text:
                    continue
                new_text = text
                for real, fake in name_map.items():
                    if real and real in new_text:
                        new_text = new_text.replace(real, fake)
                if new_text != text:
                    message.content_text = new_text
                    message_fixed += 1
        print(f"聊天记录脱敏 {message_fixed} 条")

        attachment_paths = [settings.attachments_dir / a.storage_name
                            for a in session.scalars(select(Attachment))]
        session.execute(delete(AgentMessageAttachment))
        session.execute(delete(Attachment))
        session.execute(delete(BackupRecord))
        session.flush()
        session.commit()

    # 落盘并清理附件文件（提交后再动磁盘，避免事务回滚造成不一致）
    removed = 0
    for path in attachment_paths:
        try:
            if path.exists():
                path.unlink()
                removed += 1
        except OSError as error:
            print(f"  附件删除失败 {path.name}: {error}")
    if settings.attachments_dir.is_dir():
        for leftover in settings.attachments_dir.iterdir():
            if leftover.is_file() and leftover.name != ".DS_Store":
                try:
                    leftover.unlink()
                    removed += 1
                except OSError:
                    pass
    print(f"已删除含真实信息的附件文件 {removed} 个")

    engine = factory.kw["bind"]
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
    print("\n完成。可打开 App 查看效果。")


if __name__ == "__main__":
    main()
