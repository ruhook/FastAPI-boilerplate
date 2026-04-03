from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from ....core.db.database import Base
from ....core.db.models import DataBackedEntityMixin


class Role(DataBackedEntityMixin, Base):
    __tablename__ = "role"

    name: Mapped[str] = mapped_column(String(50), nullable=False, index=True, unique=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    permissions: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
