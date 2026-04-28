"""add admin company table and job company relation

Revision ID: 20260419_000020
Revises: 20260410_000019
Create Date: 2026-04-19 16:20:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260419_000020"
down_revision: str | None = "20260410_000019"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "admin_company",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("logo_asset_id", sa.Integer(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("current_timestamp(0)")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.text("current_timestamp(0)")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(["logo_asset_id"], ["asset.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_admin_company_name"), "admin_company", ["name"], unique=True)
    op.create_index(op.f("ix_admin_company_logo_asset_id"), "admin_company", ["logo_asset_id"], unique=False)
    op.create_index(op.f("ix_admin_company_is_deleted"), "admin_company", ["is_deleted"], unique=False)

    op.add_column("job", sa.Column("company_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_job_company_id"), "job", ["company_id"], unique=False)
    op.create_foreign_key("fk_job_company_id_admin_company", "job", "admin_company", ["company_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint("fk_job_company_id_admin_company", "job", type_="foreignkey")
    op.drop_index(op.f("ix_job_company_id"), table_name="job")
    op.drop_column("job", "company_id")

    op.drop_index(op.f("ix_admin_company_is_deleted"), table_name="admin_company")
    op.drop_index(op.f("ix_admin_company_logo_asset_id"), table_name="admin_company")
    op.drop_index(op.f("ix_admin_company_name"), table_name="admin_company")
    op.drop_table("admin_company")
