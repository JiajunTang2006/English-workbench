"""考试整体分析能力

P0-3: 统一结构化输出契约。
Schema 从 schema_contract 导入，与 Pydantic 模型和前端渲染对齐。
"""

from __future__ import annotations

from ..schema_contract import EXAM_ANALYSIS_OUTPUT_SCHEMA

import logging
from typing import Any


logger = logging.getLogger(__name__)


# --- 输出结构定义（统一契约）---
# 从 schema_contract 导入，不再在此文件中定义独立的矛盾 schema。
# 统一字段：answer_type, summary, findings(title/description/evidence_ids/severity),
#           recommendations(action/rationale/supports/priority), limitations



# --- 分析维度 ---

ANALYSIS_DIMENSIONS = [
    {
        "name": "score_distribution",
        "display": "分数分布",
        "description": "分析整体分数分布形态（正态/偏态）、集中趋势和离散程度",
    },
    {
        "name": "difficulty",
        "display": "难度分析",
        "description": "评估各题目难度系数，识别过难/过易的题目",
    },
    {
        "name": "knowledge",
        "display": "知识点覆盖",
        "description": "分析知识点覆盖度和各知识点得分率，识别薄弱区域",
    },
    {
        "name": "error_pattern",
        "display": "错误模式",
        "description": "归纳常见错误类型和典型错误答案",
    },
]


def get_exam_analysis_prompt(exam_info: dict[str, Any]) -> str:
    """生成考试整体分析的提示词。

    :param exam_info: 考试基本信息（名称、科目、时间等）
    :return: 提示词文本
    """
    return f"""请对以下考试进行整体分析：

考试信息：{exam_info}

分析维度：
1. 分数分布：分析整体分数分布形态、集中趋势和离散程度
2. 难度分析：评估各题目难度系数，识别过难/过易的题目
3. 知识点覆盖：分析知识点覆盖度和各知识点得分率，识别薄弱区域
4. 错误模式：归纳常见错误类型和典型错误答案

请先调用工具获取数据，然后给出结构化分析结果。

输出必须为 JSON 格式，包含以下字段：
- answer_type: 固定为 "exam_analysis"
- summary: 考试整体概况（1-2句话）
- findings: 分析发现列表，每个 finding 包含：
  - title: 发现标题（简短一行）
  - description: 详细描述
  - evidence_ids: 支撑证据 ID 列表（至少 1 个，引用工具返回的 evidence_id）
  - severity: 严重程度（info/warning/critical）
- recommendations: 教学建议列表，每个 recommendation 包含：
  - action: 建议行动
  - rationale: 建议理由
  - supports: 支撑证据 ID 列表（至少 1 个）
  - priority: 优先级（low/medium/high）
- limitations: 分析局限性（可选）
"""
