import asyncio
from collections.abc import Callable, Iterable

from sqlalchemy import func, or_, select

from ..app.core.credential_crypto import encrypt_credential
from ..app.core.db.database import async_engine, local_session
from ..app.modules.admin.mail_account.model import MailAccount


def encrypt_mail_account_credentials(
    accounts: Iterable[MailAccount],
    *,
    encrypt: Callable[[str], str] = encrypt_credential,
) -> dict[str, int]:
    migrated = 0
    skipped = 0
    for account in accounts:
        if (account.auth_secret_encrypted or "").strip() or not (account.auth_secret or "").strip():
            skipped += 1
            continue

        encrypted = encrypt(account.auth_secret or "")
        account.auth_secret_encrypted = encrypted
        account.auth_secret = None
        migrated += 1

    return {"migrated": migrated, "skipped": skipped}


def format_migration_summary(summary: dict[str, int]) -> str:
    return f"migrated={summary['migrated']} skipped={summary['skipped']}"


async def migrate_stored_mail_account_credentials() -> dict[str, int]:
    async with local_session() as session:
        result = await session.execute(
            select(MailAccount).where(
                MailAccount.auth_secret.is_not(None),
                func.trim(MailAccount.auth_secret) != "",
                or_(
                    MailAccount.auth_secret_encrypted.is_(None),
                    func.trim(MailAccount.auth_secret_encrypted) == "",
                ),
            )
        )
        summary = encrypt_mail_account_credentials(result.scalars().all())
        await session.commit()
        return summary


async def main() -> None:
    try:
        summary = await migrate_stored_mail_account_credentials()
        print(format_migration_summary(summary))
    finally:
        await async_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
