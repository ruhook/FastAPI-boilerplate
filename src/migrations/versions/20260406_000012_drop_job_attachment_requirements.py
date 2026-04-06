"""drop job attachment requirement flags

Revision ID: 20260406_000012
Revises: 20260406_000011
Create Date: 2026-04-06 23:30:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260406_000012"
down_revision: str | None = "20260406_000011"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    if _has_column("job", "resume_required"):
        op.drop_column("job", "resume_required")
    if _has_column("job", "id_required"):
        op.drop_column("job", "id_required")


def downgrade() -> None:
    if not _has_column("job", "resume_required"):
        op.add_column(
            "job",
            sa.Column("resume_required", sa.Boolean(), nullable=False, server_default=sa.true()),
        )
    if not _has_column("job", "id_required"):
        op.add_column(
            "job",
            sa.Column("id_required", sa.Boolean(), nullable=False, server_default=sa.true()),
        )
