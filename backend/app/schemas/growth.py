"""成长树 API 的请求模型（方案 §5.2）。

响应统一为普通 dict，字段由后端确定性输出；这里只约束写入类请求，避免在
Pydantic 层再实现一套计分或校验逻辑。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field


class GrowthActivityItem(BaseModel):
    """单条教师补录。事由必填；教师确认的正整数点数完整入账。"""

    student_id: int = Field(gt=0)
    event_type: str = Field(min_length=1, max_length=40)
    note: str = Field(min_length=1, max_length=500)
    occurred_at: str | None = None
    points: int | None = Field(default=None, ge=1)


class GrowthActivityBatch(BaseModel):
    """批量补录：同一 ``request_id`` 重试不重复计分。"""

    term_id: int | None = Field(default=None, gt=0)
    request_id: str | None = Field(default=None, max_length=120)
    items: list[GrowthActivityItem] = Field(default_factory=list, max_length=200)


class GrowthReversalRequest(BaseModel):
    """误录撤销：引用原事件，不删除原记录。"""

    term_id: int | None = Field(default=None, gt=0)
    reason: str = Field(default="误录撤销", max_length=500)


class GrowthLegacyPreviewRequest(BaseModel):
    """旧版导入预览（只读）。"""

    term_id: int | None = Field(default=None, gt=0)
    payload: dict[str, Any] = Field(default_factory=dict)


class GrowthLegacyConfirmRequest(BaseModel):
    """旧版导入确认；``carry_over_date`` 为教师确认的结转日（缺日期记录用）。"""

    term_id: int | None = Field(default=None, gt=0)
    payload: dict[str, Any] = Field(default_factory=dict)
    batch_id: str | None = Field(default=None, max_length=120)
    carry_over_date: date | None = None


class GrowthLegacyBatchReverseRequest(BaseModel):
    """整批撤销一次旧版导入。"""

    term_id: int | None = Field(default=None, gt=0)
    reason: str = Field(default="整批撤销导入", max_length=500)
