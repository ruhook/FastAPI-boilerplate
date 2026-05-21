"""add referral bonus model snapshots

Revision ID: 20260520_000032
Revises: 20260518_000031
Create Date: 2026-05-20 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import func

revision: str = "20260520_000032"
down_revision: str | None = "20260518_000031"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


DEFAULT_MODEL_NAME = "Default Referral Bonus"
DEFAULT_CURRENCY = "USD"
DEFAULT_CAP = "300.00"
DEFAULT_MILESTONES = [
    {"required_hours": "40.00", "reward_amount": "25.00"},
    {"required_hours": "100.00", "reward_amount": "50.00"},
    {"required_hours": "180.00", "reward_amount": "75.00"},
    {"required_hours": "300.00", "reward_amount": "150.00"},
]


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "referral_bonus_model",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default=sa.text("'active'")),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default=sa.text("'USD'")),
        sa.Column("reward_cap", sa.Numeric(precision=10, scale=2), nullable=False, server_default=sa.text("0.00")),
        sa.Column("created_by_admin_user_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_admin_user_id", sa.Integer(), nullable=True),
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
        sa.ForeignKeyConstraint(["created_by_admin_user_id"], ["admin_user.id"]),
        sa.ForeignKeyConstraint(["updated_by_admin_user_id"], ["admin_user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_referral_bonus_model_name"), "referral_bonus_model", ["name"], unique=False)
    op.create_index(op.f("ix_referral_bonus_model_status"), "referral_bonus_model", ["status"], unique=False)
    op.create_index(op.f("ix_referral_bonus_model_currency"), "referral_bonus_model", ["currency"], unique=False)
    op.create_index(
        op.f("ix_referral_bonus_model_created_by_admin_user_id"),
        "referral_bonus_model",
        ["created_by_admin_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_referral_bonus_model_updated_by_admin_user_id"),
        "referral_bonus_model",
        ["updated_by_admin_user_id"],
        unique=False,
    )
    op.create_index(op.f("ix_referral_bonus_model_is_deleted"), "referral_bonus_model", ["is_deleted"], unique=False)

    model_table = sa.table(
        "referral_bonus_model",
        sa.column("id", sa.Integer()),
        sa.column("name", sa.String()),
        sa.column("status", sa.String()),
        sa.column("currency", sa.String()),
        sa.column("reward_cap", sa.Numeric()),
        sa.column("data", sa.JSON()),
        sa.column("is_deleted", sa.Boolean()),
    )
    result = bind.execute(
        model_table.insert().values(
            name=DEFAULT_MODEL_NAME,
            status="active",
            currency=DEFAULT_CURRENCY,
            reward_cap=DEFAULT_CAP,
            data={"milestones": DEFAULT_MILESTONES},
        )
    )
    default_model_id = result.inserted_primary_key[0] if result.inserted_primary_key else None
    if default_model_id is None:
        default_model_id = bind.execute(
            sa.select(model_table.c.id)
            .where(model_table.c.name == DEFAULT_MODEL_NAME, model_table.c.is_deleted.is_(False))
            .order_by(model_table.c.id.desc())
            .limit(1)
        ).scalar_one()
    default_model_id = int(default_model_id)

    op.add_column("job", sa.Column("referral_bonus_model_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_job_referral_bonus_model_id"), "job", ["referral_bonus_model_id"], unique=False)
    op.create_foreign_key(
        "fk_job_referral_bonus_model_id",
        "job",
        "referral_bonus_model",
        ["referral_bonus_model_id"],
        ["id"],
    )
    job_table = sa.table(
        "job",
        sa.column("id", sa.Integer()),
        sa.column("referral_bonus_model_id", sa.Integer()),
    )
    bind.execute(
        job_table.update()
        .where(job_table.c.referral_bonus_model_id.is_(None))
        .values(referral_bonus_model_id=default_model_id)
    )
    null_count = bind.execute(
        sa.select(func.count()).select_from(job_table).where(job_table.c.referral_bonus_model_id.is_(None))
    ).scalar_one()
    if int(null_count or 0) > 0:
        raise RuntimeError("Unable to enforce non-null job.referral_bonus_model_id because null values remain.")
    op.alter_column("job", "referral_bonus_model_id", existing_type=sa.Integer(), nullable=False)

    op.create_table(
        "user_referral_profile",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("referral_bonus_model_id", sa.Integer(), nullable=False),
        sa.Column("source_job_id", sa.Integer(), nullable=True),
        sa.Column("source_contract_record_id", sa.Integer(), nullable=True),
        sa.Column("model_snapshot_name", sa.String(length=120), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default=sa.text("'USD'")),
        sa.Column("reward_cap", sa.Numeric(precision=10, scale=2), nullable=False, server_default=sa.text("0.00")),
        sa.Column(
            "locked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("current_timestamp(0)")
        ),
        sa.Column("created_by_admin_user_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_admin_user_id", sa.Integer(), nullable=True),
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
        sa.ForeignKeyConstraint(["created_by_admin_user_id"], ["admin_user.id"]),
        sa.ForeignKeyConstraint(["referral_bonus_model_id"], ["referral_bonus_model.id"]),
        sa.ForeignKeyConstraint(["source_contract_record_id"], ["contract_record.id"]),
        sa.ForeignKeyConstraint(["source_job_id"], ["job.id"]),
        sa.ForeignKeyConstraint(["updated_by_admin_user_id"], ["admin_user.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index(op.f("ix_user_referral_profile_user_id"), "user_referral_profile", ["user_id"], unique=False)
    op.create_index(
        op.f("ix_user_referral_profile_referral_bonus_model_id"),
        "user_referral_profile",
        ["referral_bonus_model_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_referral_profile_source_job_id"), "user_referral_profile", ["source_job_id"], unique=False
    )
    op.create_index(
        op.f("ix_user_referral_profile_source_contract_record_id"),
        "user_referral_profile",
        ["source_contract_record_id"],
        unique=False,
    )
    op.create_index(op.f("ix_user_referral_profile_currency"), "user_referral_profile", ["currency"], unique=False)
    op.create_index(
        op.f("ix_user_referral_profile_created_by_admin_user_id"),
        "user_referral_profile",
        ["created_by_admin_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_referral_profile_updated_by_admin_user_id"),
        "user_referral_profile",
        ["updated_by_admin_user_id"],
        unique=False,
    )
    op.create_index(op.f("ix_user_referral_profile_is_deleted"), "user_referral_profile", ["is_deleted"], unique=False)

    op.add_column("referral_record", sa.Column("referral_bonus_model_id", sa.Integer(), nullable=True))
    op.add_column("referral_record", sa.Column("model_snapshot_name", sa.String(length=120), nullable=True))
    op.add_column(
        "referral_record", sa.Column("currency", sa.String(length=8), nullable=False, server_default=sa.text("'USD'"))
    )
    op.add_column(
        "referral_record",
        sa.Column("reward_cap", sa.Numeric(precision=10, scale=2), nullable=False, server_default=sa.text("0.00")),
    )
    op.create_foreign_key(
        "fk_referral_record_referral_bonus_model_id",
        "referral_record",
        "referral_bonus_model",
        ["referral_bonus_model_id"],
        ["id"],
    )
    op.create_index(
        op.f("ix_referral_record_referral_bonus_model_id"), "referral_record", ["referral_bonus_model_id"], unique=False
    )
    op.create_index(op.f("ix_referral_record_currency"), "referral_record", ["currency"], unique=False)

    referral_table = sa.table(
        "referral_record",
        sa.column("id", sa.Integer()),
        sa.column("referral_bonus_model_id", sa.Integer()),
        sa.column("model_snapshot_name", sa.String()),
        sa.column("currency", sa.String()),
        sa.column("reward_cap", sa.Numeric()),
        sa.column("data", sa.JSON()),
    )
    referral_rows = bind.execute(sa.select(referral_table.c.id, referral_table.c.data)).all()
    for referral_record_id, referral_data in referral_rows:
        existing_data = referral_data if isinstance(referral_data, dict) else {}
        bind.execute(
            referral_table.update()
            .where(referral_table.c.id == referral_record_id)
            .values(
                referral_bonus_model_id=default_model_id,
                model_snapshot_name=DEFAULT_MODEL_NAME,
                currency=DEFAULT_CURRENCY,
                reward_cap=DEFAULT_CAP,
                data={**existing_data, "milestones": DEFAULT_MILESTONES},
            )
        )
    op.alter_column("referral_record", "referral_bonus_model_id", existing_type=sa.Integer(), nullable=False)
    op.alter_column("referral_record", "model_snapshot_name", existing_type=sa.String(length=120), nullable=False)

    profile_table = sa.table(
        "user_referral_profile",
        sa.column("user_id", sa.Integer()),
        sa.column("referral_bonus_model_id", sa.Integer()),
        sa.column("source_job_id", sa.Integer()),
        sa.column("source_contract_record_id", sa.Integer()),
        sa.column("model_snapshot_name", sa.String()),
        sa.column("currency", sa.String()),
        sa.column("reward_cap", sa.Numeric()),
        sa.column("data", sa.JSON()),
    )
    contract_table = sa.table(
        "contract_record",
        sa.column("id", sa.Integer()),
        sa.column("user_id", sa.Integer()),
        sa.column("job_id", sa.Integer()),
        sa.column("contract_status", sa.String()),
        sa.column("is_deleted", sa.Boolean()),
    )
    active_rows = bind.execute(
        sa.select(contract_table.c.user_id, contract_table.c.job_id, contract_table.c.id)
        .where(contract_table.c.contract_status == "Active", contract_table.c.is_deleted.is_(False))
        .order_by(contract_table.c.id.asc())
    ).all()
    seen_user_ids: set[int] = set()
    for user_id, job_id, contract_record_id in active_rows:
        normalized_user_id = int(user_id)
        if normalized_user_id in seen_user_ids:
            continue
        seen_user_ids.add(normalized_user_id)
        bind.execute(
            profile_table.insert().values(
                user_id=normalized_user_id,
                referral_bonus_model_id=default_model_id,
                source_job_id=int(job_id) if job_id is not None else None,
                source_contract_record_id=int(contract_record_id) if contract_record_id is not None else None,
                model_snapshot_name=DEFAULT_MODEL_NAME,
                currency=DEFAULT_CURRENCY,
                reward_cap=DEFAULT_CAP,
                data={"milestones": DEFAULT_MILESTONES},
            )
        )


def downgrade() -> None:
    op.drop_index(op.f("ix_referral_record_currency"), table_name="referral_record")
    op.drop_index(op.f("ix_referral_record_referral_bonus_model_id"), table_name="referral_record")
    op.drop_constraint("fk_referral_record_referral_bonus_model_id", "referral_record", type_="foreignkey")
    op.drop_column("referral_record", "reward_cap")
    op.drop_column("referral_record", "currency")
    op.drop_column("referral_record", "model_snapshot_name")
    op.drop_column("referral_record", "referral_bonus_model_id")

    op.drop_index(op.f("ix_user_referral_profile_is_deleted"), table_name="user_referral_profile")
    op.drop_index(op.f("ix_user_referral_profile_updated_by_admin_user_id"), table_name="user_referral_profile")
    op.drop_index(op.f("ix_user_referral_profile_created_by_admin_user_id"), table_name="user_referral_profile")
    op.drop_index(op.f("ix_user_referral_profile_currency"), table_name="user_referral_profile")
    op.drop_index(op.f("ix_user_referral_profile_source_contract_record_id"), table_name="user_referral_profile")
    op.drop_index(op.f("ix_user_referral_profile_source_job_id"), table_name="user_referral_profile")
    op.drop_index(op.f("ix_user_referral_profile_referral_bonus_model_id"), table_name="user_referral_profile")
    op.drop_index(op.f("ix_user_referral_profile_user_id"), table_name="user_referral_profile")
    op.drop_table("user_referral_profile")

    op.drop_constraint("fk_job_referral_bonus_model_id", "job", type_="foreignkey")
    op.drop_index(op.f("ix_job_referral_bonus_model_id"), table_name="job")
    op.drop_column("job", "referral_bonus_model_id")

    op.drop_index(op.f("ix_referral_bonus_model_is_deleted"), table_name="referral_bonus_model")
    op.drop_index(op.f("ix_referral_bonus_model_updated_by_admin_user_id"), table_name="referral_bonus_model")
    op.drop_index(op.f("ix_referral_bonus_model_created_by_admin_user_id"), table_name="referral_bonus_model")
    op.drop_index(op.f("ix_referral_bonus_model_currency"), table_name="referral_bonus_model")
    op.drop_index(op.f("ix_referral_bonus_model_status"), table_name="referral_bonus_model")
    op.drop_index(op.f("ix_referral_bonus_model_name"), table_name="referral_bonus_model")
    op.drop_table("referral_bonus_model")
