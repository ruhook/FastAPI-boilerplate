from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from ....core.db.database import Base
from ....core.db.models import DataBackedSoftDeleteEntityMixin


class MailAccount(DataBackedSoftDeleteEntityMixin, Base):
    __tablename__ = "mail_account"

    admin_user_id: Mapped[int | None] = mapped_column(ForeignKey("admin_user.id"), nullable=True, index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    smtp_username: Mapped[str] = mapped_column(String(255), nullable=False)
    smtp_host: Mapped[str] = mapped_column(String(255), nullable=False)
    smtp_port: Mapped[int] = mapped_column(Integer, nullable=False)
    security_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    auth_secret: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True, server_default=text("'pending'"))
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
