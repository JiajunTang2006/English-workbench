from backend.app.services.moni_sync import _derive_exam_grade_ranks, _discover_class_root, _is_english_subject, _make_payload, _merge_exam_tier_cutoffs, _subject_score


class _Reader:
    def __init__(self, rows):
        self.rows = rows

    def query(self, path, *, limit=100, predicates=None):
        if path.endswith("/questions/.list.jsonl"):
            return self.rows["questions"]
        if path.endswith("/question-scores/.list.jsonl"):
            return self.rows.get("question_scores", [])
        if path.endswith("/students/.list.jsonl"):
            return self.rows["students"]
        if path.endswith("/student-progress/.list.jsonl"):
            return self.rows.get("student_progress", [])
        if path.endswith("/student-data/exams/.list.jsonl"):
            return self.rows.get("aggregate", [])
        return []

    def read(self, path):
        if path.endswith("/students/.fields.jsonl"):
            return self.rows.get("student_fields", [])
        if path.endswith("/student-progress/.fields.jsonl"):
            return self.rows.get("student_progress_fields", [])
        return []


class _RootManager:
    def __init__(self, listing):
        self.listing = listing

    def call_tool(self, plugin_id, tool_name, arguments):
        assert plugin_id == "moni"
        assert tool_name == "moni_vfs_list"
        path = arguments["path"]
        if path == "/classes":
            return {"content": [{"type": "text", "text": "No Cym VFS node for path: /classes"}]}
        return {"content": [{"type": "text", "text": self.listing}]}


class _RootReader:
    def __init__(self, listing):
        self.manager = _RootManager(listing)


def test_subject_score_prefers_english_score_over_exam_total():
    row = {"subjectScore": 249, "totalScore": 249, "score": 84.5}
    assert _subject_score(row, subject_id="english", subject_name="英语") == 84.5


def test_moni_recovers_question_scores_when_initial_page_is_empty():
    class EmptyFirstPageReader(_Reader):
        def query(self, path, *, limit=100, predicates=None):
            if path.endswith("/question-scores/.list.jsonl"):
                if not predicates:
                    return []
                return [{"studentId": "s-1", "questionNo": "1", "score": 2,
                         "fullScore": 2}]
            return super().query(path, limit=limit, predicates=predicates)

    reader = EmptyFirstPageReader({
        "questions": [{"questionNo": "1", "maxScore": 2}],
        "students": [{"studentId": "s-1", "studentNo": "1",
                      "studentName": "测试", "score": 2}],
        "aggregate": [],
    })
    payload = _make_payload(reader, {"classId": "c-1", "className": "一班"},
                            {"examId": "e-1", "subjectId": "english",
                             "subjectName": "英语", "subjectFullScore": 2})
    assert payload is not None
    assert len(payload.students[0].item_scores) == 1
    assert payload.students[0].item_scores[0].score == 2


def test_moni_keeps_other_students_when_partial_reread_succeeds():
    """回归（评审 P1）：补读只有一人返回结果时，不得整体替换首屏小分。

    合成场景：首屏已含甲乙两人小分，按学生补读时只有甲返回结果。旧实现用
    补读列表整体替换首屏，导致乙的小分凭空消失。
    """
    class PartialRereadReader(_Reader):
        def query(self, path, *, limit=100, predicates=None):
            if path.endswith("/question-scores/.list.jsonl"):
                if not predicates:
                    return [
                        {"studentId": "s-1", "questionNo": "1", "score": 2, "fullScore": 2},
                        {"studentId": "s-2", "questionNo": "1", "score": 1, "fullScore": 2},
                    ]
                if predicates[0]["value"] == "s-1":
                    return [{"studentId": "s-1", "questionNo": "1", "score": 2,
                             "fullScore": 2}]
                return []
            return super().query(path, limit=limit, predicates=predicates)

    reader = PartialRereadReader({
        "questions": [{"questionNo": "1", "maxScore": 2}],
        "students": [
            {"studentId": "s-1", "studentNo": "1", "studentName": "甲", "score": 2},
            {"studentId": "s-2", "studentNo": "2", "studentName": "乙", "score": 1},
        ],
        "aggregate": [],
    })
    payload = _make_payload(reader, {"classId": "c-1", "className": "一班"},
                            {"examId": "e-1", "subjectId": "english",
                             "subjectName": "英语", "subjectFullScore": 2})
    assert payload is not None
    counts = {student.external_id: len(student.item_scores)
              for student in payload.students}
    assert counts == {"s-1": 1, "s-2": 1}


def test_moni_sync_ignores_non_english_subjects():
    assert _is_english_subject("英语") is True
    assert _is_english_subject("English") is True
    assert _is_english_subject("数学") is False


def test_moni_discovers_alternate_class_root_without_hardcoded_class_path():
    assert _discover_class_root(_RootReader(".fields.jsonl\n.list.jsonl\nclass-a/")) == "/class"


def test_moni_payload_uses_numeric_subject_id_and_subject_score():
    reader = _Reader({
        "questions": [],
        "students": [{
            "studentId": "s-1", "studentNo": "20261101", "studentName": "测试甲",
            "subjectId": 29, "subjectName": "英语", "subjectScore": 99.5,
            "totalScore": 373.5, "subjectRankInClass": 1,
        }],
        "aggregate": [],
    })
    payload = _make_payload(reader, {"classId": "class-a", "className": "A班"}, {
        "examId": "exam-1", "subjectId": 29, "subjectName": "英语", "examName": "入学考试",
    }, class_root="/class")
    assert payload is not None
    assert payload.students[0].total_score == 99.5
    assert payload.students[0].class_rank == 1


def test_moni_payload_derives_cutoffs_from_subject_tier_in_half_point_steps():
    reader = _Reader({
        "questions": [],
        "students": [
            {"studentId": "a", "studentNo": "1", "studentName": "A", "subjectScore": 99.5, "subjectTier": "ELITE", "totalTier": "REGULAR"},
            {"studentId": "b", "studentNo": "2", "studentName": "B", "subjectScore": 88.74, "subjectTier": "KEY", "totalTier": "ELITE"},
            {"studentId": "c", "studentNo": "3", "studentName": "C", "subjectScore": 71.26, "subjectTier": "GOOD", "totalTier": "KEY"},
            {"studentId": "d", "studentNo": "4", "studentName": "D", "subjectScore": 42.0, "subjectTier": "REGULAR", "totalTier": "GOOD"},
        ],
        "aggregate": [],
    })
    payload = _make_payload(reader, {"classId": "class-1", "className": "711班"}, {
        "examId": "exam-1", "subjectId": 29, "subjectName": "英语", "examName": "入学考试", "subjectFullScore": 100,
    })
    assert payload is not None
    assert payload.exam.tier_a_cutoff == 99.5
    assert payload.exam.tier_b_cutoff == 88.5
    assert payload.exam.tier_c_cutoff == 71.5


def test_moni_tier_cutoffs_merge_across_classes_for_one_exam():
    reader = _Reader({
        "questions": [],
        "students": [{"studentId": "s", "studentNo": "1", "studentName": "测试", "subjectScore": 90, "subjectTier": "ELITE"}],
        "aggregate": [],
    })
    first = _make_payload(reader, {"classId": "c-711", "className": "711班"}, {"examId": "exam-1", "subjectId": 29, "subjectName": "英语"})
    second = _make_payload(reader, {"classId": "c-712", "className": "712班"}, {"examId": "exam-1", "subjectId": 29, "subjectName": "英语"})
    assert first is not None and second is not None
    first.exam.tier_a_cutoff, second.exam.tier_a_cutoff = 90, 88.5
    _merge_exam_tier_cutoffs([first, second])
    assert first.exam.tier_a_cutoff == second.exam.tier_a_cutoff == 88.5


def test_moni_derives_competition_grade_rank_when_subject_rank_is_missing():
    reader = _Reader({
        "questions": [],
        "students": [
            {"studentId": "a", "studentNo": "1", "studentName": "甲", "subjectScore": 90},
            {"studentId": "b", "studentNo": "2", "studentName": "乙", "subjectScore": 90},
            {"studentId": "c", "studentNo": "3", "studentName": "丙", "subjectScore": 80},
        ],
        "aggregate": [],
    })
    payload = _make_payload(reader, {"classId": "c-711", "className": "711班"}, {"examId": "exam-1", "subjectId": 29, "subjectName": "英语"})
    assert payload is not None
    _derive_exam_grade_ranks([payload])
    assert [student.grade_rank for student in payload.students] == [1, 1, 3]


def test_moni_does_not_label_partial_class_sort_as_grade_rank():
    reader = _Reader({
        "questions": [],
        "students": [
            {"studentId": "a", "studentNo": "1", "studentName": "甲", "subjectScore": 90},
            {"studentId": "b", "studentNo": "2", "studentName": "乙", "subjectScore": 80},
        ],
        "aggregate": [],
    })
    payload = _make_payload(reader, {"classId": "c-711", "className": "711班"}, {"examId": "exam-1", "subjectId": 29, "subjectName": "英语"})
    assert payload is not None
    _derive_exam_grade_ranks([payload], complete_grade_scope={payload.exam.external_id: False})
    assert [student.grade_rank for student in payload.students] == [None, None]


def test_moni_payload_uses_subject_score_and_subject_full_score():
    reader = _Reader({
        "questions": [{"questionNo": "1", "fullScore": 100}],
        "students": [{
            "studentId": "s-1", "studentNo": "20261101", "studentName": "测试甲",
            "subjectScore": 249, "score": 84.5, "totalScore": 249,
            "subjectRankInClass": 3, "subjectGradeRank": 18,
        }],
        "aggregate": [{
            "examId": "exam-1", "subjectId": "subject-english", "subjectName": "英语", "studentId": "s-1",
            "score": 84.5, "classRank": 3, "gradeRank": 18,
        }],
    })
    payload = _make_payload(
        reader,
        {"classId": "class-1", "className": "711班"},
        {
            "examId": "exam-1", "subjectId": "subject-english", "subjectName": "英语",
            "examName": "入学考试", "subjectFullScore": 100,
        },
    )
    assert payload is not None
    assert payload.exam.full_score == 100
    assert payload.students[0].total_score == 84.5
    assert payload.students[0].class_rank == 3
    assert payload.students[0].grade_rank == 18


def test_moni_payload_joins_official_subject_grade_rank_by_student_id():
    reader = _Reader({
        "questions": [],
        "students": [{
            "studentId": "s-1", "studentNo": "20261101", "studentName": "测试甲",
            "subjectScore": 96.5, "subjectRankInClass": 2,
        }],
        "student_progress": [{
            "studentId": "s-1", "subjectScore": 96.5,
            "subjectGradeRank": 17, "subjectGradePercentile": 97.2,
        }],
        "aggregate": [],
    })
    payload = _make_payload(
        reader,
        {"classId": "class-1", "className": "711班"},
        {"examId": "exam-1", "subjectId": 29, "subjectName": "英语", "examName": "入学考试"},
    )
    assert payload is not None
    assert payload.students[0].grade_rank == 17
    _derive_exam_grade_ranks([payload])
    assert payload.students[0].grade_rank == 17


def test_moni_payload_recognizes_same_business_values_after_tenant_field_drift():
    reader = _Reader({
        "questions": [],
        "students": [{
            "tenant_student_key": "student-x", "english_result_value": 96.5,
            "tenant_level_value": "ELITE", "classPosition": 2,
        }],
        "student_fields": [
            {"name": "tenant_student_key", "description": "student unique identifier 学生唯一标识"},
            {"name": "english_result_value", "description": "English subject score 英语单科成绩"},
            {"name": "tenant_level_value", "description": "English subject tier 英语单科层级"},
        ],
        "student_progress": [{"learner_reference": "student-x", "year_position_value": 17}],
        "student_progress_fields": [
            {"name": "learner_reference", "description": "learner identifier 学生标识"},
            {"name": "year_position_value", "description": "subject grade rank 英语年级排名"},
        ],
        "aggregate": [],
    })
    payload = _make_payload(
        reader,
        {"classKey": "teacher-specific-class", "className": "新教师班级"},
        {
            "examKey": "teacher-specific-exam", "subjectKey": "english-subject-any-id",
            "courseName": "English", "examName": "适配测试", "subjectFullScore": 100,
        },
        class_root="/class",
    )
    assert payload is not None
    assert payload.students[0].external_id == "student-x"
    assert payload.students[0].total_score == 96.5
    assert payload.students[0].class_rank == 2
    assert payload.students[0].grade_rank == 17
    assert payload.exam.tier_a_cutoff == 96.5


def test_moni_payload_keeps_exam_metadata_when_detail_is_not_available():
    reader = _Reader({
        "questions": [],
        "students": [{"studentId": "s-1", "studentNo": "20261101", "studentName": "测试甲", "subjectName": "英语"}],
        "aggregate": [{"examId": "exam-1", "subjectId": "total", "subjectName": "总分", "studentId": "s-1", "score": 249}],
    })
    payload = _make_payload(
        reader,
        {"classId": "class-1", "className": "711班"},
        {"examId": "exam-1", "subjectId": "subject-english", "subjectName": "英语", "examName": "入学考试"},
    )
    assert payload is not None
    assert payload.questions == []
    assert payload.exam.full_score == 100
    assert payload.students[0].total_score is None
