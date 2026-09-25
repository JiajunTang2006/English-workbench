from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AttachmentCreate(BaseModel):
    """上传接口使用 base64 传输，文件内容落盘后不再进入 workspace JSON。"""

    title: str = Field(min_length=1, max_length=200)
    original_name: str = Field(min_length=1, max_length=255)
    mime_type: str = Field(default="application/octet-stream", max_length=120)
    content_base64: str = Field(min_length=1)
    metadata: dict = Field(default_factory=dict)


class AttachmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    term_id: int
    title: str
    original_name: str
    mime_type: str
    size_bytes: int
    sha256: str
    created_at: datetime
    metadata: dict = Field(default_factory=dict, validation_alias="metadata_json")

