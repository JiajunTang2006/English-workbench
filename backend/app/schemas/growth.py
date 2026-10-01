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


class GrowthTeacherCreate(BaseModel):
    """新建本机教师档案（单机软件，不含密码）。

    ``activate`` 默认 True：老师新建自己的档案通常就是准备以自己的身份开始
    记录，因此默认顺带切换当前教师；替别人建档案时可显式传 False。
    """

    name: str = Field(min_length=1, max_length=32)
    presets: dict[str, Any] = Field(default_factory=dict)
    activate: bool = True


class GrowthTeacherRename(BaseModel):
    name: str = Field(min_length=1, max_length=32)


class GrowthTeacherPresetsUpdate(BaseModel):
    """覆盖某位教师的补录预设：``{event_type: {"points": int, "visible": bool}}``。

    只约束形状；分值范围与类别合法性由服务层按当前规则版本校验。
    """

    term_id: int | None = Field(default=None, gt=0)
    presets: dict[str, Any] = Field(default_factory=dict)


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
