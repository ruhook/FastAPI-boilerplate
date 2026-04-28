"""remove candidate application company snapshot

Revision ID: 20260419_000022
Revises: 20260419_000021
Create Date: 2026-04-19 23:55:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260419_000022"
down_revision: str | None = "20260419_000021"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("candidate_application", "job_snapshot_company_name")


def downgrade() -> None:
    op.add_column("candidate_application", sa.Column("job_snapshot_company_name", sa.String(length=100), nullable=True))
