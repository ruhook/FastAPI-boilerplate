"""add project manager to project timesheet records

Revision ID: 20260628_000039
Revises: 20260627_000038
Create Date: 2026-06-28 18:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260628_000039"
down_revision: str | None = "20260627_000038"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _column_exists(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def _index_exists(table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return index_name in {index["name"] for index in inspector.get_indexes(table_name)}


def _foreign_key_exists(table_name: str, constraint_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return constraint_name in {item["name"] for item in inspector.get_foreign_keys(table_name) if item.get("name")}


def upgrade() -> None:
    if not _column_exists("project_timesheet_record", "project_manager_admin_user_id"):
        op.add_column(
            "project_timesheet_record",
            sa.Column("project_manager_admin_user_id", sa.Integer(), nullable=True),
        )
    if not _column_exists("project_timesheet_record", "project_manager_name_snapshot"):
        op.add_column(
            "project_timesheet_record",
            sa.Column("project_manager_name_snapshot", sa.String(length=120), nullable=True),
        )
    if not _foreign_key_exists("project_timesheet_record", "fk_pt_record_pm_admin_user"):
        op.create_foreign_key(
            "fk_pt_record_pm_admin_user",
            "project_timesheet_record",
            "admin_user",
            ["project_manager_admin_user_id"],
            ["id"],
        )
    if not _index_exists("project_timesheet_record", op.f("ix_project_timesheet_record_project_manager_admin_user_id")):
        op.create_index(
            op.f("ix_project_timesheet_record_project_manager_admin_user_id"),
            "project_timesheet_record",
            ["project_manager_admin_user_id"],
            unique=False,
        )
    if not _index_exists("project_timesheet_record", op.f("ix_project_timesheet_record_project_manager_name_snapshot")):
        op.create_index(
            op.f("ix_project_timesheet_record_project_manager_name_snapshot"),
            "project_timesheet_record",
            ["project_manager_name_snapshot"],
            unique=False,
        )


def downgrade() -> None:
    if _index_exists("project_timesheet_record", op.f("ix_project_timesheet_record_project_manager_name_snapshot")):
        op.drop_index(
            op.f("ix_project_timesheet_record_project_manager_name_snapshot"),
            table_name="project_timesheet_record",
        )
    if _index_exists("project_timesheet_record", op.f("ix_project_timesheet_record_project_manager_admin_user_id")):
        op.drop_index(
            op.f("ix_project_timesheet_record_project_manager_admin_user_id"),
            table_name="project_timesheet_record",
        )
    if _foreign_key_exists("project_timesheet_record", "fk_pt_record_pm_admin_user"):
        op.drop_constraint(
            "fk_pt_record_pm_admin_user",
            "project_timesheet_record",
            type_="foreignkey",
        )
    if _column_exists("project_timesheet_record", "project_manager_name_snapshot"):
        op.drop_column("project_timesheet_record", "project_manager_name_snapshot")
    if _column_exists("project_timesheet_record", "project_manager_admin_user_id"):
        op.drop_column("project_timesheet_record", "project_manager_admin_user_id")
