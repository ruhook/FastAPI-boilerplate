"""add timesheet concurrency and idempotency guards

Revision ID: 20260711_000049
Revises: 20260711_000048
Create Date: 2026-07-11 11:15:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260711_000049"
down_revision: str | None = "20260711_000048"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "project_timesheet_record",
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )
    op.create_table(
        "project_timesheet_batch_request",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("current_timestamp(0)"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.text("current_timestamp(0)"),
        ),
        sa.Column("idempotency_key", sa.String(length=191), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("admin_user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("record_ids", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["admin_user_id"], ["admin_user.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["admin_company.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["admin_company_project.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_project_timesheet_batch_request_key"),
    )
    for column in ("company_id", "project_id", "admin_user_id"):
        op.create_index(
            f"ix_project_timesheet_batch_request_{column}",
            "project_timesheet_batch_request",
            [column],
        )


def downgrade() -> None:
    op.drop_table("project_timesheet_batch_request")
    op.drop_column("project_timesheet_record", "version")
