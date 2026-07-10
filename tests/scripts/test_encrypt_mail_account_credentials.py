import pytest

from src.app.modules.admin.mail_account.model import MailAccount
from src.scripts.encrypt_mail_account_credentials import (
    encrypt_mail_account_credentials,
    format_migration_summary,
)

pytestmark = pytest.mark.no_database_cleanup


def build_account(*, plaintext: str | None, encrypted: str | None = None) -> MailAccount:
    account = MailAccount()
    account.auth_secret = plaintext
    account.auth_secret_encrypted = encrypted
    return account


def test_encrypts_plaintext_and_clears_legacy_column_without_returning_secrets() -> None:
    plaintext = "smtp-secret-that-must-not-leak"
    account = build_account(plaintext=plaintext)

    summary = encrypt_mail_account_credentials(
        [account],
        encrypt=lambda value: f"v1:encrypted-length-{len(value)}",
    )

    assert summary == {"migrated": 1, "skipped": 0}
    assert account.auth_secret is None
    assert account.auth_secret_encrypted == f"v1:encrypted-length-{len(plaintext)}"
    assert plaintext not in repr(summary)
    assert plaintext not in format_migration_summary(summary)


def test_skips_already_encrypted_and_empty_accounts() -> None:
    already_encrypted = build_account(plaintext="stale", encrypted="v1:existing")
    empty = build_account(plaintext=None)

    summary = encrypt_mail_account_credentials(
        [already_encrypted, empty],
        encrypt=lambda value: f"v1:{value}",
    )

    assert summary == {"migrated": 0, "skipped": 2}
    assert already_encrypted.auth_secret == "stale"
    assert already_encrypted.auth_secret_encrypted == "v1:existing"


def test_encryption_failure_does_not_clear_plaintext() -> None:
    account = build_account(plaintext="smtp-secret")

    def fail_encryption(value: str) -> str:
        raise ValueError("encryption failed")

    with pytest.raises(ValueError, match="encryption failed"):
        encrypt_mail_account_credentials([account], encrypt=fail_encryption)

    assert account.auth_secret == "smtp-secret"
    assert account.auth_secret_encrypted is None


def test_summary_output_contains_counts_only() -> None:
    assert format_migration_summary({"migrated": 2, "skipped": 3}) == "migrated=2 skipped=3"
