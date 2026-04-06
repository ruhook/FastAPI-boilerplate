"""add job domain

Revision ID: 20260406_000011
Revises: 20260406_000010
Create Date: 2026-04-06 21:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260406_000011"
down_revision: str | None = "20260406_000010"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "job",
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("company_name", sa.String(length=100), nullable=False),
        sa.Column("country", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("work_mode", sa.String(length=20), nullable=False),
        sa.Column("compensation_min", sa.Numeric(10, 2), nullable=True),
        sa.Column("compensation_max", sa.Numeric(10, 2), nullable=True),
        sa.Column("compensation_unit", sa.String(length=20), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("applicant_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("owner_admin_user_id", sa.Integer(), nullable=False),
        sa.Column("form_template_id", sa.Integer(), nullable=False),
        sa.Column("assessment_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("assessment_mail_account_id", sa.Integer(), nullable=True),
        sa.Column("assessment_mail_template_id", sa.Integer(), nullable=True),
        sa.Column("assessment_mail_signature_id", sa.Integer(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(["owner_admin_user_id"], ["admin_user.id"]),
        sa.ForeignKeyConstraint(["form_template_id"], ["admin_form_template.id"]),
        sa.ForeignKeyConstraint(["assessment_mail_account_id"], ["mail_account.id"]),
        sa.ForeignKeyConstraint(["assessment_mail_template_id"], ["mail_template.id"]),
        sa.ForeignKeyConstraint(["assessment_mail_signature_id"], ["mail_signature.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_job_title"), "job", ["title"], unique=False)
    op.create_index(op.f("ix_job_company_name"), "job", ["company_name"], unique=False)
    op.create_index(op.f("ix_job_country"), "job", ["country"], unique=False)
    op.create_index(op.f("ix_job_status"), "job", ["status"], unique=False)
    op.create_index(op.f("ix_job_work_mode"), "job", ["work_mode"], unique=False)
    op.create_index(op.f("ix_job_owner_admin_user_id"), "job", ["owner_admin_user_id"], unique=False)
    op.create_index(op.f("ix_job_form_template_id"), "job", ["form_template_id"], unique=False)
    op.create_index(op.f("ix_job_assessment_mail_account_id"), "job", ["assessment_mail_account_id"], unique=False)
    op.create_index(op.f("ix_job_assessment_mail_template_id"), "job", ["assessment_mail_template_id"], unique=False)
    op.create_index(op.f("ix_job_assessment_mail_signature_id"), "job", ["assessment_mail_signature_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_job_assessment_mail_signature_id"), table_name="job")
    op.drop_index(op.f("ix_job_assessment_mail_template_id"), table_name="job")
    op.drop_index(op.f("ix_job_assessment_mail_account_id"), table_name="job")
    op.drop_index(op.f("ix_job_form_template_id"), table_name="job")
    op.drop_index(op.f("ix_job_owner_admin_user_id"), table_name="job")
    op.drop_index(op.f("ix_job_work_mode"), table_name="job")
    op.drop_index(op.f("ix_job_status"), table_name="job")
    op.drop_index(op.f("ix_job_country"), table_name="job")
    op.drop_index(op.f("ix_job_company_name"), table_name="job")
    op.drop_index(op.f("ix_job_title"), table_name="job")
    op.drop_table("job")
