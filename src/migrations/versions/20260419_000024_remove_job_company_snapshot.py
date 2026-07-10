"""remove job company snapshot and enforce company relation

Revision ID: 20260419_000024
Revises: 20260419_000023
Create Date: 2026-04-19 23:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260419_000024"
down_revision: str | None = "20260419_000023"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    company_table = sa.Table(
        "admin_company",
        metadata,
        sa.Column("id", sa.Integer()),
        sa.Column("name", sa.String(length=120)),
        sa.Column("description", sa.Text()),
        sa.Column("data", sa.JSON()),
    )
    job_table = sa.Table(
        "job",
        metadata,
        sa.Column("id", sa.Integer()),
        sa.Column("company_id", sa.Integer()),
        sa.Column("company_name", sa.String(length=100)),
    )

    existing_companies = {
        str(name).strip(): int(company_id)
        for company_id, name in bind.execute(sa.select(company_table.c.id, company_table.c.name)).all()
        if name and str(name).strip()
    }

    jobs_without_company = bind.execute(
        sa.select(job_table.c.id, job_table.c.company_name).where(job_table.c.company_id.is_(None))
    ).all()
    for job_id, raw_name in jobs_without_company:
        normalized_name = str(raw_name or "").strip() or f"Job Company {job_id}"
        company_id = existing_companies.get(normalized_name)
        if company_id is None:
            bind.execute(
                company_table.insert().values(
                    name=normalized_name,
                    description=None,
                    data={},
                )
            )
            company_id = int(
                bind.execute(sa.select(company_table.c.id).where(company_table.c.name == normalized_name)).scalar_one()
            )
            existing_companies[normalized_name] = company_id
        bind.execute(job_table.update().where(job_table.c.id == job_id).values(company_id=company_id))

    op.alter_column("job", "company_id", existing_type=sa.Integer(), nullable=False)
    op.drop_index(op.f("ix_job_company_name"), table_name="job")
    op.drop_column("job", "company_name")


def downgrade() -> None:
    op.add_column(
        "job",
        sa.Column(
            "company_name",
            sa.String(length=100),
            nullable=False,
            server_default="DA",
        ),
    )
    op.create_index(op.f("ix_job_company_name"), "job", ["company_name"], unique=False)

    bind = op.get_bind()
    metadata = sa.MetaData()
    company_table = sa.Table(
        "admin_company",
        metadata,
        sa.Column("id", sa.Integer()),
        sa.Column("name", sa.String(length=120)),
    )
    job_table = sa.Table(
        "job",
        metadata,
        sa.Column("id", sa.Integer()),
        sa.Column("company_id", sa.Integer()),
        sa.Column("company_name", sa.String(length=100)),
    )

    rows = bind.execute(
        sa.select(job_table.c.id, company_table.c.name).select_from(
            job_table.outerjoin(company_table, company_table.c.id == job_table.c.company_id)
        )
    ).all()
    for job_id, company_name in rows:
        bind.execute(
            job_table.update()
            .where(job_table.c.id == job_id)
            .values(company_name=(str(company_name or "").strip() or "DA"))
        )

    op.alter_column("job", "company_name", server_default=None)
    op.alter_column("job", "company_id", existing_type=sa.Integer(), nullable=True)
