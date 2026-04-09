"""add global operation log

Revision ID: 20260407_000014
Revises: 20260407_000013
Create Date: 2026-04-07 15:50:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260407_000014"
down_revision: str | None = "20260407_000013"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "operation_log",
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("log_type", sa.String(length=64), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_operation_log_user_id"), "operation_log", ["user_id"], unique=False)
    op.create_index(op.f("ix_operation_log_log_type"), "operation_log", ["log_type"], unique=False)
    op.create_index(op.f("ix_operation_log_created_at"), "operation_log", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_operation_log_created_at"), table_name="operation_log")
    op.drop_index(op.f("ix_operation_log_log_type"), table_name="operation_log")
    op.drop_index(op.f("ix_operation_log_user_id"), table_name="operation_log")
    op.drop_table("operation_log")
