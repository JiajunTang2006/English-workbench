from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AttachmentWorkspaceDelete(BaseModel):
    term_id: int = Field(gt=0)
    expected_revision: int = Field(ge=0)
    document_id: str = Field(min_length=1, max_length=200)
    error_ids: list[str] = Field(default_factory=list)


class AttachmentWorkspaceDeleteRead(BaseModel):
    attachment_id: int
    revision: int
    state: dict[str, Any]
