from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from ...core.db.database import Base
from ...core.db.models import StandardEntityMixin


class TalentProfileMergeLog(StandardEntityMixin, Base):
    __tablename__ = "talent_profile_merge_log"

    talent_profile_id: Mapped[int] = mapped_column(
        ForeignKey("talent_profile.id"),
        nullable=False,
        index=True,
    )
    application_id: Mapped[int] = mapped_column(
        ForeignKey("candidate_application.id"),
        nullable=False,
        index=True,
    )
    operator_admin_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_user.id"),
        nullable=True,
        index=True,
    )
    merge_strategy: Mapped[str] = mapped_column(String(32), nullable=False, default="manual_merge")
    merged_fields: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
