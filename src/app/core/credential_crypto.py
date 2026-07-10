import base64
import binascii

from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr

from .config import settings

_CIPHERTEXT_VERSION = "v1"


class CredentialConfigurationError(ValueError):
    """Raised when credential encryption is not configured safely."""


class CredentialDecryptionError(ValueError):
    """Raised when stored credential ciphertext cannot be authenticated."""


def _resolve_fernet(key: SecretStr | str | None) -> Fernet:
    configured_key = settings.MAIL_CREDENTIAL_ENCRYPTION_KEY if key is None else key
    raw_key = configured_key.get_secret_value() if isinstance(configured_key, SecretStr) else configured_key
    if not raw_key or not raw_key.strip():
        raise CredentialConfigurationError("MAIL_CREDENTIAL_ENCRYPTION_KEY is required for credential encryption.")
    try:
        return Fernet(raw_key.strip().encode())
    except (TypeError, ValueError):
        raise CredentialConfigurationError(
            "MAIL_CREDENTIAL_ENCRYPTION_KEY must be a valid URL-safe base64-encoded 32-byte Fernet key."
        ) from None


def encrypt_credential(secret: str, key: SecretStr | str | None = None) -> str:
    token = _resolve_fernet(key).encrypt(secret.encode()).decode()
    return f"{_CIPHERTEXT_VERSION}:{token}"


def decrypt_credential(value: str, key: SecretStr | str | None = None) -> str:
    version, separator, token = value.partition(":")
    if not separator or version != _CIPHERTEXT_VERSION:
        raise CredentialDecryptionError("Unsupported credential ciphertext version.")
    try:
        decoded_token = base64.b64decode(token, altchars=b"-_", validate=True)
        if base64.urlsafe_b64encode(decoded_token).decode() != token:
            raise CredentialDecryptionError("Stored credential could not be decrypted.")
        return _resolve_fernet(key).decrypt(token.encode()).decode()
    except (binascii.Error, InvalidToken, UnicodeDecodeError):
        raise CredentialDecryptionError("Stored credential could not be decrypted.") from None
