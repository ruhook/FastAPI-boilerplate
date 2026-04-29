"""create payment records

Revision ID: 20260429_000030
Revises: 20260429_000029
Create Date: 2026-04-29 16:30:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260429_000030"
down_revision: str | None = "20260429_000029"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
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
    op.create_index(op.f("ix_payment_record_user_id"), "payment_record", ["user_id"], unique=False)
    op.create_index(op.f("ix_payment_record_talent_profile_id"), "payment_record", ["talent_profile_id"], unique=False)
    op.create_index(
        op.f("ix_payment_record_contract_record_id"),
        "payment_record",
        ["contract_record_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_payment_record_referral_record_id"),
        "payment_record",
        ["referral_record_id"],
        unique=False,
    )
    op.create_index(op.f("ix_payment_record_payment_type"), "payment_record", ["payment_type"], unique=False)
    op.create_index(op.f("ix_payment_record_paid_at"), "payment_record", ["paid_at"], unique=False)
    op.create_index(
        op.f("ix_payment_record_external_transaction_no"),
        "payment_record",
        ["external_transaction_no"],
        unique=False,
    )
    op.create_index(
        op.f("ix_payment_record_user_snapshot_name"),
        "payment_record",
        ["user_snapshot_name"],
        unique=False,
    )
    op.create_index(
        op.f("ix_payment_record_user_snapshot_email"),
        "payment_record",
        ["user_snapshot_email"],
        unique=False,
    )
    op.create_index(op.f("ix_payment_record_company_id"), "payment_record", ["company_id"], unique=False)
    op.create_index(op.f("ix_payment_record_project_id"), "payment_record", ["project_id"], unique=False)
    op.create_index(
        op.f("ix_payment_record_company_snapshot_name"),
        "payment_record",
        ["company_snapshot_name"],
        unique=False,
    )
    op.create_index(
        op.f("ix_payment_record_project_snapshot_name"),
        "payment_record",
        ["project_snapshot_name"],
        unique=False,
    )
    op.create_index(
        op.f("ix_payment_record_contract_snapshot_ref_no"),
        "payment_record",
        ["contract_snapshot_ref_no"],
        unique=False,
    )
    op.create_index(
        op.f("ix_payment_record_referral_referred_user_id"),
        "payment_record",
        ["referral_referred_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_payment_record_referral_referred_snapshot_name"),
        "payment_record",
        ["referral_referred_snapshot_name"],
        unique=False,
    )
    op.create_index(
        op.f("ix_payment_record_referral_referred_snapshot_email"),
        "payment_record",
        ["referral_referred_snapshot_email"],
        unique=False,
    )
    op.create_index(
        op.f("ix_payment_record_created_by_admin_user_id"),
        "payment_record",
        ["created_by_admin_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_payment_record_updated_by_admin_user_id"),
        "payment_record",
        ["updated_by_admin_user_id"],
        unique=False,
    )
    op.create_index(op.f("ix_payment_record_is_deleted"), "payment_record", ["is_deleted"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_payment_record_is_deleted"), table_name="payment_record")
    op.drop_index(op.f("ix_payment_record_updated_by_admin_user_id"), table_name="payment_record")
    op.drop_index(op.f("ix_payment_record_created_by_admin_user_id"), table_name="payment_record")
    op.drop_index(op.f("ix_payment_record_referral_referred_snapshot_email"), table_name="payment_record")
    op.drop_index(op.f("ix_payment_record_referral_referred_snapshot_name"), table_name="payment_record")
    op.drop_index(op.f("ix_payment_record_referral_referred_user_id"), table_name="payment_record")
    op.drop_index(op.f("ix_payment_record_contract_snapshot_ref_no"), table_name="payment_record")
    op.drop_index(op.f("ix_payment_record_project_snapshot_name"), table_name="payment_record")
    op.drop_index(op.f("ix_payment_record_company_snapshot_name"), table_name="payment_record")
    op.drop_index(op.f("ix_payment_record_project_id"), table_name="payment_record")
    op.drop_index(op.f("ix_payment_record_company_id"), table_name="payment_record")
    op.drop_index(op.f("ix_payment_record_user_snapshot_email"), table_name="payment_record")
    op.drop_index(op.f("ix_payment_record_user_snapshot_name"), table_name="payment_record")
    op.drop_index(op.f("ix_payment_record_external_transaction_no"), table_name="payment_record")
    op.drop_index(op.f("ix_payment_record_paid_at"), table_name="payment_record")
    op.drop_index(op.f("ix_payment_record_payment_type"), table_name="payment_record")
    op.drop_index(op.f("ix_payment_record_referral_record_id"), table_name="payment_record")
    op.drop_index(op.f("ix_payment_record_contract_record_id"), table_name="payment_record")
    op.drop_index(op.f("ix_payment_record_talent_profile_id"), table_name="payment_record")
    op.drop_index(op.f("ix_payment_record_user_id"), table_name="payment_record")
    op.drop_table("payment_record")
