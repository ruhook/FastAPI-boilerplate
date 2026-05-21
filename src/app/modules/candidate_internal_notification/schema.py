from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class CandidateInternalNotificationRead(BaseModel):
    id: int
    recipient_user_id: int
    sender_admin_user_id: int | None = None
    category: str
    title: str
    description: str
    action_url: str | None = None
    is_read: bool
    read_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class CandidateInternalNotificationListPage(BaseModel):
    items: list[CandidateInternalNotificationRead]
    total: int
    unread_count: int
    page: int
    page_size: int


class CandidateInternalNotificationReadAllResponse(BaseModel):
    updated_count: int
