from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class OperationLogRead(BaseModel):
    id: int
    user_id: int | None = None
    job_id: int | None = None
    job_title: str | None = None
    application_id: int | None = None
    talent_profile_id: int | None = None
    log_type: str
    title: str
    summary: str
    actor_type: str
    actor_name: str | None = None
    status_label: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class OperationLogListRead(BaseModel):
    items: list[OperationLogRead] = Field(default_factory=list)
