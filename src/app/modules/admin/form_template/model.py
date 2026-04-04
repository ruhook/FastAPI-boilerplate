from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from ....core.db.database import Base
from ....core.db.models import DataBackedSoftDeleteEntityMixin


class AdminFormTemplate(DataBackedSoftDeleteEntityMixin, Base):
    __tablename__ = "admin_form_template"

    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True, unique=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    fields: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False, default=list)

