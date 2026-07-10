"""add contract type and resign linkage

Revision ID: 20260428_000028
Revises: 20260428_000027
Create Date: 2026-04-28 18:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260428_000028"
down_revision: str | None = "20260428_000027"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "contract_record",
        sa.Column("contract_type", sa.String(length=32), nullable=False, server_default="normal"),
    )
    op.add_column(
        "contract_record",
        sa.Column("previous_contract_record_id", sa.Integer(), nullable=True),
    )
    op.create_index(op.f("ix_contract_record_contract_type"), "contract_record", ["contract_type"], unique=False)
    op.create_index(
        op.f("ix_contract_record_previous_contract_record_id"),
        "contract_record",
        ["previous_contract_record_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_contract_record_previous_contract_record_id_contract_record",
        "contract_record",
        "contract_record",
        ["previous_contract_record_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_contract_record_previous_contract_record_id_contract_record",
        "contract_record",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_contract_record_previous_contract_record_id"), table_name="contract_record")
    op.drop_index(op.f("ix_contract_record_contract_type"), table_name="contract_record")
    op.drop_column("contract_record", "previous_contract_record_id")
    op.drop_column("contract_record", "contract_type")
