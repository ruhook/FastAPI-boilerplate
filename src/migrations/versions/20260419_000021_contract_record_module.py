"""add contract record module

Revision ID: 20260419_000021
Revises: 20260419_000020
Create Date: 2026-04-19 22:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260419_000021"
down_revision: str | None = "20260419_000020"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "contract_record",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("user_snapshot_name", sa.String(length=120), nullable=True),
        sa.Column("user_snapshot_email", sa.String(length=120), nullable=True),
        sa.Column("talent_profile_id", sa.Integer(), nullable=True),
        sa.Column("application_id", sa.Integer(), nullable=True),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("job_progress_id", sa.Integer(), nullable=False),
        sa.Column("job_snapshot_title", sa.String(length=160), nullable=True),
        sa.Column("service_customer_company_id", sa.Integer(), nullable=True),
        sa.Column("service_customer_company_name", sa.String(length=120), nullable=True),
        sa.Column("agreement_ref_no", sa.String(length=120), nullable=True),
        sa.Column("contract_status", sa.String(length=32), nullable=False, server_default=sa.text("'draft_uploaded'")),
        sa.Column("contractor_name", sa.String(length=120), nullable=True),
        sa.Column("rate", sa.Numeric(10, 2), nullable=True),
        sa.Column(
            "legal_entity", sa.String(length=120), nullable=False, server_default=sa.text("'T-Maxx International'")
        ),
        sa.Column("worker_type", sa.String(length=64), nullable=False, server_default=sa.text("'Contractor'")),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("draft_contract_asset_id", sa.Integer(), nullable=True),
        sa.Column("candidate_signed_contract_asset_id", sa.Integer(), nullable=True),
        sa.Column("company_sealed_contract_asset_id", sa.Integer(), nullable=True),
        sa.Column("contract_attachment_asset_id", sa.Integer(), nullable=True),
        sa.Column("parse_status", sa.String(length=32), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("parse_error", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_by_admin_user_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_admin_user_id", sa.Integer(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("current_timestamp(0)")
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.text("current_timestamp(0)")
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(["application_id"], ["candidate_application.id"]),
        sa.ForeignKeyConstraint(["candidate_signed_contract_asset_id"], ["asset.id"]),
        sa.ForeignKeyConstraint(["company_sealed_contract_asset_id"], ["asset.id"]),
        sa.ForeignKeyConstraint(["contract_attachment_asset_id"], ["asset.id"]),
        sa.ForeignKeyConstraint(["created_by_admin_user_id"], ["admin_user.id"]),
        sa.ForeignKeyConstraint(["draft_contract_asset_id"], ["asset.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["job.id"]),
        sa.ForeignKeyConstraint(["job_progress_id"], ["job_progress.id"]),
        sa.ForeignKeyConstraint(["service_customer_company_id"], ["admin_company.id"]),
        sa.ForeignKeyConstraint(["talent_profile_id"], ["talent_profile.id"]),
        sa.ForeignKeyConstraint(["updated_by_admin_user_id"], ["admin_user.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_contract_record_user_id"), "contract_record", ["user_id"], unique=False)
    op.create_index(
        op.f("ix_contract_record_user_snapshot_name"), "contract_record", ["user_snapshot_name"], unique=False
    )
    op.create_index(
        op.f("ix_contract_record_user_snapshot_email"), "contract_record", ["user_snapshot_email"], unique=False
    )
    op.create_index(
        op.f("ix_contract_record_talent_profile_id"), "contract_record", ["talent_profile_id"], unique=False
    )
    op.create_index(op.f("ix_contract_record_application_id"), "contract_record", ["application_id"], unique=False)
    op.create_index(op.f("ix_contract_record_job_id"), "contract_record", ["job_id"], unique=False)
    op.create_index(op.f("ix_contract_record_job_progress_id"), "contract_record", ["job_progress_id"], unique=False)
    op.create_index(
        op.f("ix_contract_record_job_snapshot_title"), "contract_record", ["job_snapshot_title"], unique=False
    )
    op.create_index(
        op.f("ix_contract_record_service_customer_company_id"),
        "contract_record",
        ["service_customer_company_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_contract_record_service_customer_company_name"),
        "contract_record",
        ["service_customer_company_name"],
        unique=False,
    )
    op.create_index(op.f("ix_contract_record_agreement_ref_no"), "contract_record", ["agreement_ref_no"], unique=False)
    op.create_index(op.f("ix_contract_record_contract_status"), "contract_record", ["contract_status"], unique=False)
    op.create_index(op.f("ix_contract_record_contractor_name"), "contract_record", ["contractor_name"], unique=False)
    op.create_index(
        op.f("ix_contract_record_draft_contract_asset_id"), "contract_record", ["draft_contract_asset_id"], unique=False
    )
    op.create_index(
        op.f("ix_contract_record_candidate_signed_contract_asset_id"),
        "contract_record",
        ["candidate_signed_contract_asset_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_contract_record_company_sealed_contract_asset_id"),
        "contract_record",
        ["company_sealed_contract_asset_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_contract_record_contract_attachment_asset_id"),
        "contract_record",
        ["contract_attachment_asset_id"],
        unique=False,
    )
    op.create_index(op.f("ix_contract_record_parse_status"), "contract_record", ["parse_status"], unique=False)
    op.create_index(op.f("ix_contract_record_is_current"), "contract_record", ["is_current"], unique=False)
    op.create_index(
        op.f("ix_contract_record_created_by_admin_user_id"),
        "contract_record",
        ["created_by_admin_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_contract_record_updated_by_admin_user_id"),
        "contract_record",
        ["updated_by_admin_user_id"],
        unique=False,
    )
    op.create_index(op.f("ix_contract_record_is_deleted"), "contract_record", ["is_deleted"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_contract_record_is_deleted"), table_name="contract_record")
    op.drop_index(op.f("ix_contract_record_updated_by_admin_user_id"), table_name="contract_record")
    op.drop_index(op.f("ix_contract_record_created_by_admin_user_id"), table_name="contract_record")
    op.drop_index(op.f("ix_contract_record_is_current"), table_name="contract_record")
    op.drop_index(op.f("ix_contract_record_parse_status"), table_name="contract_record")
    op.drop_index(op.f("ix_contract_record_contract_attachment_asset_id"), table_name="contract_record")
    op.drop_index(op.f("ix_contract_record_company_sealed_contract_asset_id"), table_name="contract_record")
    op.drop_index(op.f("ix_contract_record_candidate_signed_contract_asset_id"), table_name="contract_record")
    op.drop_index(op.f("ix_contract_record_draft_contract_asset_id"), table_name="contract_record")
    op.drop_index(op.f("ix_contract_record_contractor_name"), table_name="contract_record")
    op.drop_index(op.f("ix_contract_record_contract_status"), table_name="contract_record")
    op.drop_index(op.f("ix_contract_record_agreement_ref_no"), table_name="contract_record")
    op.drop_index(op.f("ix_contract_record_service_customer_company_name"), table_name="contract_record")
    op.drop_index(op.f("ix_contract_record_service_customer_company_id"), table_name="contract_record")
    op.drop_index(op.f("ix_contract_record_job_snapshot_title"), table_name="contract_record")
    op.drop_index(op.f("ix_contract_record_job_progress_id"), table_name="contract_record")
    op.drop_index(op.f("ix_contract_record_job_id"), table_name="contract_record")
    op.drop_index(op.f("ix_contract_record_application_id"), table_name="contract_record")
    op.drop_index(op.f("ix_contract_record_talent_profile_id"), table_name="contract_record")
    op.drop_index(op.f("ix_contract_record_user_snapshot_email"), table_name="contract_record")
    op.drop_index(op.f("ix_contract_record_user_snapshot_name"), table_name="contract_record")
    op.drop_index(op.f("ix_contract_record_user_id"), table_name="contract_record")
    op.drop_table("contract_record")
