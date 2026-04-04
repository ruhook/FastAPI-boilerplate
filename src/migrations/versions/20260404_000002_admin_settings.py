"""admin settings tables

Revision ID: 20260404_000002
Revises: 20260403_000001
Create Date: 2026-04-04 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260404_000002"
down_revision: str | None = "20260403_000001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "admin_dictionary",
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_admin_dictionary_is_deleted"), "admin_dictionary", ["is_deleted"], unique=False)
    op.create_index(op.f("ix_admin_dictionary_label"), "admin_dictionary", ["label"], unique=True)

    op.create_table(
        "admin_form_template",
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("fields", sa.JSON(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_admin_form_template_is_deleted"), "admin_form_template", ["is_deleted"], unique=False)
    op.create_index(op.f("ix_admin_form_template_name"), "admin_form_template", ["name"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_admin_form_template_name"), table_name="admin_form_template")
    op.drop_index(op.f("ix_admin_form_template_is_deleted"), table_name="admin_form_template")
    op.drop_table("admin_form_template")

    op.drop_index(op.f("ix_admin_dictionary_label"), table_name="admin_dictionary")
    op.drop_index(op.f("ix_admin_dictionary_is_deleted"), table_name="admin_dictionary")
    op.drop_table("admin_dictionary")
