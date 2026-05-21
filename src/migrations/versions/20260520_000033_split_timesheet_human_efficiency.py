"""split timesheet human efficiency fields

Revision ID: 20260520_000033
Revises: 20260520_000032
Create Date: 2026-05-20 16:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260520_000033"
down_revision: str | None = "20260520_000032"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "project_timesheet_record",
        sa.Column("customer_human_efficiency_minutes", sa.Numeric(precision=10, scale=2), nullable=True),
    )
    op.add_column(
        "project_timesheet_record",
        sa.Column("candidate_human_efficiency_minutes", sa.Numeric(precision=10, scale=2), nullable=True),
    )
    op.execute(
        """
        UPDATE project_timesheet_record
        SET
            customer_human_efficiency_minutes = human_efficiency_minutes,
            candidate_human_efficiency_minutes = human_efficiency_minutes
        WHERE human_efficiency_minutes IS NOT NULL
        """
    )
    op.drop_column("project_timesheet_record", "human_efficiency_minutes")


def downgrade() -> None:
    op.add_column(
        "project_timesheet_record",
        sa.Column("human_efficiency_minutes", sa.Numeric(precision=10, scale=2), nullable=True),
    )
    op.execute(
        """
        UPDATE project_timesheet_record
        SET human_efficiency_minutes = customer_human_efficiency_minutes
        WHERE customer_human_efficiency_minutes IS NOT NULL
        """
    )
    op.drop_column("project_timesheet_record", "candidate_human_efficiency_minutes")
    op.drop_column("project_timesheet_record", "customer_human_efficiency_minutes")
