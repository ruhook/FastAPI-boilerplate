"""scope mail domain data to admin users

Revision ID: 20260405_000008
Revises: 20260405_000007
Create Date: 2026-04-05 23:59:59.800000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260405_000008"
down_revision: str | None = "20260405_000007"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _drop_constraint_if_exists(table_name: str, constraint_name: str, constraint_type: str) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if constraint_type == "foreignkey":
        names = {item.get("name") for item in inspector.get_foreign_keys(table_name)}
    elif constraint_type == "unique":
        names = {item.get("name") for item in inspector.get_unique_constraints(table_name)}
    else:
        names = set()
    if constraint_name in names:
        op.drop_constraint(constraint_name, table_name, type_=constraint_type)


def _drop_index_if_exists(table_name: str, index_name: str) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    names = {item.get("name") for item in inspector.get_indexes(table_name)}
    if index_name in names:
        op.drop_index(index_name, table_name=table_name)


def upgrade() -> None:
    op.add_column("mail_account", sa.Column("admin_user_id", sa.Integer(), nullable=True))
    op.create_index("ix_mail_account_admin_user_id", "mail_account", ["admin_user_id"], unique=False)
    op.create_foreign_key("fk_mail_account_admin_user_id_admin_user", "mail_account", "admin_user", ["admin_user_id"], ["id"])
    _drop_index_if_exists("mail_account", "ix_mail_account_email")
    op.create_index("ix_mail_account_email", "mail_account", ["email"], unique=False)
    op.create_unique_constraint("uq_mail_account_admin_user_email", "mail_account", ["admin_user_id", "email"])

    op.add_column("mail_template_category", sa.Column("admin_user_id", sa.Integer(), nullable=True))
    op.create_index("ix_mail_template_category_admin_user_id", "mail_template_category", ["admin_user_id"], unique=False)
    op.create_foreign_key(
        "fk_mail_template_category_admin_user_id_admin_user",
        "mail_template_category",
        "admin_user",
        ["admin_user_id"],
        ["id"],
    )
    _drop_constraint_if_exists("mail_template_category", "uq_mail_template_category_scope_name", "unique")
    op.create_unique_constraint(
        "uq_mail_template_category_admin_user_scope_name",
        "mail_template_category",
        ["admin_user_id", "parent_id", "name"],
    )

    op.add_column("mail_template", sa.Column("admin_user_id", sa.Integer(), nullable=True))
    op.create_index("ix_mail_template_admin_user_id", "mail_template", ["admin_user_id"], unique=False)
    op.create_foreign_key("fk_mail_template_admin_user_id_admin_user", "mail_template", "admin_user", ["admin_user_id"], ["id"])
    _drop_constraint_if_exists("mail_template", "uq_mail_template_name", "unique")
    op.create_unique_constraint("uq_mail_template_admin_user_name", "mail_template", ["admin_user_id", "name"])

    op.add_column("mail_signature", sa.Column("admin_user_id", sa.Integer(), nullable=True))
    op.create_index("ix_mail_signature_admin_user_id", "mail_signature", ["admin_user_id"], unique=False)
    op.create_foreign_key("fk_mail_signature_admin_user_id_admin_user", "mail_signature", "admin_user", ["admin_user_id"], ["id"])
    _drop_constraint_if_exists("mail_signature", "uq_mail_signature_name", "unique")
    op.create_unique_constraint("uq_mail_signature_admin_user_name", "mail_signature", ["admin_user_id", "name"])


def downgrade() -> None:
    _drop_constraint_if_exists("mail_signature", "uq_mail_signature_admin_user_name", "unique")
    _drop_constraint_if_exists("mail_signature", "fk_mail_signature_admin_user_id_admin_user", "foreignkey")
    _drop_index_if_exists("mail_signature", "ix_mail_signature_admin_user_id")
    op.drop_column("mail_signature", "admin_user_id")
    op.create_unique_constraint("uq_mail_signature_name", "mail_signature", ["name"])

    _drop_constraint_if_exists("mail_template", "uq_mail_template_admin_user_name", "unique")
    _drop_constraint_if_exists("mail_template", "fk_mail_template_admin_user_id_admin_user", "foreignkey")
    _drop_index_if_exists("mail_template", "ix_mail_template_admin_user_id")
    op.drop_column("mail_template", "admin_user_id")
    op.create_unique_constraint("uq_mail_template_name", "mail_template", ["name"])

    _drop_constraint_if_exists("mail_template_category", "uq_mail_template_category_admin_user_scope_name", "unique")
    _drop_constraint_if_exists("mail_template_category", "fk_mail_template_category_admin_user_id_admin_user", "foreignkey")
    _drop_index_if_exists("mail_template_category", "ix_mail_template_category_admin_user_id")
    op.drop_column("mail_template_category", "admin_user_id")

    _drop_constraint_if_exists("mail_account", "uq_mail_account_admin_user_email", "unique")
    _drop_constraint_if_exists("mail_account", "fk_mail_account_admin_user_id_admin_user", "foreignkey")
    _drop_index_if_exists("mail_account", "ix_mail_account_admin_user_id")
    _drop_index_if_exists("mail_account", "ix_mail_account_email")
    op.drop_column("mail_account", "admin_user_id")
    op.create_index("ix_mail_account_email", "mail_account", ["email"], unique=True)
