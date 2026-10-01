"""Canonical question labels, shared by ingestion, settings and statistics."""
from copy import deepcopy

ENGLISH_PAPER_QUESTION_TYPES = (
    "听力理解", "阅读理解", "完形填空", "词汇运用", "语法填空", "书面表达",
)


def canonical_question_type(value):
    # Keep printed section prefixes and original question content intact.
    return value.replace("任务型阅读", "阅读理解") if isinstance(value, str) else value


def merge_reading_rows(rows):
    """Retain every range, including disjoint ranges and printed subquestions."""
    merged = {}
    for original in rows:
        row = deepcopy(original)
        name = canonical_question_type(row["question_type"])
        numbers = row.get("question_numbers", "").strip()
        if name in merged:
            previous = merged[name]["question_numbers"]
            merged[name]["question_numbers"] = "、".join(v for v in (previous, numbers) if v)
        else:
            row.update(question_type=name, question_numbers=numbers)
            merged[name] = row
    return list(merged.values())


def normalize_workspace_question_types(state):
    result = deepcopy(state)
    for row in result.get("errors", []):
        if isinstance(row, dict) and "type" in row:
            row["type"] = canonical_question_type(row["type"])
    return result
