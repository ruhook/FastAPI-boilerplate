import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import settings
from app.core.db.database import Base
from app.modules.admin.admin_audit_log.model import AdminAuditLog
from app.modules.admin.admin_user.model import AdminUser
from app.modules.admin.dictionary.model import AdminDictionary
from app.modules.admin.form_template.model import AdminFormTemplate
from app.modules.admin.internal_notification.model import AdminInternalNotification
from app.modules.admin.mail_account.model import MailAccount
from app.modules.admin.mail_signature.model import MailSignature
from app.modules.admin.mail_task.model import MailTask
from app.modules.admin.mail_template.model import MailTemplate
from app.modules.admin.mail_template_category.model import MailTemplateCategory
from app.modules.admin.role.model import Role
from app.modules.assets.model import Asset
from app.modules.candidate_application.model import CandidateApplication
from app.modules.candidate_application_field_value.model import CandidateApplicationFieldValue
from app.modules.job.model import Job
from app.modules.operation_log.model import OperationLog
from app.modules.talent_profile.model import TalentProfile
from app.modules.talent_profile_merge_log.model import TalentProfileMergeLog
from app.modules.user.model import User

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

config.set_main_option(
    "sqlalchemy.url",
    settings.DATABASE_ASYNC_URL,
)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

REGISTERED_MODELS = (
    AdminUser,
    AdminAuditLog,
    Role,
    AdminDictionary,
    AdminFormTemplate,
    AdminInternalNotification,
    Job,
    MailAccount,
    Asset,
    MailTemplateCategory,
    MailTemplate,
    MailSignature,
    MailTask,
    User,
    CandidateApplication,
    CandidateApplicationFieldValue,
    OperationLog,
    TalentProfile,
    TalentProfileMergeLog,
)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL and not an Engine, though an Engine is acceptable here as well.  By
    skipping the Engine creation we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the script output.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine and associate a connection with the context."""

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
