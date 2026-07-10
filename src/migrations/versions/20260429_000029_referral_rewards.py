"""create referral reward records

Revision ID: 20260429_000029
Revises: 20260428_000028
Create Date: 2026-04-29 10:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260429_000029"
down_revision: str | None = "20260428_000028"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "referral_record",
        sa.Column("referrer_user_id", sa.Integer(), nullable=False),
        sa.Column("referred_user_id", sa.Integer(), nullable=False),
        sa.Column("referred_talent_profile_id", sa.Integer(), nullable=True),
        sa.Column("referrer_snapshot_name", sa.String(length=120), nullable=True),
        sa.Column("referrer_snapshot_email", sa.String(length=120), nullable=True),
        sa.Column("referred_snapshot_name", sa.String(length=120), nullable=True),
        sa.Column("referred_snapshot_email", sa.String(length=120), nullable=True),
        sa.Column("source_referral_code", sa.String(length=64), nullable=True),
        sa.Column(
            "paid_reward_amount", sa.Numeric(precision=10, scale=2), nullable=False, server_default=sa.text("0.00")
        ),
        sa.Column("payout_status", sa.String(length=32), nullable=False, server_default="tracking"),
        sa.Column("last_paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_paid_by_admin_user_id", sa.Integer(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=False),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(["referrer_user_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["referred_user_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["referred_talent_profile_id"], ["talent_profile.id"]),
        sa.ForeignKeyConstraint(["last_paid_by_admin_user_id"], ["admin_user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("referred_user_id"),
    )
    op.create_index(op.f("ix_referral_record_referrer_user_id"), "referral_record", ["referrer_user_id"], unique=False)
    op.create_index(op.f("ix_referral_record_referred_user_id"), "referral_record", ["referred_user_id"], unique=False)
    op.create_index(
        op.f("ix_referral_record_referred_talent_profile_id"),
        "referral_record",
        ["referred_talent_profile_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_referral_record_referrer_snapshot_name"), "referral_record", ["referrer_snapshot_name"], unique=False
    )
    op.create_index(
        op.f("ix_referral_record_referrer_snapshot_email"), "referral_record", ["referrer_snapshot_email"], unique=False
    )
    op.create_index(
        op.f("ix_referral_record_referred_snapshot_name"), "referral_record", ["referred_snapshot_name"], unique=False
    )
    op.create_index(
        op.f("ix_referral_record_referred_snapshot_email"), "referral_record", ["referred_snapshot_email"], unique=False
    )
    op.create_index(
        op.f("ix_referral_record_source_referral_code"), "referral_record", ["source_referral_code"], unique=False
    )
    op.create_index(op.f("ix_referral_record_payout_status"), "referral_record", ["payout_status"], unique=False)
    op.create_index(op.f("ix_referral_record_last_paid_at"), "referral_record", ["last_paid_at"], unique=False)
    op.create_index(
        op.f("ix_referral_record_last_paid_by_admin_user_id"),
        "referral_record",
        ["last_paid_by_admin_user_id"],
        unique=False,
    )
    op.create_index(op.f("ix_referral_record_is_deleted"), "referral_record", ["is_deleted"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_referral_record_is_deleted"), table_name="referral_record")
    op.drop_index(op.f("ix_referral_record_last_paid_by_admin_user_id"), table_name="referral_record")
    op.drop_index(op.f("ix_referral_record_last_paid_at"), table_name="referral_record")
    op.drop_index(op.f("ix_referral_record_payout_status"), table_name="referral_record")
    op.drop_index(op.f("ix_referral_record_source_referral_code"), table_name="referral_record")
    op.drop_index(op.f("ix_referral_record_referred_snapshot_email"), table_name="referral_record")
    op.drop_index(op.f("ix_referral_record_referred_snapshot_name"), table_name="referral_record")
    op.drop_index(op.f("ix_referral_record_referrer_snapshot_email"), table_name="referral_record")
    op.drop_index(op.f("ix_referral_record_referrer_snapshot_name"), table_name="referral_record")
    op.drop_index(op.f("ix_referral_record_referred_talent_profile_id"), table_name="referral_record")
    op.drop_index(op.f("ix_referral_record_referred_user_id"), table_name="referral_record")
    op.drop_index(op.f("ix_referral_record_referrer_user_id"), table_name="referral_record")
    op.drop_table("referral_record")
