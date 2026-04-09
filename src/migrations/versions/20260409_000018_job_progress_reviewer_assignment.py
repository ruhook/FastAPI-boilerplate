"""add reviewer assignment fields to job progress

Revision ID: 20260409_000018
Revises: 20260408_000017
Create Date: 2026-04-09 11:30:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260409_000018"
down_revision: str | None = "20260408_000017"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "job_progress",
        sa.Column("assessment_reviewer_admin_user_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "job_progress",
        sa.Column("assessment_assigned_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        op.f("ix_job_progress_assessment_reviewer_admin_user_id"),
        "job_progress",
        ["assessment_reviewer_admin_user_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_job_progress_assessment_reviewer_admin_user_id_admin_user",
        "job_progress",
        "admin_user",
        ["assessment_reviewer_admin_user_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_job_progress_assessment_reviewer_admin_user_id_admin_user",
        "job_progress",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_job_progress_assessment_reviewer_admin_user_id"), table_name="job_progress")
    op.drop_column("job_progress", "assessment_assigned_at")
    op.drop_column("job_progress", "assessment_reviewer_admin_user_id")
