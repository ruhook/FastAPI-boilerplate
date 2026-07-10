"""add revocable authentication sessions

Revision ID: 20260710_000041
Revises: 20260710_000040
Create Date: 2026-07-10 17:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260710_000041"
down_revision: str | None = "20260710_000040"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _table_exists(table_name: str) -> bool:
    return table_name in sa.inspect(op.get_bind()).get_table_names()


def _column_exists(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column_name in {str(column["name"]) for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    for table_name in ("user", "admin_user"):
        if not _column_exists(table_name, "token_version"):
            op.add_column(
                table_name,
                sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
            )

    if not _table_exists("auth_refresh_session"):
        op.create_table(
            "auth_refresh_session",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("token_hash", sa.String(length=64), nullable=False),
            sa.Column("portal", sa.String(length=16), nullable=False),
            sa.Column("account_id", sa.BigInteger(), nullable=False),
            sa.Column("family_id", sa.String(length=36), nullable=False),
            sa.Column("parent_session_id", sa.BigInteger(), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("rotation_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revocation_reason", sa.String(length=32), nullable=True),
            sa.Column("user_agent_hash", sa.String(length=64), nullable=True),
            sa.Column("rotation_count", sa.Integer(), nullable=False, server_default="0"),
            sa.ForeignKeyConstraint(
                ["parent_session_id"],
                ["auth_refresh_session.id"],
                name="fk_auth_refresh_session_parent",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("token_hash", name="uq_auth_refresh_session_token_hash"),
        )
        op.create_index(
            "ix_auth_refresh_session_account",
            "auth_refresh_session",
            ["portal", "account_id"],
            unique=False,
        )
        op.create_index(
            "ix_auth_refresh_session_family",
            "auth_refresh_session",
            ["family_id", "revoked_at"],
            unique=False,
        )
        op.create_index(
            op.f("ix_auth_refresh_session_token_hash"),
            "auth_refresh_session",
            ["token_hash"],
            unique=True,
        )


def downgrade() -> None:
    if _table_exists("auth_refresh_session"):
        op.drop_table("auth_refresh_session")
    for table_name in ("admin_user", "user"):
        if _column_exists(table_name, "token_version"):
            op.drop_column(table_name, "token_version")
