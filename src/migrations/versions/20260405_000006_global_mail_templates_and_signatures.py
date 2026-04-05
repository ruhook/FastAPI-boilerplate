"""make mail templates, categories and signatures global

Revision ID: 20260405_000006
Revises: 20260405_000005
Create Date: 2026-04-05 23:59:59.000000
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "20260405_000006"
down_revision: str | None = "20260405_000005"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _ensure_fallback_mail_account_id() -> int:
    bind = op.get_bind()
    existing_id = bind.execute(sa.text("SELECT id FROM mail_account ORDER BY id ASC LIMIT 1")).scalar()
    if existing_id is not None:
        return int(existing_id)

    now = datetime.now(UTC)
    bind.execute(
        sa.text(
            """
            INSERT INTO mail_account (
                data,
                email,
                provider,
                smtp_username,
                smtp_host,
                smtp_port,
                security_mode,
                auth_secret,
                status,
                note,
                verified_at,
                last_tested_at,
                created_at,
                updated_at,
                deleted_at,
                is_deleted
            ) VALUES (
                :data,
                :email,
                :provider,
                :smtp_username,
                :smtp_host,
                :smtp_port,
                :security_mode,
                :auth_secret,
                :status,
                :note,
                NULL,
                NULL,
                :created_at,
                :updated_at,
                NULL,
                0
            )
            """
        ),
        {
            "data": "{}",
            "email": "legacy-mail-account@local.invalid",
            "provider": "custom",
            "smtp_username": "legacy-mail-account@local.invalid",
            "smtp_host": "localhost",
            "smtp_port": 25,
            "security_mode": "none",
            "auth_secret": "legacy-placeholder",
            "status": "disabled",
            "note": "Generated during downgrade of global mail templates/signatures.",
            "created_at": now,
            "updated_at": now,
        },
    )
    fallback_id = bind.execute(sa.text("SELECT id FROM mail_account ORDER BY id DESC LIMIT 1")).scalar()
    return int(fallback_id)


def upgrade() -> None:
    op.alter_column("mail_template_category", "account_id", existing_type=sa.Integer(), nullable=True)
    op.alter_column("mail_template", "account_id", existing_type=sa.Integer(), nullable=True)
    op.alter_column("mail_signature", "account_id", existing_type=sa.Integer(), nullable=True)

    op.execute(sa.text("UPDATE mail_template_category SET account_id = NULL"))
    op.execute(sa.text("UPDATE mail_template SET account_id = NULL"))
    op.execute(sa.text("UPDATE mail_signature SET account_id = NULL"))
    op.execute(sa.text("UPDATE asset SET owner_type = NULL, owner_id = NULL WHERE module = 'mail'"))


def downgrade() -> None:
    fallback_account_id = _ensure_fallback_mail_account_id()

    op.execute(
        sa.text(
            "UPDATE asset SET owner_type = 'mail_account', owner_id = :owner_id WHERE module = 'mail'"
        ),
        {"owner_id": fallback_account_id},
    )
    op.execute(
        sa.text("UPDATE mail_template_category SET account_id = :account_id WHERE account_id IS NULL"),
        {"account_id": fallback_account_id},
    )
    op.execute(
        sa.text("UPDATE mail_template SET account_id = :account_id WHERE account_id IS NULL"),
        {"account_id": fallback_account_id},
    )
    op.execute(
        sa.text("UPDATE mail_signature SET account_id = :account_id WHERE account_id IS NULL"),
        {"account_id": fallback_account_id},
    )

    op.alter_column("mail_signature", "account_id", existing_type=sa.Integer(), nullable=False)
    op.alter_column("mail_template", "account_id", existing_type=sa.Integer(), nullable=False)
    op.alter_column("mail_template_category", "account_id", existing_type=sa.Integer(), nullable=False)
