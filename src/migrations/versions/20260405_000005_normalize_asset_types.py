"""normalize asset types to generic media groups

Revision ID: 20260405_000005
Revises: 20260405_000004
Create Date: 2026-04-05 23:59:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260405_000005"
down_revision: str | None = "20260405_000004"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE asset
            SET type = CASE
                WHEN type IN ('mail_signature_avatar', 'mail_signature_banner') THEN 'image'
                WHEN type IN ('mail_attachment', 'mail_file') THEN 'file'
                ELSE type
            END
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE asset
            SET type = CASE
                WHEN module = 'mail' AND type = 'image' THEN 'mail_signature_avatar'
                WHEN module = 'mail' AND type = 'file' THEN 'mail_attachment'
                ELSE type
            END
            """
        )
    )
