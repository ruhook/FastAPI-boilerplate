"""promote mail assets to global assets

Revision ID: 20260405_000004
Revises: 20260405_000003
Create Date: 2026-04-05 23:55:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260405_000004"
down_revision: str | None = "20260405_000003"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _drop_matching_foreign_keys(table_name: str, *, referred_table: str, constrained_columns: set[str]) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for foreign_key in inspector.get_foreign_keys(table_name):
        name = foreign_key.get("name")
        if not name:
            continue
        if foreign_key.get("referred_table") != referred_table:
            continue
        columns = set(foreign_key.get("constrained_columns") or [])
        if columns & constrained_columns:
            op.drop_constraint(name, table_name, type_="foreignkey")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("asset"):
        _drop_matching_foreign_keys(
            "mail_signature",
            referred_table="asset",
            constrained_columns={"avatar_asset_id", "banner_asset_id"},
        )
        op.drop_table("asset")

    op.create_table(
        "asset",
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("module", sa.String(length=64), nullable=False),
        sa.Column("owner_type", sa.String(length=64), nullable=True),
        sa.Column("owner_id", sa.Integer(), nullable=True),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=255), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_asset_is_deleted"), "asset", ["is_deleted"], unique=False)
    op.create_index(op.f("ix_asset_module"), "asset", ["module"], unique=False)
    op.create_index(op.f("ix_asset_owner_id"), "asset", ["owner_id"], unique=False)
    op.create_index(op.f("ix_asset_owner_type"), "asset", ["owner_type"], unique=False)
    op.create_index(op.f("ix_asset_storage_key"), "asset", ["storage_key"], unique=True)
    op.create_index(op.f("ix_asset_type"), "asset", ["type"], unique=False)

    op.execute(
        sa.text(
            """
            INSERT INTO asset (
                id,
                data,
                type,
                module,
                owner_type,
                owner_id,
                original_name,
                storage_key,
                mime_type,
                file_size,
                created_at,
                updated_at,
                deleted_at,
                is_deleted
            )
            SELECT
                id,
                data,
                CASE kind
                    WHEN 'attachment' THEN 'file'
                    WHEN 'avatar' THEN 'image'
                    WHEN 'banner' THEN 'image'
                    ELSE 'file'
                END,
                'mail',
                'mail_account',
                account_id,
                original_name,
                storage_key,
                mime_type,
                file_size,
                created_at,
                updated_at,
                deleted_at,
                is_deleted
            FROM mail_asset
            """
        )
    )

    _drop_matching_foreign_keys(
        "mail_signature",
        referred_table="mail_asset",
        constrained_columns={"avatar_asset_id", "banner_asset_id"},
    )
    op.create_foreign_key(
        "fk_mail_signature_avatar_asset_id_asset",
        "mail_signature",
        "asset",
        ["avatar_asset_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_mail_signature_banner_asset_id_asset",
        "mail_signature",
        "asset",
        ["banner_asset_id"],
        ["id"],
    )

    op.drop_table("mail_asset")


def downgrade() -> None:
    _drop_matching_foreign_keys(
        "mail_signature",
        referred_table="asset",
        constrained_columns={"avatar_asset_id", "banner_asset_id"},
    )

    op.create_table(
        "mail_asset",
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=255), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(["account_id"], ["mail_account.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_mail_asset_account_id"), "mail_asset", ["account_id"], unique=False)
    op.create_index(op.f("ix_mail_asset_is_deleted"), "mail_asset", ["is_deleted"], unique=False)
    op.create_index(op.f("ix_mail_asset_kind"), "mail_asset", ["kind"], unique=False)
    op.create_index(op.f("ix_mail_asset_storage_key"), "mail_asset", ["storage_key"], unique=True)

    op.execute(
        sa.text(
            """
            INSERT INTO mail_asset (
                id,
                data,
                account_id,
                kind,
                original_name,
                storage_key,
                mime_type,
                file_size,
                created_at,
                updated_at,
                deleted_at,
                is_deleted
            )
            SELECT
                id,
                data,
                owner_id,
                CASE type
                    WHEN 'file' THEN 'attachment'
                    WHEN 'image' THEN 'avatar'
                    ELSE 'attachment'
                END,
                original_name,
                storage_key,
                mime_type,
                file_size,
                created_at,
                updated_at,
                deleted_at,
                is_deleted
            FROM asset
            WHERE module = 'mail' AND owner_type = 'mail_account'
            """
        )
    )

    op.create_foreign_key(
        "fk_mail_signature_avatar_asset_id_mail_asset",
        "mail_signature",
        "mail_asset",
        ["avatar_asset_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_mail_signature_banner_asset_id_mail_asset",
        "mail_signature",
        "mail_asset",
        ["banner_asset_id"],
        ["id"],
    )

    op.drop_table("asset")
