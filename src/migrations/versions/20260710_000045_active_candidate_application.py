"""enforce one active application per candidate and job

Revision ID: 20260710_000045
Revises: 20260710_000044
Create Date: 2026-07-10 22:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260710_000045"
down_revision: str | None = "20260710_000044"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "candidate_application",
        sa.Column(
            "active_job_id",
            sa.Integer(),
            sa.Computed("CASE WHEN is_deleted = 0 THEN job_id ELSE NULL END", persisted=True),
            nullable=True,
        ),
    )
    op.create_index(
        "uq_candidate_application_active_user_job",
        "candidate_application",
        ["user_id", "active_job_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_candidate_application_active_user_job",
        table_name="candidate_application",
    )
    op.drop_column("candidate_application", "active_job_id")
