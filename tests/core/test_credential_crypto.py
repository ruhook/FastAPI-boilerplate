import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from src.app.core.credential_crypto import (
    CredentialConfigurationError,
    CredentialDecryptionError,
    decrypt_credential,
    encrypt_credential,
)

pytestmark = pytest.mark.no_database_cleanup


def test_credential_ciphertext_is_versioned_and_round_trips() -> None:
    key = Fernet.generate_key().decode()

    encrypted = encrypt_credential("smtp-code", key)

    assert encrypted.startswith("v1:")
    assert "smtp-code" not in encrypted
    assert decrypt_credential(encrypted, key) == "smtp-code"


def test_secret_str_key_is_supported() -> None:
    key = SecretStr(Fernet.generate_key().decode())

    encrypted = encrypt_credential("smtp-code", key)

    assert decrypt_credential(encrypted, key) == "smtp-code"


def test_credential_tampering_is_rejected() -> None:
    key = Fernet.generate_key().decode()
    encrypted = encrypt_credential("smtp-code", key)

    with pytest.raises(CredentialDecryptionError, match="decrypt"):
        decrypt_credential(encrypted + "tampered", key)


@pytest.mark.parametrize("value", ["smtp-code", "v2:unsupported"])
def test_unversioned_or_unknown_ciphertext_is_rejected(value: str) -> None:
    key = Fernet.generate_key().decode()

    with pytest.raises(CredentialDecryptionError, match="version"):
        decrypt_credential(value, key)


@pytest.mark.parametrize("key", ["", "not-a-fernet-key"])
def test_empty_or_invalid_encryption_key_is_rejected(key: str) -> None:
    with pytest.raises(CredentialConfigurationError, match="MAIL_CREDENTIAL_ENCRYPTION_KEY"):
        encrypt_credential("smtp-code", key)
