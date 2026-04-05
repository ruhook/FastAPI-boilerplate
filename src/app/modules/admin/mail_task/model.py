from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from ....core.db.database import Base
from ....core.db.models import DataBackedEntityMixin


class MailTask(DataBackedEntityMixin, Base):
    __tablename__ = "mail_task"

    account_id: Mapped[int] = mapped_column(ForeignKey("mail_account.id"), nullable=False, index=True)
    template_id: Mapped[int | None] = mapped_column(ForeignKey("mail_template.id"), nullable=True, index=True)
    signature_id: Mapped[int | None] = mapped_column(ForeignKey("mail_signature.id"), nullable=True, index=True)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    body_html: Mapped[str] = mapped_column(Text, nullable=False)
    final_subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    final_body_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    to_recipients: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False, default=list)
    cc_recipients: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False, default=list)
    bcc_recipients: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False, default=list)
    attachment_asset_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'pending'"), index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
