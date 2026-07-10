from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ...core.db.database import Base


class AuthRefreshSession(Base):
    __tablename__ = "auth_refresh_session"
    __table_args__ = (
        Index("ix_auth_refresh_session_account", "portal", "account_id"),
        Index("ix_auth_refresh_session_family", "family_id", "revoked_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    portal: Mapped[str] = mapped_column(String(16), nullable=False)
    account_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    family_id: Mapped[str] = mapped_column(String(36), nullable=False)
    parent_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("auth_refresh_session.id"),
        nullable=True,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rotation_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revocation_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    user_agent_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rotation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
