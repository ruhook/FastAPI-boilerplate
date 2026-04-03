"""initial schema

Revision ID: 20260403_000001
Revises:
Create Date: 2026-04-03 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260403_000001"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "role",
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("permissions", sa.JSON(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_role_name"), "role", ["name"], unique=True)

    op.create_table(
        "user",
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("name", sa.String(length=30), nullable=False),
        sa.Column("username", sa.String(length=20), nullable=False),
        sa.Column("email", sa.String(length=50), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("profile_image_url", sa.String(length=255), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_user_email"), "user", ["email"], unique=True)
    op.create_index(op.f("ix_user_is_deleted"), "user", ["is_deleted"], unique=False)
    op.create_index(op.f("ix_user_username"), "user", ["username"], unique=True)
    op.create_table(
        "admin_user",
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("name", sa.String(length=30), nullable=False),
        sa.Column("username", sa.String(length=20), nullable=False),
        sa.Column("email", sa.String(length=100), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("profile_image_url", sa.String(length=255), nullable=False),
        sa.Column("is_superuser", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("role_id", sa.Integer(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["role_id"], ["role.id"]),
    )
    op.create_index(op.f("ix_admin_user_email"), "admin_user", ["email"], unique=True)
    op.create_index(op.f("ix_admin_user_is_deleted"), "admin_user", ["is_deleted"], unique=False)
    op.create_index(op.f("ix_admin_user_is_superuser"), "admin_user", ["is_superuser"], unique=False)
    op.create_index(op.f("ix_admin_user_role_id"), "admin_user", ["role_id"], unique=False)
    op.create_index(op.f("ix_admin_user_status"), "admin_user", ["status"], unique=False)
    op.create_index(op.f("ix_admin_user_username"), "admin_user", ["username"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_admin_user_username"), table_name="admin_user")
    op.drop_index(op.f("ix_admin_user_status"), table_name="admin_user")
    op.drop_index(op.f("ix_admin_user_role_id"), table_name="admin_user")
    op.drop_index(op.f("ix_admin_user_is_superuser"), table_name="admin_user")
    op.drop_index(op.f("ix_admin_user_is_deleted"), table_name="admin_user")
    op.drop_index(op.f("ix_admin_user_email"), table_name="admin_user")
    op.drop_table("admin_user")

    op.drop_index(op.f("ix_user_username"), table_name="user")
    op.drop_index(op.f("ix_user_is_deleted"), table_name="user")
    op.drop_index(op.f("ix_user_email"), table_name="user")
    op.drop_table("user")

    op.drop_index(op.f("ix_role_name"), table_name="role")
    op.drop_table("role")
