from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ...core.db.database import Base
from ...core.db.models import DataBackedSoftDeleteEntityMixin


class Asset(DataBackedSoftDeleteEntityMixin, Base):
    __tablename__ = "asset"

    type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    module: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    owner_type: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    owner_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
