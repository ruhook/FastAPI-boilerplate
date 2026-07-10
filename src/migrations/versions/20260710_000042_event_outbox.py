"""add transactional event outbox

Revision ID: 20260710_000042
Revises: 20260710_000041
Create Date: 2026-07-10 18:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260710_000042"
down_revision: str | None = "20260710_000041"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "event_outbox",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="8"),
        sa.Column("lease_owner", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_event_outbox_event_id"),
    )
    op.create_index(
        "ix_event_outbox_dispatch",
        "event_outbox",
        ["status", "available_at", "lease_expires_at"],
        unique=False,
    )
    op.create_index(op.f("ix_event_outbox_event_id"), "event_outbox", ["event_id"], unique=True)
    op.create_index(op.f("ix_event_outbox_event_type"), "event_outbox", ["event_type"], unique=False)
    op.create_index(op.f("ix_event_outbox_status"), "event_outbox", ["status"], unique=False)


def downgrade() -> None:
    op.drop_table("event_outbox")
