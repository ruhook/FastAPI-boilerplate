"""add talent domain

Revision ID: 20260407_000013
Revises: 20260406_000012
Create Date: 2026-04-07 12:30:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260407_000013"
down_revision: str | None = "20260406_000012"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "candidate_application",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("form_template_id", sa.Integer(), nullable=True),
        sa.Column("job_snapshot_title", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(["form_template_id"], ["admin_form_template.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["job.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_candidate_application_user_id"), "candidate_application", ["user_id"], unique=False)
    op.create_index(op.f("ix_candidate_application_job_id"), "candidate_application", ["job_id"], unique=False)
    op.create_index(op.f("ix_candidate_application_form_template_id"), "candidate_application", ["form_template_id"], unique=False)
    op.create_index(op.f("ix_candidate_application_status"), "candidate_application", ["status"], unique=False)
    op.create_index(op.f("ix_candidate_application_submitted_at"), "candidate_application", ["submitted_at"], unique=False)

    op.create_table(
        "talent_profile",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("full_name", sa.String(length=120), nullable=True),
        sa.Column("email", sa.String(length=120), nullable=True),
        sa.Column("whatsapp", sa.String(length=64), nullable=True),
        sa.Column("nationality", sa.String(length=120), nullable=True),
        sa.Column("location", sa.String(length=120), nullable=True),
        sa.Column("education", sa.String(length=160), nullable=True),
        sa.Column("resume_asset_id", sa.Integer(), nullable=True),
        sa.Column("latest_applied_job_id", sa.Integer(), nullable=True),
        sa.Column("latest_applied_job_title", sa.String(length=160), nullable=True),
        sa.Column("latest_applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("source_application_id", sa.Integer(), nullable=True),
        sa.Column("merge_strategy", sa.String(length=32), nullable=True),
        sa.Column("last_merged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(["latest_applied_job_id"], ["job.id"]),
        sa.ForeignKeyConstraint(["resume_asset_id"], ["asset.id"]),
        sa.ForeignKeyConstraint(["source_application_id"], ["candidate_application.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index(op.f("ix_talent_profile_user_id"), "talent_profile", ["user_id"], unique=True)
    op.create_index(op.f("ix_talent_profile_full_name"), "talent_profile", ["full_name"], unique=False)
    op.create_index(op.f("ix_talent_profile_email"), "talent_profile", ["email"], unique=False)
    op.create_index(op.f("ix_talent_profile_whatsapp"), "talent_profile", ["whatsapp"], unique=False)
    op.create_index(op.f("ix_talent_profile_nationality"), "talent_profile", ["nationality"], unique=False)
    op.create_index(op.f("ix_talent_profile_location"), "talent_profile", ["location"], unique=False)
    op.create_index(op.f("ix_talent_profile_resume_asset_id"), "talent_profile", ["resume_asset_id"], unique=False)
    op.create_index(op.f("ix_talent_profile_latest_applied_job_id"), "talent_profile", ["latest_applied_job_id"], unique=False)
    op.create_index(op.f("ix_talent_profile_latest_applied_at"), "talent_profile", ["latest_applied_at"], unique=False)
    op.create_index(op.f("ix_talent_profile_source_application_id"), "talent_profile", ["source_application_id"], unique=False)
    op.create_index(op.f("ix_talent_profile_merge_strategy"), "talent_profile", ["merge_strategy"], unique=False)

    op.create_table(
        "candidate_application_field_value",
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("field_key", sa.String(length=100), nullable=False),
        sa.Column("field_label", sa.String(length=255), nullable=False),
        sa.Column("field_type", sa.String(length=50), nullable=False),
        sa.Column("catalog_key", sa.String(length=100), nullable=True),
        sa.Column("raw_value", sa.Text(), nullable=True),
        sa.Column("display_value", sa.Text(), nullable=True),
        sa.Column("asset_id", sa.Integer(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["application_id"], ["candidate_application.id"]),
        sa.ForeignKeyConstraint(["asset_id"], ["asset.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_candidate_application_field_value_application_id"),
        "candidate_application_field_value",
        ["application_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_candidate_application_field_value_field_key"),
        "candidate_application_field_value",
        ["field_key"],
        unique=False,
    )
    op.create_index(
        op.f("ix_candidate_application_field_value_catalog_key"),
        "candidate_application_field_value",
        ["catalog_key"],
        unique=False,
    )
    op.create_index(
        op.f("ix_candidate_application_field_value_asset_id"),
        "candidate_application_field_value",
        ["asset_id"],
        unique=False,
    )

    op.create_table(
        "talent_profile_merge_log",
        sa.Column("talent_profile_id", sa.Integer(), nullable=False),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("operator_admin_user_id", sa.Integer(), nullable=True),
        sa.Column("merge_strategy", sa.String(length=32), nullable=False),
        sa.Column("merged_fields", sa.JSON(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["application_id"], ["candidate_application.id"]),
        sa.ForeignKeyConstraint(["operator_admin_user_id"], ["admin_user.id"]),
        sa.ForeignKeyConstraint(["talent_profile_id"], ["talent_profile.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_talent_profile_merge_log_talent_profile_id"), "talent_profile_merge_log", ["talent_profile_id"], unique=False)
    op.create_index(op.f("ix_talent_profile_merge_log_application_id"), "talent_profile_merge_log", ["application_id"], unique=False)
    op.create_index(op.f("ix_talent_profile_merge_log_operator_admin_user_id"), "talent_profile_merge_log", ["operator_admin_user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_talent_profile_merge_log_operator_admin_user_id"), table_name="talent_profile_merge_log")
    op.drop_index(op.f("ix_talent_profile_merge_log_application_id"), table_name="talent_profile_merge_log")
    op.drop_index(op.f("ix_talent_profile_merge_log_talent_profile_id"), table_name="talent_profile_merge_log")
    op.drop_table("talent_profile_merge_log")

    op.drop_index(op.f("ix_candidate_application_field_value_asset_id"), table_name="candidate_application_field_value")
    op.drop_index(op.f("ix_candidate_application_field_value_catalog_key"), table_name="candidate_application_field_value")
    op.drop_index(op.f("ix_candidate_application_field_value_field_key"), table_name="candidate_application_field_value")
    op.drop_index(op.f("ix_candidate_application_field_value_application_id"), table_name="candidate_application_field_value")
    op.drop_table("candidate_application_field_value")

    op.drop_index(op.f("ix_talent_profile_merge_strategy"), table_name="talent_profile")
    op.drop_index(op.f("ix_talent_profile_source_application_id"), table_name="talent_profile")
    op.drop_index(op.f("ix_talent_profile_latest_applied_at"), table_name="talent_profile")
    op.drop_index(op.f("ix_talent_profile_latest_applied_job_id"), table_name="talent_profile")
    op.drop_index(op.f("ix_talent_profile_resume_asset_id"), table_name="talent_profile")
    op.drop_index(op.f("ix_talent_profile_location"), table_name="talent_profile")
    op.drop_index(op.f("ix_talent_profile_nationality"), table_name="talent_profile")
    op.drop_index(op.f("ix_talent_profile_whatsapp"), table_name="talent_profile")
    op.drop_index(op.f("ix_talent_profile_email"), table_name="talent_profile")
    op.drop_index(op.f("ix_talent_profile_full_name"), table_name="talent_profile")
    op.drop_index(op.f("ix_talent_profile_user_id"), table_name="talent_profile")
    op.drop_table("talent_profile")

    op.drop_index(op.f("ix_candidate_application_submitted_at"), table_name="candidate_application")
    op.drop_index(op.f("ix_candidate_application_status"), table_name="candidate_application")
    op.drop_index(op.f("ix_candidate_application_form_template_id"), table_name="candidate_application")
    op.drop_index(op.f("ix_candidate_application_job_id"), table_name="candidate_application")
    op.drop_index(op.f("ix_candidate_application_user_id"), table_name="candidate_application")
    op.drop_table("candidate_application")
