"""能力包：所有 Agent 分析能力的定义和注册入口。"""

from .exam_analysis import (
    EXAM_ANALYSIS_OUTPUT_SCHEMA,
    get_exam_analysis_prompt,
)
from .student_diagnosis import (
    STUDENT_DIAGNOSIS_OUTPUT_SCHEMA,
    get_student_diagnosis_prompt,
)
from .review_plan import (
    REVIEW_PLAN_OUTPUT_SCHEMA,
    get_review_plan_prompt,
)
from .exam_ingestion import (
    EXAM_INGESTION_OUTPUT_SCHEMA,
    get_exam_ingestion_prompt,
)

__all__ = [
    "EXAM_ANALYSIS_OUTPUT_SCHEMA",
    "get_exam_analysis_prompt",
    "STUDENT_DIAGNOSIS_OUTPUT_SCHEMA",
    "get_student_diagnosis_prompt",
    "REVIEW_PLAN_OUTPUT_SCHEMA",
    "get_review_plan_prompt",
    "EXAM_INGESTION_OUTPUT_SCHEMA",
    "get_exam_ingestion_prompt",
]
