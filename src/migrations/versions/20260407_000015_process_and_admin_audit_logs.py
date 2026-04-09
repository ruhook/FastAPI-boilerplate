"""split process log and admin audit log

Revision ID: 20260407_000015
Revises: 20260407_000014
Create Date: 2026-04-07 22:30:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260407_000015"
down_revision: str | None = "20260407_000014"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("operation_log", sa.Column("job_id", sa.Integer(), nullable=True))
    op.add_column("operation_log", sa.Column("application_id", sa.Integer(), nullable=True))
    op.add_column("operation_log", sa.Column("talent_profile_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_operation_log_job_id", "operation_log", "job", ["job_id"], ["id"])
    op.create_foreign_key(
        "fk_operation_log_application_id",
        "operation_log",
        "candidate_application",
        ["application_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_operation_log_talent_profile_id",
        "operation_log",
        "talent_profile",
        ["talent_profile_id"],
        ["id"],
    )
    op.create_index(op.f("ix_operation_log_job_id"), "operation_log", ["job_id"], unique=False)
    op.create_index(op.f("ix_operation_log_application_id"), "operation_log", ["application_id"], unique=False)
    op.create_index(op.f("ix_operation_log_talent_profile_id"), "operation_log", ["talent_profile_id"], unique=False)

    op.create_table(
        "admin_audit_log",
        sa.Column("admin_user_id", sa.Integer(), nullable=False),
        sa.Column("action_type", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=True),
        sa.Column("target_id", sa.Integer(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(["admin_user_id"], ["admin_user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_admin_audit_log_admin_user_id"), "admin_audit_log", ["admin_user_id"], unique=False)
    op.create_index(op.f("ix_admin_audit_log_action_type"), "admin_audit_log", ["action_type"], unique=False)
    op.create_index(op.f("ix_admin_audit_log_target_type"), "admin_audit_log", ["target_type"], unique=False)
    op.create_index(op.f("ix_admin_audit_log_target_id"), "admin_audit_log", ["target_id"], unique=False)
    op.create_index(op.f("ix_admin_audit_log_created_at"), "admin_audit_log", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_admin_audit_log_created_at"), table_name="admin_audit_log")
    op.drop_index(op.f("ix_admin_audit_log_target_id"), table_name="admin_audit_log")
    op.drop_index(op.f("ix_admin_audit_log_target_type"), table_name="admin_audit_log")
    op.drop_index(op.f("ix_admin_audit_log_action_type"), table_name="admin_audit_log")
    op.drop_index(op.f("ix_admin_audit_log_admin_user_id"), table_name="admin_audit_log")
    op.drop_table("admin_audit_log")

    op.drop_index(op.f("ix_operation_log_talent_profile_id"), table_name="operation_log")
    op.drop_index(op.f("ix_operation_log_application_id"), table_name="operation_log")
    op.drop_index(op.f("ix_operation_log_job_id"), table_name="operation_log")
    op.drop_constraint("fk_operation_log_talent_profile_id", "operation_log", type_="foreignkey")
    op.drop_constraint("fk_operation_log_application_id", "operation_log", type_="foreignkey")
    op.drop_constraint("fk_operation_log_job_id", "operation_log", type_="foreignkey")
    op.drop_column("operation_log", "talent_profile_id")
    op.drop_column("operation_log", "application_id")
    op.drop_column("operation_log", "job_id")
