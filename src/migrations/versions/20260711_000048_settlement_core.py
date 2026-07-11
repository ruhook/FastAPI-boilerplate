"""add settlement core tables

Revision ID: 20260711_000048
Revises: 20260710_000047
Create Date: 2026-07-11 10:50:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260711_000048"
down_revision: str | None = "20260710_000047"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
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
    )


def upgrade() -> None:
    op.create_table(
        "payable",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        *_timestamps(),
        sa.Column("source_key", sa.String(length=191), nullable=False),
        sa.Column("payment_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("settlement_month", sa.String(length=7), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("talent_profile_id", sa.Integer(), nullable=True),
        sa.Column("contract_record_id", sa.Integer(), nullable=True),
        sa.Column("referral_record_id", sa.Integer(), nullable=True),
        sa.Column("company_id", sa.Integer(), nullable=True),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default=sa.text("'USD'")),
        sa.Column("calculation_snapshot", sa.JSON(), nullable=False),
        sa.Column("user_snapshot_name", sa.String(length=120), nullable=True),
        sa.Column("user_snapshot_email", sa.String(length=120), nullable=True),
        sa.Column("company_snapshot_name", sa.String(length=120), nullable=True),
        sa.Column("project_snapshot_name", sa.String(length=120), nullable=True),
        sa.Column("contract_snapshot_ref_no", sa.String(length=120), nullable=True),
        sa.Column("referral_referred_user_id", sa.Integer(), nullable=True),
        sa.Column("referral_referred_snapshot_name", sa.String(length=120), nullable=True),
        sa.Column("referral_referred_snapshot_email", sa.String(length=120), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_admin_user_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_admin_user_id", sa.Integer(), nullable=True),
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
        sa.UniqueConstraint("source_key", name="uq_payable_source_key"),
    )
    for column in (
        "payment_type",
        "status",
        "settlement_month",
        "user_id",
        "talent_profile_id",
        "contract_record_id",
        "referral_record_id",
        "company_id",
        "project_id",
        "user_snapshot_name",
        "user_snapshot_email",
        "company_snapshot_name",
        "project_snapshot_name",
        "contract_snapshot_ref_no",
        "referral_referred_user_id",
        "paid_at",
        "created_by_admin_user_id",
        "updated_by_admin_user_id",
    ):
        op.create_index(f"ix_payable_{column}", "payable", [column], unique=False)

    op.create_table(
        "payable_timesheet_source",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        *_timestamps(),
        sa.Column("payable_id", sa.Integer(), nullable=False),
        sa.Column("project_timesheet_record_id", sa.Integer(), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("work_hours_snapshot", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("amount_contribution_snapshot", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.ForeignKeyConstraint(["payable_id"], ["payable.id"]),
        sa.ForeignKeyConstraint(["project_timesheet_record_id"], ["project_timesheet_record.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "payable_id",
            "project_timesheet_record_id",
            name="uq_payable_timesheet_source",
        ),
    )
    op.create_index("ix_payable_timesheet_source_payable_id", "payable_timesheet_source", ["payable_id"])
    op.create_index(
        "ix_payable_timesheet_source_project_timesheet_record_id",
        "payable_timesheet_source",
        ["project_timesheet_record_id"],
    )

    op.create_table(
        "payment",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        *_timestamps(),
        sa.Column("payable_id", sa.Integer(), nullable=False),
        sa.Column("entry_type", sa.String(length=16), nullable=False, server_default=sa.text("'payment'")),
        sa.Column("reversal_of_payment_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("talent_profile_id", sa.Integer(), nullable=True),
        sa.Column("contract_record_id", sa.Integer(), nullable=True),
        sa.Column("referral_record_id", sa.Integer(), nullable=True),
        sa.Column("company_id", sa.Integer(), nullable=True),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("referral_referred_user_id", sa.Integer(), nullable=True),
        sa.Column("payment_type", sa.String(length=32), nullable=False),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default=sa.text("'USD'")),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("external_platform", sa.String(length=120), nullable=True),
        sa.Column("external_transaction_no", sa.String(length=160), nullable=True),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column("user_snapshot_name", sa.String(length=120), nullable=True),
        sa.Column("user_snapshot_email", sa.String(length=120), nullable=True),
        sa.Column("company_snapshot_name", sa.String(length=120), nullable=True),
        sa.Column("project_snapshot_name", sa.String(length=120), nullable=True),
        sa.Column("contract_snapshot_ref_no", sa.String(length=120), nullable=True),
        sa.Column("referral_referred_snapshot_name", sa.String(length=120), nullable=True),
        sa.Column("referral_referred_snapshot_email", sa.String(length=120), nullable=True),
        sa.Column("created_by_admin_user_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["admin_company.id"]),
        sa.ForeignKeyConstraint(["contract_record_id"], ["contract_record.id"]),
        sa.ForeignKeyConstraint(["created_by_admin_user_id"], ["admin_user.id"]),
        sa.ForeignKeyConstraint(["payable_id"], ["payable.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["admin_company_project.id"]),
        sa.ForeignKeyConstraint(["referral_record_id"], ["referral_record.id"]),
        sa.ForeignKeyConstraint(["referral_referred_user_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["reversal_of_payment_id"], ["payment.id"]),
        sa.ForeignKeyConstraint(["talent_profile_id"], ["talent_profile.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("payable_id", "entry_type", name="uq_payment_payable_entry_type"),
        sa.UniqueConstraint("reversal_of_payment_id", name="uq_payment_reversal_of"),
    )
    for column in (
        "payable_id",
        "entry_type",
        "user_id",
        "talent_profile_id",
        "contract_record_id",
        "referral_record_id",
        "company_id",
        "project_id",
        "referral_referred_user_id",
        "payment_type",
        "paid_at",
        "external_transaction_no",
        "user_snapshot_name",
        "user_snapshot_email",
        "company_snapshot_name",
        "project_snapshot_name",
        "contract_snapshot_ref_no",
        "created_by_admin_user_id",
    ):
        op.create_index(f"ix_payment_{column}", "payment", [column], unique=False)


def downgrade() -> None:
    op.drop_table("payment")
    op.drop_table("payable_timesheet_source")
    op.drop_table("payable")
