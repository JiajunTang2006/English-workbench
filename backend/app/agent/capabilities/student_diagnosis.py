"""学生诊断能力

P0-3: 统一结构化输出契约。
Schema 从 schema_contract 导入，与 Pydantic 模型和前端渲染对齐。
"""

from __future__ import annotations

from ..schema_contract import STUDENT_DIAGNOSIS_OUTPUT_SCHEMA

import logging
from typing import Any


logger = logging.getLogger(__name__)


# 统一契约：从 schema_contract 导入


def get_student_diagnosis_prompt(
    exam_info: dict[str, Any], student_ref: str
) -> str:
    """生成学生诊断的提示词。

    :param exam_info: 考试基本信息
    :param student_ref: 学生匿名编号
    :return: 提示词文本
    """
    return f"""请对以下学生进行个体诊断：

考试信息：{exam_info}
学生编号：{student_ref}

诊断要求：
1. 获取该学生的详细成绩数据
2. 识别学生的优势知识点和薄弱知识点
3. 分析错误原因（审题/词汇/语法/定位/推断/表达）
4. 给出针对性的学习建议

请先调用工具获取数据，然后给出结构化诊断结果。
注意：模型内部可使用匿名编号 {student_ref} 对照数据，但面向教师的文字不要输出
student01、student_01 等程序编号，也不要使用真实姓名，统一称“该生”。

输出必须为 JSON 格式，包含以下字段：
- answer_type: 固定为 "student_diagnosis"
- summary: 学生整体评价（1-2句话）
- findings: 分析发现列表，每个 finding 包含：
  - title: 发现标题（如"词汇薄弱"、"阅读理解强项"）
  - description: 详细描述
  - evidence_ids: 支撑证据 ID 列表（至少 1 个）
  - severity: 严重程度（info/warning/critical）
- recommendations: 针对性学习建议列表，每个 recommendation 包含：
  - action: 建议行动
  - rationale: 建议理由
  - supports: 支撑证据 ID 列表（至少 1 个）
  - priority: 优先级（low/medium/high）
- limitations: 分析局限性（可选）
- profile_summary: 结合既有画像和本次证据重写的教师可读自然语言摘要，不要输出 JSON 或机械字段列表
"""
