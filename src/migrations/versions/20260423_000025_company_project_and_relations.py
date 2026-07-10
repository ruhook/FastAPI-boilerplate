"""add company project table and enforce job/project relations

Revision ID: 20260423_000025
Revises: 20260419_000024
Create Date: 2026-04-23 15:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import func

revision: str = "20260423_000025"
down_revision: str | None = "20260419_000024"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


DEFAULT_PROJECT_NAME = "Default Project"
JOB_PROJECT_FK_NAME = "fk_job_project_id_admin_company_project"
CONTRACT_PROJECT_FK_NAME = "fk_contract_record_project_id"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("admin_company_project"):
        op.create_table(
            "admin_company_project",
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("data", sa.JSON(), nullable=False),
            sa.Column(
                "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("current_timestamp(0)")
            ),
            sa.Column(
                "updated_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.text("current_timestamp(0)")
            ),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.ForeignKeyConstraint(["company_id"], ["admin_company.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "company_id", "name", "is_deleted", name="uq_admin_company_project_company_name_active"
            ),
        )
        inspector = sa.inspect(bind)

    project_indexes = {index["name"] for index in inspector.get_indexes("admin_company_project")}
    if op.f("ix_admin_company_project_company_id") not in project_indexes:
        op.create_index(
            op.f("ix_admin_company_project_company_id"), "admin_company_project", ["company_id"], unique=False
        )
    if op.f("ix_admin_company_project_name") not in project_indexes:
        op.create_index(op.f("ix_admin_company_project_name"), "admin_company_project", ["name"], unique=False)
    if op.f("ix_admin_company_project_is_deleted") not in project_indexes:
        op.create_index(
            op.f("ix_admin_company_project_is_deleted"), "admin_company_project", ["is_deleted"], unique=False
        )

    job_columns = {column["name"] for column in inspector.get_columns("job")}
    if "project_id" not in job_columns:
        op.add_column("job", sa.Column("project_id", sa.Integer(), nullable=True))
        inspector = sa.inspect(bind)

    metadata = sa.MetaData()
    company_table = sa.Table(
        "admin_company",
        metadata,
        sa.Column("id", sa.Integer()),
    )
    project_table = sa.Table(
        "admin_company_project",
        metadata,
        sa.Column("id", sa.Integer()),
        sa.Column("company_id", sa.Integer()),
        sa.Column("name", sa.String(length=120)),
        sa.Column("data", sa.JSON()),
        sa.Column("is_deleted", sa.Boolean()),
    )
    job_table = sa.Table(
        "job",
        metadata,
        sa.Column("id", sa.Integer()),
        sa.Column("company_id", sa.Integer()),
        sa.Column("project_id", sa.Integer()),
    )
    contract_table = sa.Table(
        "contract_record",
        metadata,
        sa.Column("id", sa.Integer()),
        sa.Column("job_id", sa.Integer()),
        sa.Column("service_customer_project_id", sa.Integer()),
    )

    company_ids = [int(company_id) for (company_id,) in bind.execute(sa.select(company_table.c.id)).all()]
    existing_project_company_ids = {
        int(company_id)
        for (company_id,) in bind.execute(
            sa.select(project_table.c.company_id).where(project_table.c.is_deleted.is_(False))
        ).all()
        if company_id is not None
    }
    for company_id in company_ids:
        if company_id in existing_project_company_ids:
            continue
        bind.execute(
            project_table.insert().values(
                company_id=company_id,
                name=DEFAULT_PROJECT_NAME,
                data={},
            )
        )

    project_rows = bind.execute(
        sa.select(project_table.c.id, project_table.c.company_id).where(project_table.c.is_deleted.is_(False))
    ).all()
    company_project_map = {int(company_id): int(project_id) for project_id, company_id in project_rows}

    job_rows = bind.execute(
        sa.select(job_table.c.id, job_table.c.company_id).where(job_table.c.project_id.is_(None))
    ).all()
    for job_id, company_id in job_rows:
        project_id = company_project_map.get(int(company_id)) if company_id is not None else None
        if project_id is None:
            raise RuntimeError(f"Unable to resolve default project for job {job_id}.")
        bind.execute(job_table.update().where(job_table.c.id == job_id).values(project_id=project_id))

    job_indexes = {index["name"] for index in inspector.get_indexes("job")}
    if op.f("ix_job_project_id") not in job_indexes:
        op.create_index(op.f("ix_job_project_id"), "job", ["project_id"], unique=False)

    job_foreign_keys = {fk["name"] for fk in inspector.get_foreign_keys("job")}
    if JOB_PROJECT_FK_NAME not in job_foreign_keys:
        op.create_foreign_key(
            JOB_PROJECT_FK_NAME,
            "job",
            "admin_company_project",
            ["project_id"],
            ["id"],
        )

    job_null_count = bind.execute(
        sa.select(func.count()).select_from(job_table).where(job_table.c.project_id.is_(None))
    ).scalar_one()
    if int(job_null_count or 0) > 0:
        raise RuntimeError("Unable to enforce non-null job.project_id because null values remain.")
    op.alter_column("job", "project_id", existing_type=sa.Integer(), nullable=False)

    contract_columns = {column["name"] for column in inspector.get_columns("contract_record")}
    if "service_customer_project_id" not in contract_columns:
        op.add_column("contract_record", sa.Column("service_customer_project_id", sa.Integer(), nullable=True))
        inspector = sa.inspect(bind)

    contract_rows = bind.execute(
        sa.select(contract_table.c.id, job_table.c.project_id)
        .select_from(contract_table.join(job_table, job_table.c.id == contract_table.c.job_id))
        .where(contract_table.c.service_customer_project_id.is_(None))
    ).all()
    for contract_id, project_id in contract_rows:
        if project_id is None:
            raise RuntimeError(f"Unable to resolve project for contract record {contract_id}.")
        bind.execute(
            contract_table.update()
            .where(contract_table.c.id == contract_id)
            .values(service_customer_project_id=int(project_id))
        )

    contract_indexes = {index["name"] for index in inspector.get_indexes("contract_record")}
    if op.f("ix_contract_record_service_customer_project_id") not in contract_indexes:
        op.create_index(
            op.f("ix_contract_record_service_customer_project_id"),
            "contract_record",
            ["service_customer_project_id"],
            unique=False,
        )

    contract_foreign_keys = {fk["name"] for fk in inspector.get_foreign_keys("contract_record")}
    if CONTRACT_PROJECT_FK_NAME not in contract_foreign_keys:
        op.create_foreign_key(
            CONTRACT_PROJECT_FK_NAME,
            "contract_record",
            "admin_company_project",
            ["service_customer_project_id"],
            ["id"],
        )

    contract_null_count = bind.execute(
        sa.select(func.count())
        .select_from(contract_table)
        .where(contract_table.c.service_customer_project_id.is_(None))
    ).scalar_one()
    if int(contract_null_count or 0) > 0:
        raise RuntimeError(
            "Unable to enforce non-null contract_record.service_customer_project_id because null values remain."
        )
    op.alter_column("contract_record", "service_customer_project_id", existing_type=sa.Integer(), nullable=False)


def downgrade() -> None:
    op.drop_constraint(
        CONTRACT_PROJECT_FK_NAME,
        "contract_record",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_contract_record_service_customer_project_id"), table_name="contract_record")
    op.drop_column("contract_record", "service_customer_project_id")

    op.drop_constraint(JOB_PROJECT_FK_NAME, "job", type_="foreignkey")
    op.drop_index(op.f("ix_job_project_id"), table_name="job")
    op.drop_column("job", "project_id")

    op.drop_index(op.f("ix_admin_company_project_is_deleted"), table_name="admin_company_project")
    op.drop_index(op.f("ix_admin_company_project_name"), table_name="admin_company_project")
    op.drop_index(op.f("ix_admin_company_project_company_id"), table_name="admin_company_project")
    op.drop_table("admin_company_project")
