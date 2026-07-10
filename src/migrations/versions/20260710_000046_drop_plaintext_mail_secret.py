"""drop plaintext mail account credential

Revision ID: 20260710_000046
Revises: 20260710_000045
Create Date: 2026-07-10 23:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260710_000046"
down_revision: str | None = "20260710_000045"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("mail_account", "auth_secret")


def downgrade() -> None:
    op.add_column("mail_account", sa.Column("auth_secret", sa.String(length=255), nullable=True))
