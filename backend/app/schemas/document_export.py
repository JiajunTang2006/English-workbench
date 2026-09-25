"""L4 文档导出请求协议（DocumentSpec v1）。

对应方案 §11.2 与 L0_PROTOCOL_SCHEMAS.md 第 4 节。
约束：
- provider.type=remote 仅在 document_export_enabled 与 remote_document_provider_enabled
  同时开启时接受，否则路由层返回 409；
- 导出内容不含未确认模型输出（由导出服务从 confirmed 源取材）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

_ZH_LOCALE = Literal["zh-CN"]


class DocumentSpecSource(BaseModel):
    session_ids: list[int] = Field(default_factory=list)
    attachment_ids: list[int] = Field(default_factory=list)


class DocumentSpecProvider(BaseModel):
    type: Literal["local", "remote"] = "local"
    remote_enabled: bool = False


class DocumentSpec(BaseModel):
    """DocumentSpec v1（方案 §11.2 / L0 协议第 4 节）。"""

    format: Literal["pdf", "docx"]
    template: Literal["report", "worksheet", "summary"] = "report"
    title: str = Field(default="", description="文档标题")
    locale: _ZH_LOCALE = "zh-CN"  # 仅支持中文；违反即校验失败
    source: DocumentSpecSource = Field(default_factory=DocumentSpecSource)
    provider: DocumentSpecProvider = Field(default_factory=DocumentSpecProvider)
    options: dict = Field(default_factory=dict)

    # 兼容前端可能直接塞入结构化内容（教师确认稿）。服务端以 source 为准重建，
    # 此字段仅作透传快照，不参与事实计算。
    inline_content: dict | None = None
