"""复习计划生成能力

P0-3: 统一结构化输出契约。
Schema 从 schema_contract 导入，与 Pydantic 模型和前端渲染对齐。
"""

from __future__ import annotations

from ..schema_contract import REVIEW_PLAN_OUTPUT_SCHEMA

import logging
from typing import Any


logger = logging.getLogger(__name__)


# 统一契约：从 schema_contract 导入


def get_review_plan_prompt(exam_info: dict[str, Any]) -> str:
    """生成复习计划的提示词。"""
    return f"""请根据以下考试的分析结果，生成针对性的复习计划：

考试信息：{exam_info}

复习计划要求：
1. 按知识点优先级排序（得分率低的优先）
2. 每个知识点给出练习类型和难度建议
3. 制定分周复习时间表
4. 关注风险学生的专项辅导建议

请先调用工具获取考试统计数据、知识点覆盖和错误原因数据。

输出必须为 JSON 格式，包含以下字段：
- answer_type: 固定为 "review_plan"
- summary: 复习计划概述
- findings: 关键发现列表（薄弱知识点、风险学生等），每个 finding 包含：
  - title: 发现标题
  - description: 详细描述
  - evidence_ids: 支撑证据 ID 列表（至少 1 个）
  - severity: 严重程度（info/warning/critical）
- recommendations: 复习建议列表，每个 recommendation 包含：
  - action: 建议行动
  - rationale: 建议理由
  - supports: 支撑证据 ID 列表（至少 1 个）
  - priority: 优先级（low/medium/high）
- timeline: 复习时间安排（文本描述）
- limitations: 分析局限性（可选）
"""
