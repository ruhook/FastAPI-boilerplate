"""move referral payout ownership to the settlement ledger

Revision ID: 20260711_000050
Revises: 20260711_000049
Create Date: 2026-07-11 12:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260711_000050"
down_revision: str | None = "20260711_000049"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _referral_admin_fk_name() -> str | None:
    inspector = sa.inspect(op.get_bind())
    for foreign_key in inspector.get_foreign_keys("referral_record"):
        if foreign_key.get("constrained_columns") == ["last_paid_by_admin_user_id"]:
            return foreign_key.get("name")
    return None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _referral_index_names() -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("referral_record")}


def _referral_column_names() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns("referral_record")}


def upgrade() -> None:
    if "payment_record" in _table_names():
        op.drop_table("payment_record")
    foreign_key_name = _referral_admin_fk_name()
    if foreign_key_name:
        op.drop_constraint(foreign_key_name, "referral_record", type_="foreignkey")
    index_names = _referral_index_names()
    for index_name in (
        "ix_referral_record_payout_status",
        "ix_referral_record_last_paid_at",
        "ix_referral_record_last_paid_by_admin_user_id",
    ):
        if index_name in index_names:
            op.drop_index(index_name, table_name="referral_record")
    column_names = _referral_column_names()
    for column_name in (
        "last_paid_by_admin_user_id",
        "last_paid_at",
        "payout_status",
        "paid_reward_amount",
    ):
        if column_name in column_names:
            op.drop_column("referral_record", column_name)


def downgrade() -> None:
    op.add_column(
        "referral_record",
        sa.Column(
            "paid_reward_amount",
            sa.Numeric(precision=10, scale=2),
            nullable=False,
            server_default=sa.text("0.00"),
        ),
    )
    op.add_column(
        "referral_record",
        sa.Column("payout_status", sa.String(length=32), nullable=False, server_default=sa.text("'tracking'")),
    )
    op.add_column("referral_record", sa.Column("last_paid_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("referral_record", sa.Column("last_paid_by_admin_user_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_referral_record_last_paid_by_admin_user_id",
        "referral_record",
        "admin_user",
        ["last_paid_by_admin_user_id"],
        ["id"],
    )
    op.create_index("ix_referral_record_payout_status", "referral_record", ["payout_status"])
    op.create_index("ix_referral_record_last_paid_at", "referral_record", ["last_paid_at"])
    op.create_index(
        "ix_referral_record_last_paid_by_admin_user_id",
        "referral_record",
        ["last_paid_by_admin_user_id"],
    )

    op.create_table(
        "payment_record",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("talent_profile_id", sa.Integer(), nullable=True),
        sa.Column("contract_record_id", sa.Integer(), nullable=True),
        sa.Column("referral_record_id", sa.Integer(), nullable=True),
        sa.Column("payment_type", sa.String(length=32), nullable=False),
        sa.Column("amount", sa.Numeric(precision=10, scale=2), nullable=False, server_default=sa.text("0.00")),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default=sa.text("'USD'")),
        sa.Column(
            "paid_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("current_timestamp(0)"),
        ),
        sa.Column("external_platform", sa.String(length=120), nullable=True),
        sa.Column("external_transaction_no", sa.String(length=160), nullable=True),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column("user_snapshot_name", sa.String(length=120), nullable=True),
        sa.Column("user_snapshot_email", sa.String(length=120), nullable=True),
        sa.Column("company_id", sa.Integer(), nullable=True),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("company_snapshot_name", sa.String(length=120), nullable=True),
        sa.Column("project_snapshot_name", sa.String(length=120), nullable=True),
        sa.Column("contract_snapshot_ref_no", sa.String(length=120), nullable=True),
        sa.Column("referral_referred_user_id", sa.Integer(), nullable=True),
        sa.Column("referral_referred_snapshot_name", sa.String(length=120), nullable=True),
        sa.Column("referral_referred_snapshot_email", sa.String(length=120), nullable=True),
        sa.Column("created_by_admin_user_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_admin_user_id", sa.Integer(), nullable=True),
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
        sa.ForeignKeyConstraint(["company_id"], ["admin_company.id"]),
        sa.ForeignKeyConstraint(["contract_record_id"], ["contract_record.id"]),
        sa.ForeignKeyConstraint(["created_by_admin_user_id"], ["admin_user.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["admin_company_project.id"]),
        sa.ForeignKeyConstraint(["referral_record_id"], ["referral_record.id"]),
        sa.ForeignKeyConstraint(["referral_referred_user_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["talent_profile_id"], ["talent_profile.id"]),
        sa.ForeignKeyConstraint(["updated_by_admin_user_id"], ["admin_user.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "user_id",
        "talent_profile_id",
        "contract_record_id",
        "referral_record_id",
        "payment_type",
        "paid_at",
        "external_transaction_no",
        "user_snapshot_name",
        "user_snapshot_email",
        "company_id",
        "project_id",
        "company_snapshot_name",
        "project_snapshot_name",
        "contract_snapshot_ref_no",
        "referral_referred_user_id",
        "referral_referred_snapshot_name",
        "referral_referred_snapshot_email",
        "created_by_admin_user_id",
        "updated_by_admin_user_id",
        "is_deleted",
    ):
        op.create_index(f"ix_payment_record_{column}", "payment_record", [column])
