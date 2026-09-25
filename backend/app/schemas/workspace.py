from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WorkspaceStateWrite(BaseModel):
    state: dict[str, Any]
    expected_revision: int = Field(ge=0)


class WorkspaceStateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    state: dict[str, Any] | None
    revision: int
    updated_at: datetime | None = None
