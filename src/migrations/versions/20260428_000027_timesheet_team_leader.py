"""add team leader user to project timesheet records

Revision ID: 20260428_000027
Revises: 20260423_000026
Create Date: 2026-04-28 16:20:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260428_000027"
down_revision: str | None = "20260423_000026"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("project_timesheet_record", sa.Column("team_leader_user_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_project_timesheet_record_team_leader_user_id_user",
        "project_timesheet_record",
        "user",
        ["team_leader_user_id"],
        ["id"],
    )
    op.create_index(
        op.f("ix_project_timesheet_record_team_leader_user_id"),
        "project_timesheet_record",
        ["team_leader_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_project_timesheet_record_team_leader_user_id"), table_name="project_timesheet_record")
    op.drop_constraint(
        "fk_project_timesheet_record_team_leader_user_id_user",
        "project_timesheet_record",
        type_="foreignkey",
    )
    op.drop_column("project_timesheet_record", "team_leader_user_id")
