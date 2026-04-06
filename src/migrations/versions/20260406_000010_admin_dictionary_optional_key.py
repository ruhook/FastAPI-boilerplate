"""add optional key to admin dictionary

Revision ID: 20260406_000010
Revises: 20260405_000009
Create Date: 2026-04-06 19:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260406_000010"
down_revision: str | None = "20260405_000009"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("admin_dictionary", sa.Column("key", sa.String(length=100), nullable=True))
    op.create_index(op.f("ix_admin_dictionary_key"), "admin_dictionary", ["key"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_admin_dictionary_key"), table_name="admin_dictionary")
    op.drop_column("admin_dictionary", "key")
