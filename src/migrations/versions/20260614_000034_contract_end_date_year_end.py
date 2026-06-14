"""backfill contract end date to effective year end

Revision ID: 20260614_000034
Revises: 20260520_000033
Create Date: 2026-06-14 20:00:00.000000
"""

from collections.abc import Sequence
from datetime import date, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "20260614_000034"
down_revision: str | None = "20260520_000033"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            """
            SELECT id, effective_date
            FROM contract_record
            WHERE effective_date IS NOT NULL
              AND end_date IS NULL
              AND is_deleted = 0
            """
        )
    )
    for row in rows.mappings():
        effective_date = row["effective_date"]
        if isinstance(effective_date, str):
            effective_date = date.fromisoformat(effective_date[:10])
        elif isinstance(effective_date, datetime):
            effective_date = effective_date.date()
        if not isinstance(effective_date, date):
            continue
        connection.execute(
            sa.text("UPDATE contract_record SET end_date = :end_date WHERE id = :record_id"),
            {
                "record_id": row["id"],
                "end_date": date(effective_date.year, 12, 31),
            },
        )


def downgrade() -> None:
    pass
