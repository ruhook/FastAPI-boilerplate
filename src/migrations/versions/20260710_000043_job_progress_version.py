"""add optimistic job progress version

Revision ID: 20260710_000043
Revises: 20260710_000042
Create Date: 2026-07-10 19:15:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260710_000043"
down_revision: str | None = "20260710_000042"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "job_progress",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("job_progress", "version")
