import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from src.app.core.config import settings


@pytest.fixture(autouse=True)
def configure_mail_credential_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        settings,
        "MAIL_CREDENTIAL_ENCRYPTION_KEY",
        SecretStr(Fernet.generate_key().decode()),
    )
