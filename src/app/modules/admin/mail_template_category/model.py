from sqlalchemy import Boolean, ForeignKey, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from ....core.db.database import Base
from ....core.db.models import DataBackedSoftDeleteEntityMixin


class MailTemplateCategory(DataBackedSoftDeleteEntityMixin, Base):
    __tablename__ = "mail_template_category"

    admin_user_id: Mapped[int | None] = mapped_column(ForeignKey("admin_user.id"), nullable=True, index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("mail_template_category.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("1"))
