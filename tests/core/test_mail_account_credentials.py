from datetime import UTC, datetime

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from src.app.core.config import settings
from src.app.core.credential_crypto import CredentialDecryptionError, decrypt_credential, encrypt_credential
from src.app.modules.admin.mail_account import service as mail_account_service
from src.app.modules.admin.mail_account.model import MailAccount
from src.app.modules.admin.mail_account.schema import MailAccountCreate, MailAccountUpdate
from src.app.modules.admin.mail_account.service import (
    create_mail_account,
    resolve_mail_account_auth_secret,
    serialize_mail_account,
    update_mail_account,
)

pytestmark = pytest.mark.no_database_cleanup


class EmptyResult:
    def scalar_one_or_none(self) -> None:
        return None


class FakeSession:
    def __init__(self) -> None:
        self.added: MailAccount | None = None

    async def execute(self, statement: object) -> EmptyResult:
        return EmptyResult()

    def add(self, account: MailAccount) -> None:
        self.added = account

    async def flush(self) -> None:
        if self.added is not None:
            self.added.id = 11
            self.added.created_at = datetime.now(UTC)

    async def refresh(self, account: MailAccount) -> None:
        return None


def build_account(
    *,
    auth_secret: str | None,
    auth_secret_encrypted: str | None,
) -> MailAccount:
    account = MailAccount(
        admin_user_id=1,
        email="mailbox@example.com",
        provider="qq",
        smtp_username="mailbox@example.com",
        smtp_host="smtp.qq.com",
        smtp_port=465,
        security_mode="ssl",
        auth_secret=auth_secret,
        status="enabled",
        note=None,
        data={},
    )
    account.id = 10
    account.created_at = datetime.now(UTC)
    account.auth_secret_encrypted = auth_secret_encrypted
    return account


@pytest.mark.parametrize(
    ("legacy", "encrypted", "expected"),
    [
        ("legacy-secret", None, True),
        (None, "v1:ciphertext", True),
        (None, None, False),
    ],
)
def test_serialized_mail_account_only_exposes_secret_presence(
    legacy: str | None,
    encrypted: str | None,
    expected: bool,
) -> None:
    serialized = serialize_mail_account(build_account(auth_secret=legacy, auth_secret_encrypted=encrypted))

    assert serialized["has_auth_secret"] is expected
    assert "auth_secret" not in serialized
    assert "auth_secret_encrypted" not in serialized


def test_resolver_prefers_encrypted_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "MAIL_CREDENTIAL_ENCRYPTION_KEY", SecretStr(key))
    account = build_account(
        auth_secret="stale-legacy-secret",
        auth_secret_encrypted=encrypt_credential("encrypted-secret", key),
    )

    assert resolve_mail_account_auth_secret(account) == "encrypted-secret"


def test_resolver_temporarily_supports_legacy_plaintext() -> None:
    account = build_account(auth_secret="legacy-secret", auth_secret_encrypted=None)

    assert resolve_mail_account_auth_secret(account) == "legacy-secret"


def test_resolver_never_falls_back_when_encrypted_value_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings,
        "MAIL_CREDENTIAL_ENCRYPTION_KEY",
        SecretStr(Fernet.generate_key().decode()),
    )
    account = build_account(
        auth_secret="legacy-secret",
        auth_secret_encrypted="v1:tampered",
    )

    with pytest.raises(CredentialDecryptionError):
        resolve_mail_account_auth_secret(account)


def test_resolver_rejects_account_without_credentials() -> None:
    account = build_account(auth_secret=None, auth_secret_encrypted=None)

    with pytest.raises(ValueError, match="not configured"):
        resolve_mail_account_auth_secret(account)


@pytest.mark.asyncio
async def test_create_service_only_persists_encrypted_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "MAIL_CREDENTIAL_ENCRYPTION_KEY", SecretStr(key))

    async def ignore_audit_log(**kwargs: object) -> None:
        return None

    monkeypatch.setattr(mail_account_service, "create_admin_audit_log", ignore_audit_log)
    session = FakeSession()

    response = await create_mail_account(
        MailAccountCreate(
            email="mailbox@example.com",
            provider="qq",
            auth_secret="smtp-secret",
        ),
        session,  # type: ignore[arg-type]
        admin_user_id=1,
    )

    assert session.added is not None
    assert session.added.auth_secret is None
    assert session.added.auth_secret_encrypted.startswith("v1:")
    assert "smtp-secret" not in session.added.auth_secret_encrypted
    assert response["has_auth_secret"] is True
    assert "auth_secret" not in response


@pytest.mark.asyncio
async def test_update_service_replaces_legacy_secret_with_ciphertext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "MAIL_CREDENTIAL_ENCRYPTION_KEY", SecretStr(key))
    account = build_account(auth_secret="legacy-secret", auth_secret_encrypted=None)

    async def return_account(*args: object, **kwargs: object) -> MailAccount:
        return account

    async def ignore_audit_log(**kwargs: object) -> None:
        return None

    monkeypatch.setattr(mail_account_service, "get_mail_account_model", return_account)
    monkeypatch.setattr(mail_account_service, "create_admin_audit_log", ignore_audit_log)
    session = FakeSession()

    response = await update_mail_account(
        account.id,
        MailAccountUpdate(auth_secret="replacement-secret"),
        session,  # type: ignore[arg-type]
        admin_user_id=1,
    )

    assert account.auth_secret is None
    assert account.auth_secret_encrypted.startswith("v1:")
    assert decrypt_credential(account.auth_secret_encrypted, key) == "replacement-secret"
    assert response["has_auth_secret"] is True
    assert "auth_secret" not in response
