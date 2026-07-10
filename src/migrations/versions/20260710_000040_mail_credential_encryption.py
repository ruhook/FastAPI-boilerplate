"""add encrypted mail account credentials

Revision ID: 20260710_000040
Revises: 20260628_000039
Create Date: 2026-07-10 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260710_000040"
down_revision: str | None = "20260628_000039"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _columns(table_name: str) -> dict[str, dict[str, object]]:
    inspector = sa.inspect(op.get_bind())
    return {str(column["name"]): column for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    columns = _columns("mail_account")
    if "auth_secret_encrypted" not in columns:
        op.add_column(
            "mail_account",
            sa.Column("auth_secret_encrypted", sa.String(length=1024), nullable=True),
        )
    if not bool(columns["auth_secret"]["nullable"]):
        op.alter_column(
            "mail_account",
            "auth_secret",
            existing_type=sa.String(length=255),
            nullable=True,
        )


def downgrade() -> None:
    columns = _columns("mail_account")
    if "auth_secret_encrypted" not in columns:
        return

    missing_plaintext_count = int(
        op.get_bind()
        .execute(sa.text("SELECT COUNT(*) FROM mail_account WHERE auth_secret IS NULL OR TRIM(auth_secret) = ''"))
        .scalar_one()
    )
    if missing_plaintext_count:
        raise RuntimeError(
            "Cannot downgrade mail credential encryption while encrypted-only rows exist; "
            "restore plaintext credentials explicitly before retrying."
        )

    if bool(columns["auth_secret"]["nullable"]):
        op.alter_column(
            "mail_account",
            "auth_secret",
            existing_type=sa.String(length=255),
            nullable=False,
        )
    op.drop_column("mail_account", "auth_secret_encrypted")
