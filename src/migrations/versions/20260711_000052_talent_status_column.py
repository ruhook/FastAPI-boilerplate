"""add typed talent status override column

Revision ID: 20260711_000052
Revises: 20260711_000051
Create Date: 2026-07-11 16:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260711_000052"
down_revision: str | None = "20260711_000051"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "talent_profile",
        sa.Column("status_override", sa.String(length=32), nullable=True),
    )
    op.create_index(
        "ix_talent_profile_status_override",
        "talent_profile",
        ["status_override"],
    )
    op.execute(
        """
        UPDATE talent_profile
        SET status_override = NULLIF(
            JSON_UNQUOTE(JSON_EXTRACT(data, '$.talent_status_override')),
            ''
        )
        WHERE JSON_EXTRACT(data, '$.talent_status_override') IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE talent_profile
        SET data = JSON_REMOVE(data, '$.talent_status_override')
        WHERE JSON_EXTRACT(data, '$.talent_status_override') IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE talent_profile
        SET data = JSON_SET(
            COALESCE(data, JSON_OBJECT()),
            '$.talent_status_override',
            status_override
        )
        WHERE status_override IS NOT NULL
        """
    )
    op.drop_index("ix_talent_profile_status_override", table_name="talent_profile")
    op.drop_column("talent_profile", "status_override")
