from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from ....core.db.database import Base
from ....core.db.models import DataBackedSoftDeleteEntityMixin


class AdminUser(DataBackedSoftDeleteEntityMixin, Base):
    __tablename__ = "admin_user"

    name: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(20), nullable=False, index=True, unique=True)
    email: Mapped[str] = mapped_column(String(100), nullable=False, index=True, unique=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    profile_image_url: Mapped[str] = mapped_column(String(255), nullable=False)
    is_superuser: Mapped[bool] = mapped_column(nullable=False, default=False, index=True)
    role_id: Mapped[int | None] = mapped_column(ForeignKey("role.id"), nullable=True, index=True, default=None)
