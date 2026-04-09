from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, JSON, String, text
from sqlalchemy.orm import Mapped, mapped_column

from ...core.db.database import Base
from ...core.db.models import PrimaryKeyMixin


class OperationLog(PrimaryKeyMixin, Base):
    __tablename__ = "operation_log"

    user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"), nullable=True, index=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("job.id"), nullable=True, index=True)
    application_id: Mapped[int | None] = mapped_column(ForeignKey("candidate_application.id"), nullable=True, index=True)
    talent_profile_id: Mapped[int | None] = mapped_column(ForeignKey("talent_profile.id"), nullable=True, index=True)
    log_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=text("current_timestamp(0)"),
        index=True,
    )
