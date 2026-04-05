"""mail domain tables

Revision ID: 20260405_000003
Revises: 20260404_000002
Create Date: 2026-04-05 22:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260405_000003"
down_revision: str | None = "20260404_000002"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mail_account",
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("smtp_username", sa.String(length=255), nullable=False),
        sa.Column("smtp_host", sa.String(length=255), nullable=False),
        sa.Column("smtp_port", sa.Integer(), nullable=False),
        sa.Column("security_mode", sa.String(length=16), nullable=False),
        sa.Column("auth_secret", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_mail_account_email"), "mail_account", ["email"], unique=True)
    op.create_index(op.f("ix_mail_account_is_deleted"), "mail_account", ["is_deleted"], unique=False)
    op.create_index(op.f("ix_mail_account_provider"), "mail_account", ["provider"], unique=False)
    op.create_index(op.f("ix_mail_account_status"), "mail_account", ["status"], unique=False)

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

    op.create_table(
        "mail_template_category",
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(["account_id"], ["mail_account.id"]),
        sa.ForeignKeyConstraint(["parent_id"], ["mail_template_category.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "parent_id", "name", name="uq_mail_template_category_scope_name"),
    )
    op.create_index(op.f("ix_mail_template_category_account_id"), "mail_template_category", ["account_id"], unique=False)
    op.create_index(op.f("ix_mail_template_category_is_deleted"), "mail_template_category", ["is_deleted"], unique=False)
    op.create_index(op.f("ix_mail_template_category_parent_id"), "mail_template_category", ["parent_id"], unique=False)

    op.create_table(
        "mail_template",
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("subject_template", sa.String(length=500), nullable=False),
        sa.Column("body_html", sa.Text(), nullable=False),
        sa.Column("attachments", sa.JSON(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(["account_id"], ["mail_account.id"]),
        sa.ForeignKeyConstraint(["category_id"], ["mail_template_category.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "name", name="uq_mail_template_account_name"),
    )
    op.create_index(op.f("ix_mail_template_account_id"), "mail_template", ["account_id"], unique=False)
    op.create_index(op.f("ix_mail_template_category_id"), "mail_template", ["category_id"], unique=False)
    op.create_index(op.f("ix_mail_template_is_deleted"), "mail_template", ["is_deleted"], unique=False)
    op.create_index(op.f("ix_mail_template_name"), "mail_template", ["name"], unique=False)

    op.create_table(
        "mail_signature",
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("owner", sa.String(length=120), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("full_name", sa.String(length=120), nullable=False),
        sa.Column("job_title", sa.String(length=120), nullable=True),
        sa.Column("company_name", sa.String(length=120), nullable=True),
        sa.Column("primary_email", sa.String(length=255), nullable=True),
        sa.Column("secondary_email", sa.String(length=255), nullable=True),
        sa.Column("website", sa.String(length=500), nullable=True),
        sa.Column("linkedin_label", sa.String(length=255), nullable=True),
        sa.Column("linkedin_url", sa.String(length=500), nullable=True),
        sa.Column("address", sa.String(length=500), nullable=True),
        sa.Column("avatar_asset_id", sa.Integer(), nullable=True),
        sa.Column("banner_asset_id", sa.Integer(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(["account_id"], ["mail_account.id"]),
        sa.ForeignKeyConstraint(["avatar_asset_id"], ["mail_asset.id"]),
        sa.ForeignKeyConstraint(["banner_asset_id"], ["mail_asset.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "name", name="uq_mail_signature_account_name"),
    )
    op.create_index(op.f("ix_mail_signature_account_id"), "mail_signature", ["account_id"], unique=False)
    op.create_index(op.f("ix_mail_signature_is_deleted"), "mail_signature", ["is_deleted"], unique=False)
    op.create_index(op.f("ix_mail_signature_name"), "mail_signature", ["name"], unique=False)

    op.create_table(
        "mail_task",
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("template_id", sa.Integer(), nullable=True),
        sa.Column("signature_id", sa.Integer(), nullable=True),
        sa.Column("subject", sa.String(length=500), nullable=False),
        sa.Column("body_html", sa.Text(), nullable=False),
        sa.Column("to_recipients", sa.JSON(), nullable=False),
        sa.Column("cc_recipients", sa.JSON(), nullable=False),
        sa.Column("bcc_recipients", sa.JSON(), nullable=False),
        sa.Column("attachment_asset_ids", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["mail_account.id"]),
        sa.ForeignKeyConstraint(["signature_id"], ["mail_signature.id"]),
        sa.ForeignKeyConstraint(["template_id"], ["mail_template.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_mail_task_account_id"), "mail_task", ["account_id"], unique=False)
    op.create_index(op.f("ix_mail_task_signature_id"), "mail_task", ["signature_id"], unique=False)
    op.create_index(op.f("ix_mail_task_status"), "mail_task", ["status"], unique=False)
    op.create_index(op.f("ix_mail_task_template_id"), "mail_task", ["template_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_mail_task_template_id"), table_name="mail_task")
    op.drop_index(op.f("ix_mail_task_status"), table_name="mail_task")
    op.drop_index(op.f("ix_mail_task_signature_id"), table_name="mail_task")
    op.drop_index(op.f("ix_mail_task_account_id"), table_name="mail_task")
    op.drop_table("mail_task")

    op.drop_index(op.f("ix_mail_signature_name"), table_name="mail_signature")
    op.drop_index(op.f("ix_mail_signature_is_deleted"), table_name="mail_signature")
    op.drop_index(op.f("ix_mail_signature_account_id"), table_name="mail_signature")
    op.drop_table("mail_signature")

    op.drop_index(op.f("ix_mail_template_name"), table_name="mail_template")
    op.drop_index(op.f("ix_mail_template_is_deleted"), table_name="mail_template")
    op.drop_index(op.f("ix_mail_template_category_id"), table_name="mail_template")
    op.drop_index(op.f("ix_mail_template_account_id"), table_name="mail_template")
    op.drop_table("mail_template")

    op.drop_index(op.f("ix_mail_template_category_parent_id"), table_name="mail_template_category")
    op.drop_index(op.f("ix_mail_template_category_is_deleted"), table_name="mail_template_category")
    op.drop_index(op.f("ix_mail_template_category_account_id"), table_name="mail_template_category")
    op.drop_table("mail_template_category")

    op.drop_index(op.f("ix_mail_asset_storage_key"), table_name="mail_asset")
    op.drop_index(op.f("ix_mail_asset_kind"), table_name="mail_asset")
    op.drop_index(op.f("ix_mail_asset_is_deleted"), table_name="mail_asset")
    op.drop_index(op.f("ix_mail_asset_account_id"), table_name="mail_asset")
    op.drop_table("mail_asset")

    op.drop_index(op.f("ix_mail_account_status"), table_name="mail_account")
    op.drop_index(op.f("ix_mail_account_provider"), table_name="mail_account")
    op.drop_index(op.f("ix_mail_account_is_deleted"), table_name="mail_account")
    op.drop_index(op.f("ix_mail_account_email"), table_name="mail_account")
    op.drop_table("mail_account")
