from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, JSON, String, text
from sqlalchemy.orm import Mapped, mapped_column

from ....core.db.database import Base
from ....core.db.models import PrimaryKeyMixin


class AdminAuditLog(PrimaryKeyMixin, Base):
    __tablename__ = "admin_audit_log"

    admin_user_id: Mapped[int] = mapped_column(ForeignKey("admin_user.id"), nullable=False, index=True)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_type: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    target_id: Mapped[int | None] = mapped_column(nullable=True, index=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=text("current_timestamp(0)"),
        index=True,
    )
