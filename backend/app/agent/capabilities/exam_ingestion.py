"""试卷录入能力

P0-3: 统一结构化输出契约。
Schema 从 schema_contract 导入，与 Pydantic 模型和前端渲染对齐。
"""

from __future__ import annotations

from ..schema_contract import EXAM_INGESTION_OUTPUT_SCHEMA

import logging
from typing import Any


logger = logging.getLogger(__name__)


# 统一契约：从 schema_contract 导入


def get_exam_ingestion_prompt() -> str:
    """生成试卷录入的提示词。"""
    return """请识别并提取试卷图片中的所有题目信息。

提取要求：
1. 识别每道题的题号、题型、内容、分值
2. 选择题需提取所有选项
3. 如有标准答案，一并提取
4. 尝试标注每题涉及的知识点
5. 给出 OCR 置信度评估

输出必须为 JSON 格式，包含以下字段：
- answer_type: 固定为 "exam_ingestion"
- summary: 试卷录入概况
- questions_extracted: 提取的题目数量
- confidence: OCR 整体置信度（0-1）
- issues: 提取过程中的问题列表
- findings: 关键发现列表（可选，如格式异常、模糊区域等）
- recommendations: 后续处理建议列表（可选）
- limitations: 分析局限性（可选）
"""
