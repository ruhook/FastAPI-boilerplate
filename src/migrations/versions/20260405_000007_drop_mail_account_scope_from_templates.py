"""drop mail account scope from templates, categories and signatures

Revision ID: 20260405_000007
Revises: 20260405_000006
Create Date: 2026-04-05 23:59:59.500000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260405_000007"
down_revision: str | None = "20260405_000006"
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
    _drop_constraint_if_exists("mail_template_category", "mail_template_category_ibfk_1", "foreignkey")
    _drop_constraint_if_exists("mail_template_category", "uq_mail_template_category_scope_name", "unique")
    _drop_index_if_exists("mail_template_category", "ix_mail_template_category_account_id")
    op.drop_column("mail_template_category", "account_id")

    _drop_constraint_if_exists("mail_template", "mail_template_ibfk_1", "foreignkey")
    _drop_constraint_if_exists("mail_template", "uq_mail_template_account_name", "unique")
    _drop_index_if_exists("mail_template", "ix_mail_template_account_id")
    op.drop_column("mail_template", "account_id")
    op.create_unique_constraint("uq_mail_template_name", "mail_template", ["name"])

    _drop_constraint_if_exists("mail_signature", "mail_signature_ibfk_1", "foreignkey")
    _drop_constraint_if_exists("mail_signature", "uq_mail_signature_account_name", "unique")
    _drop_index_if_exists("mail_signature", "ix_mail_signature_account_id")
    op.drop_column("mail_signature", "account_id")
    op.create_unique_constraint("uq_mail_signature_name", "mail_signature", ["name"])


def downgrade() -> None:
    op.add_column("mail_template_category", sa.Column("account_id", sa.Integer(), nullable=True))
    op.create_index("ix_mail_template_category_account_id", "mail_template_category", ["account_id"], unique=False)
    op.create_foreign_key("mail_template_category_ibfk_1", "mail_template_category", "mail_account", ["account_id"], ["id"])
    op.create_unique_constraint("uq_mail_template_category_scope_name", "mail_template_category", ["account_id", "parent_id", "name"])

    _drop_constraint_if_exists("mail_template", "uq_mail_template_name", "unique")
    op.add_column("mail_template", sa.Column("account_id", sa.Integer(), nullable=True))
    op.create_index("ix_mail_template_account_id", "mail_template", ["account_id"], unique=False)
    op.create_foreign_key("mail_template_ibfk_1", "mail_template", "mail_account", ["account_id"], ["id"])
    op.create_unique_constraint("uq_mail_template_account_name", "mail_template", ["account_id", "name"])

    _drop_constraint_if_exists("mail_signature", "uq_mail_signature_name", "unique")
    op.add_column("mail_signature", sa.Column("account_id", sa.Integer(), nullable=True))
    op.create_index("ix_mail_signature_account_id", "mail_signature", ["account_id"], unique=False)
    op.create_foreign_key("mail_signature_ibfk_1", "mail_signature", "mail_account", ["account_id"], ["id"])
    op.create_unique_constraint("uq_mail_signature_account_name", "mail_signature", ["account_id", "name"])
