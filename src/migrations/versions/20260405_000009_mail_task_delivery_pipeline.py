"""add mail task delivery pipeline columns

Revision ID: 20260405_000009
Revises: 20260405_000008
Create Date: 2026-04-05 23:59:59.900000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260405_000009"
down_revision: str | None = "20260405_000008"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _column_exists(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if not _column_exists("mail_task", "final_subject"):
        op.add_column("mail_task", sa.Column("final_subject", sa.String(length=500), nullable=True))
    if not _column_exists("mail_task", "final_body_html"):
        op.add_column("mail_task", sa.Column("final_body_html", sa.Text(), nullable=True))
    if not _column_exists("mail_task", "provider_message_id"):
        op.add_column("mail_task", sa.Column("provider_message_id", sa.String(length=255), nullable=True))


def downgrade() -> None:
    if _column_exists("mail_task", "provider_message_id"):
        op.drop_column("mail_task", "provider_message_id")
    if _column_exists("mail_task", "final_body_html"):
        op.drop_column("mail_task", "final_body_html")
    if _column_exists("mail_task", "final_subject"):
        op.drop_column("mail_task", "final_subject")
