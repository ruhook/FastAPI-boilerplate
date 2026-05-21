"""add candidate internal notification table

Revision ID: 20260518_000031
Revises: 20260429_000030
Create Date: 2026-05-18 16:20:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260518_000031"
down_revision: str | None = "20260429_000030"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "candidate_internal_notification",
        sa.Column("recipient_user_id", sa.Integer(), nullable=False),
        sa.Column("sender_admin_user_id", sa.Integer(), nullable=True),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("action_url", sa.String(length=500), nullable=True),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("current_timestamp(0)")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.text("current_timestamp(0)")),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(["recipient_user_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["sender_admin_user_id"], ["admin_user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_candidate_internal_notification_recipient_user_id"),
        "candidate_internal_notification",
        ["recipient_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_candidate_internal_notification_sender_admin_user_id"),
        "candidate_internal_notification",
        ["sender_admin_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_candidate_internal_notification_category"),
        "candidate_internal_notification",
        ["category"],
        unique=False,
    )
    op.create_index(
        op.f("ix_candidate_internal_notification_is_read"),
        "candidate_internal_notification",
        ["is_read"],
        unique=False,
    )
    op.create_index(
        op.f("ix_candidate_internal_notification_created_at"),
        "candidate_internal_notification",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_candidate_internal_notification_created_at"), table_name="candidate_internal_notification")
    op.drop_index(op.f("ix_candidate_internal_notification_is_read"), table_name="candidate_internal_notification")
    op.drop_index(op.f("ix_candidate_internal_notification_category"), table_name="candidate_internal_notification")
    op.drop_index(
        op.f("ix_candidate_internal_notification_sender_admin_user_id"),
        table_name="candidate_internal_notification",
    )
    op.drop_index(op.f("ix_candidate_internal_notification_recipient_user_id"), table_name="candidate_internal_notification")
    op.drop_table("candidate_internal_notification")
