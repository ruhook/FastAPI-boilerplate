"""add job progress domain

Revision ID: 20260408_000017
Revises: 20260408_000016
Create Date: 2026-04-08 21:20:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260408_000017"
down_revision: str | None = "20260408_000016"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "job_progress",
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("talent_profile_id", sa.Integer(), nullable=True),
        sa.Column("current_stage", sa.String(length=32), nullable=False),
        sa.Column("screening_mode", sa.String(length=16), nullable=False, server_default="manual"),
        sa.Column(
            "entered_stage_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(["application_id"], ["candidate_application.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["job.id"]),
        sa.ForeignKeyConstraint(["talent_profile_id"], ["talent_profile.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("application_id"),
    )
    op.create_index(op.f("ix_job_progress_application_id"), "job_progress", ["application_id"], unique=True)
    op.create_index(op.f("ix_job_progress_current_stage"), "job_progress", ["current_stage"], unique=False)
    op.create_index(op.f("ix_job_progress_entered_stage_at"), "job_progress", ["entered_stage_at"], unique=False)
    op.create_index(op.f("ix_job_progress_is_deleted"), "job_progress", ["is_deleted"], unique=False)
    op.create_index(op.f("ix_job_progress_job_id"), "job_progress", ["job_id"], unique=False)
    op.create_index(op.f("ix_job_progress_talent_profile_id"), "job_progress", ["talent_profile_id"], unique=False)
    op.create_index(op.f("ix_job_progress_user_id"), "job_progress", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_job_progress_user_id"), table_name="job_progress")
    op.drop_index(op.f("ix_job_progress_talent_profile_id"), table_name="job_progress")
    op.drop_index(op.f("ix_job_progress_job_id"), table_name="job_progress")
    op.drop_index(op.f("ix_job_progress_is_deleted"), table_name="job_progress")
    op.drop_index(op.f("ix_job_progress_entered_stage_at"), table_name="job_progress")
    op.drop_index(op.f("ix_job_progress_current_stage"), table_name="job_progress")
    op.drop_index(op.f("ix_job_progress_application_id"), table_name="job_progress")
    op.drop_table("job_progress")
