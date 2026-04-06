from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from ....core.db.database import Base
from ....core.db.models import DataBackedSoftDeleteEntityMixin


class AdminDictionary(DataBackedSoftDeleteEntityMixin, Base):
    __tablename__ = "admin_dictionary"

    key: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True, unique=True)
    label: Mapped[str] = mapped_column(String(100), nullable=False, index=True, unique=True)
    options: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False, default=list)
