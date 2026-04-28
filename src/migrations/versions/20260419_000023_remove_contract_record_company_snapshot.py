"""remove contract record company snapshot

Revision ID: 20260419_000023
Revises: 20260419_000022
Create Date: 2026-04-20 00:20:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260419_000023"
down_revision: str | None = "20260419_000022"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index(op.f("ix_contract_record_service_customer_company_name"), table_name="contract_record")
    op.drop_column("contract_record", "service_customer_company_name")


def downgrade() -> None:
    op.add_column("contract_record", sa.Column("service_customer_company_name", sa.String(length=120), nullable=True))
    op.create_index(
        op.f("ix_contract_record_service_customer_company_name"),
        "contract_record",
        ["service_customer_company_name"],
        unique=False,
    )
