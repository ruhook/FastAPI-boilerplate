"""create project timesheet record table

Revision ID: 20260423_000026
Revises: 20260423_000025
Create Date: 2026-04-23 23:15:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260423_000026"
down_revision: str | None = "20260423_000025"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_timesheet_record",
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("sub_project_name", sa.String(length=160), nullable=False),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("talent_profile_id", sa.Integer(), nullable=True),
        sa.Column("contract_record_id", sa.Integer(), nullable=True),
        sa.Column("user_name_snapshot", sa.String(length=120), nullable=True),
        sa.Column("user_email_snapshot", sa.String(length=120), nullable=True),
        sa.Column("language", sa.String(length=64), nullable=False),
        sa.Column("work_type", sa.String(length=64), nullable=False),
        sa.Column("output_quantity", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("human_efficiency_minutes", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("customer_duration_hours", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("candidate_duration_hours", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("role_name", sa.String(length=120), nullable=True),
        sa.Column("non_operational_duration_hours", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("project_link", sa.Text(), nullable=True),
        sa.Column("poc_evaluation", sa.Text(), nullable=True),
        sa.Column("extra_notes", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["project_id"], ["admin_company_project.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["talent_profile_id"], ["talent_profile.id"]),
        sa.ForeignKeyConstraint(["contract_record_id"], ["contract_record.id"]),
        sa.ForeignKeyConstraint(["created_by_admin_user_id"], ["admin_user.id"]),
        sa.ForeignKeyConstraint(["updated_by_admin_user_id"], ["admin_user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(op.f("ix_project_timesheet_record_company_id"), "project_timesheet_record", ["company_id"], unique=False)
    op.create_index(op.f("ix_project_timesheet_record_project_id"), "project_timesheet_record", ["project_id"], unique=False)
    op.create_index(op.f("ix_project_timesheet_record_sub_project_name"), "project_timesheet_record", ["sub_project_name"], unique=False)
    op.create_index(op.f("ix_project_timesheet_record_work_date"), "project_timesheet_record", ["work_date"], unique=False)
    op.create_index(op.f("ix_project_timesheet_record_user_id"), "project_timesheet_record", ["user_id"], unique=False)
    op.create_index(op.f("ix_project_timesheet_record_talent_profile_id"), "project_timesheet_record", ["talent_profile_id"], unique=False)
    op.create_index(op.f("ix_project_timesheet_record_contract_record_id"), "project_timesheet_record", ["contract_record_id"], unique=False)
    op.create_index(op.f("ix_project_timesheet_record_user_name_snapshot"), "project_timesheet_record", ["user_name_snapshot"], unique=False)
    op.create_index(op.f("ix_project_timesheet_record_user_email_snapshot"), "project_timesheet_record", ["user_email_snapshot"], unique=False)
    op.create_index(op.f("ix_project_timesheet_record_language"), "project_timesheet_record", ["language"], unique=False)
    op.create_index(op.f("ix_project_timesheet_record_work_type"), "project_timesheet_record", ["work_type"], unique=False)
    op.create_index(op.f("ix_project_timesheet_record_created_by_admin_user_id"), "project_timesheet_record", ["created_by_admin_user_id"], unique=False)
    op.create_index(op.f("ix_project_timesheet_record_updated_by_admin_user_id"), "project_timesheet_record", ["updated_by_admin_user_id"], unique=False)
    op.create_index(op.f("ix_project_timesheet_record_is_deleted"), "project_timesheet_record", ["is_deleted"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_project_timesheet_record_is_deleted"), table_name="project_timesheet_record")
    op.drop_index(op.f("ix_project_timesheet_record_updated_by_admin_user_id"), table_name="project_timesheet_record")
    op.drop_index(op.f("ix_project_timesheet_record_created_by_admin_user_id"), table_name="project_timesheet_record")
    op.drop_index(op.f("ix_project_timesheet_record_work_type"), table_name="project_timesheet_record")
    op.drop_index(op.f("ix_project_timesheet_record_language"), table_name="project_timesheet_record")
    op.drop_index(op.f("ix_project_timesheet_record_user_email_snapshot"), table_name="project_timesheet_record")
    op.drop_index(op.f("ix_project_timesheet_record_user_name_snapshot"), table_name="project_timesheet_record")
    op.drop_index(op.f("ix_project_timesheet_record_contract_record_id"), table_name="project_timesheet_record")
    op.drop_index(op.f("ix_project_timesheet_record_talent_profile_id"), table_name="project_timesheet_record")
    op.drop_index(op.f("ix_project_timesheet_record_user_id"), table_name="project_timesheet_record")
    op.drop_index(op.f("ix_project_timesheet_record_work_date"), table_name="project_timesheet_record")
    op.drop_index(op.f("ix_project_timesheet_record_sub_project_name"), table_name="project_timesheet_record")
    op.drop_index(op.f("ix_project_timesheet_record_project_id"), table_name="project_timesheet_record")
    op.drop_index(op.f("ix_project_timesheet_record_company_id"), table_name="project_timesheet_record")
    op.drop_table("project_timesheet_record")
