"""add mail task processing lease

Revision ID: 20260710_000047
Revises: 20260710_000046
Create Date: 2026-07-10 23:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260710_000047"
down_revision: str | None = "20260710_000046"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "mail_task",
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "mail_task",
        sa.Column("processing_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_mail_task_recovery",
        "mail_task",
        ["status", "processing_lease_expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_mail_task_recovery", table_name="mail_task")
    op.drop_column("mail_task", "processing_lease_expires_at")
    op.drop_column("mail_task", "processing_started_at")
