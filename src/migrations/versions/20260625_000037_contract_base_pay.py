"""add contract base pay

Revision ID: 20260625_000037
Revises: 20260614_000036
Create Date: 2026-06-25 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260625_000037"
down_revision: str | None = "20260614_000036"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("contract_record", sa.Column("base_pay", sa.Numeric(10, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("contract_record", "base_pay")
