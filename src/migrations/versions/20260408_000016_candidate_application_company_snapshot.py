"""add candidate application company snapshot

Revision ID: 20260408_000016
Revises: 20260407_000015
Create Date: 2026-04-08 10:25:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260408_000016"
down_revision: str | None = "20260407_000015"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("candidate_application", sa.Column("job_snapshot_company_name", sa.String(length=100), nullable=True))
    op.execute(
        """
        UPDATE candidate_application AS ca
        JOIN job AS j ON j.id = ca.job_id
        SET ca.job_snapshot_company_name = j.company_name
        WHERE ca.job_snapshot_company_name IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("candidate_application", "job_snapshot_company_name")
