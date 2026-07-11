"""add typed contract workflow state columns

Revision ID: 20260711_000051
Revises: 20260711_000050
Create Date: 2026-07-11 14:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260711_000051"
down_revision: str | None = "20260711_000050"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "contract_record",
        sa.Column(
            "contract_review_status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
    )
    op.add_column(
        "contract_record",
        sa.Column(
            "signing_status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'not_sent'"),
        ),
    )
    op.create_index(
        "ix_contract_record_contract_review_status",
        "contract_record",
        ["contract_review_status"],
    )
    op.create_index("ix_contract_record_signing_status", "contract_record", ["signing_status"])
    op.execute("UPDATE contract_record SET contract_status = 'pending_activation'")
    op.alter_column(
        "contract_record",
        "contract_status",
        existing_type=sa.String(length=32),
        nullable=False,
        server_default=sa.text("'pending_activation'"),
    )


def downgrade() -> None:
    op.execute("UPDATE contract_record SET contract_status = 'Pending Activation'")
    op.alter_column(
        "contract_record",
        "contract_status",
        existing_type=sa.String(length=32),
        nullable=False,
        server_default=sa.text("'Pending Activation'"),
    )
    op.drop_index("ix_contract_record_signing_status", table_name="contract_record")
    op.drop_index("ix_contract_record_contract_review_status", table_name="contract_record")
    op.drop_column("contract_record", "signing_status")
    op.drop_column("contract_record", "contract_review_status")
