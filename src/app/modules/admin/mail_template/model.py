from sqlalchemy import ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ....core.db.database import Base
from ....core.db.models import DataBackedSoftDeleteEntityMixin


class MailTemplate(DataBackedSoftDeleteEntityMixin, Base):
    __tablename__ = "mail_template"

    admin_user_id: Mapped[int | None] = mapped_column(ForeignKey("admin_user.id"), nullable=True, index=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("mail_template_category.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    subject_template: Mapped[str] = mapped_column(String(500), nullable=False)
    body_html: Mapped[str] = mapped_column(Text, nullable=False)
    attachments: Mapped[list[dict[str, int]]] = mapped_column(JSON, nullable=False, default=list)
