import pytest
from fastapi import APIRouter
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from src.app.admin.local_admin_bootstrap import should_ensure_local_admin
from src.app.core.config import EnvironmentOption, Settings
from src.app.core.config import settings as runtime_settings
from src.app.core.setup import create_application
from src.app.modules.admin.admin_user.service import is_local_dev_auto_login_admin

pytestmark = pytest.mark.no_database_cleanup


VALID_FERNET_KEY = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="


def production_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "ENVIRONMENT": EnvironmentOption.PRODUCTION,
        "SECRET_KEY": "production-secret-with-at-least-32-characters",
        "CORS_ORIGINS": ["https://admin.example.com"],
        "CORS_ALLOW_CREDENTIALS": True,
        "ENABLE_LOCAL_AUTH_BYPASS": False,
        "ENABLE_LOCAL_ADMIN_BOOTSTRAP": False,
        "CANDIDATE_REGISTER_VERIFICATION_ENABLED": False,
        "ASSET_STORAGE_PROVIDER": "local",
        "MAIL_CREDENTIAL_ENCRYPTION_KEY": VALID_FERNET_KEY,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_production_accepts_explicit_safe_security_configuration() -> None:
    configured = production_settings()

    assert configured.ENVIRONMENT == EnvironmentOption.PRODUCTION


def test_production_rejects_placeholder_secret_key() -> None:
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        production_settings(SECRET_KEY="secret-key")


def test_production_rejects_credentialed_wildcard_cors() -> None:
    with pytest.raises(ValidationError, match="CORS"):
        production_settings(CORS_ORIGINS=["*"], CORS_ALLOW_CREDENTIALS=True)


def test_application_honors_explicit_cors_credentials_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("src.app.core.setup.init_logging", lambda service_name=None: None)
    configured = Settings(
        _env_file=None,
        ENVIRONMENT="local",
        CORS_ORIGINS=["https://admin.example.com"],
        CORS_ALLOW_CREDENTIALS=False,
    )

    application = create_application(APIRouter(), configured)
    cors = next(middleware for middleware in application.user_middleware if middleware.cls is CORSMiddleware)

    assert cors.kwargs["allow_origins"] == ["https://admin.example.com"]
    assert cors.kwargs["allow_origin_regex"] is None
    assert cors.kwargs["allow_credentials"] is False


def test_application_uses_plain_wildcard_cors_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("src.app.core.setup.init_logging", lambda service_name=None: None)
    configured = Settings(
        _env_file=None,
        ENVIRONMENT="local",
        CORS_ORIGINS=["*"],
        CORS_ALLOW_CREDENTIALS=False,
    )

    application = create_application(APIRouter(), configured)
    cors = next(middleware for middleware in application.user_middleware if middleware.cls is CORSMiddleware)

    assert cors.kwargs["allow_origins"] == ["*"]
    assert cors.kwargs["allow_origin_regex"] is None
    assert cors.kwargs["allow_credentials"] is False


def test_production_rejects_local_admin_bootstrap() -> None:
    with pytest.raises(ValidationError, match="ENABLE_LOCAL_ADMIN_BOOTSTRAP"):
        production_settings(ENABLE_LOCAL_ADMIN_BOOTSTRAP=True)


def test_production_rejects_local_auth_bypass() -> None:
    with pytest.raises(ValidationError, match="ENABLE_LOCAL_AUTH_BYPASS"):
        production_settings(ENABLE_LOCAL_AUTH_BYPASS=True)


def test_central_auth_rate_limit_settings_are_not_part_of_runtime_config() -> None:
    configured = Settings(_env_file=None, ENVIRONMENT="local")

    removed_names = {
        "AUTH_RATE_LIMIT_PREFIX",
        "AUTH_LOGIN_WINDOW_SECONDS",
        "AUTH_LOGIN_IP_LIMIT",
        "AUTH_LOGIN_IDENTIFIER_LIMIT",
        "AUTH_LOGIN_PAIR_LIMIT",
        "AUTH_VERIFICATION_SEND_WINDOW_SECONDS",
        "AUTH_VERIFICATION_SEND_IP_LIMIT",
        "AUTH_VERIFICATION_SEND_IDENTIFIER_LIMIT",
        "AUTH_VERIFICATION_CHECK_WINDOW_SECONDS",
        "AUTH_VERIFICATION_CHECK_IP_LIMIT",
        "AUTH_VERIFICATION_CHECK_IDENTIFIER_LIMIT",
    }

    assert removed_names.isdisjoint(type(configured).model_fields)


@pytest.mark.parametrize(
    "setting_name",
    [
        "EVENT_CONSUMER_CONCURRENCY",
        "EVENT_CONSUMER_BUFFER_SIZE",
        "EVENT_CONSUMER_MAX_DELIVERIES",
        "EVENT_CONSUMER_SHUTDOWN_TIMEOUT_SECONDS",
        "EVENT_PENDING_IDLE_MS",
        "EVENT_DEAD_LETTER_MAXLEN",
        "EVENT_DEAD_LETTER_RAW_MAX_CHARS",
        "EVENT_DEAD_LETTER_ERROR_MAX_CHARS",
    ],
)
def test_event_consumer_limits_must_be_positive(setting_name: str) -> None:
    with pytest.raises(ValidationError, match=setting_name):
        Settings(_env_file=None, ENVIRONMENT="local", **{setting_name: 0})


def test_event_shutdown_timeout_accepts_positive_subsecond_value() -> None:
    configured = Settings(
        _env_file=None,
        ENVIRONMENT="local",
        EVENT_CONSUMER_SHUTDOWN_TIMEOUT_SECONDS=0.5,
    )

    assert configured.EVENT_CONSUMER_SHUTDOWN_TIMEOUT_SECONDS == 0.5


@pytest.mark.parametrize(
    "setting_name",
    [
        "HEALTH_CHECK_TIMEOUT_SECONDS",
        "REDIS_CONNECT_TIMEOUT_SECONDS",
        "REDIS_SOCKET_TIMEOUT_SECONDS",
        "MAIL_TASK_PROCESSING_LEASE_SECONDS",
        "MAIL_TASK_RECOVERY_INTERVAL_SECONDS",
        "MAIL_TASK_RECOVERY_BATCH_SIZE",
    ],
)
def test_foundation_runtime_limits_must_be_positive(setting_name: str) -> None:
    with pytest.raises(ValidationError, match=setting_name):
        Settings(_env_file=None, ENVIRONMENT="local", **{setting_name: 0})


def test_production_requires_mail_credential_encryption_key() -> None:
    with pytest.raises(ValidationError, match="MAIL_CREDENTIAL_ENCRYPTION_KEY"):
        production_settings(MAIL_CREDENTIAL_ENCRYPTION_KEY="")


def test_production_rejects_malformed_mail_credential_encryption_key() -> None:
    with pytest.raises(ValidationError, match="MAIL_CREDENTIAL_ENCRYPTION_KEY"):
        production_settings(MAIL_CREDENTIAL_ENCRYPTION_KEY="not-a-fernet-key")


def test_local_admin_bootstrap_is_opt_in() -> None:
    configured = Settings(_env_file=None, ENVIRONMENT="local")

    assert configured.ENABLE_LOCAL_AUTH_BYPASS is False
    assert configured.ENABLE_LOCAL_ADMIN_BOOTSTRAP is False
    assert should_ensure_local_admin(configured) is False


def test_local_admin_bootstrap_can_be_explicitly_enabled_locally() -> None:
    configured = Settings(
        _env_file=None,
        ENVIRONMENT="local",
        ENABLE_LOCAL_ADMIN_BOOTSTRAP=True,
    )

    assert should_ensure_local_admin(configured) is True


def test_local_auth_bypass_can_be_explicitly_enabled_locally() -> None:
    configured = Settings(
        _env_file=None,
        ENVIRONMENT="local",
        ENABLE_LOCAL_AUTH_BYPASS=True,
    )

    assert configured.ENABLE_LOCAL_AUTH_BYPASS is True


def test_local_auth_bypass_predicate_requires_explicit_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime_settings, "ENVIRONMENT", EnvironmentOption.LOCAL)
    monkeypatch.setattr(runtime_settings, "ENABLE_LOCAL_AUTH_BYPASS", False, raising=False)

    assert is_local_dev_auto_login_admin("HaokangImport") is False

    monkeypatch.setattr(runtime_settings, "ENABLE_LOCAL_AUTH_BYPASS", True, raising=False)

    assert is_local_dev_auto_login_admin("HaokangImport") is True


def test_local_auth_bypass_predicate_never_matches_outside_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime_settings, "ENVIRONMENT", EnvironmentOption.STAGING)
    monkeypatch.setattr(runtime_settings, "ENABLE_LOCAL_AUTH_BYPASS", True, raising=False)

    assert is_local_dev_auto_login_admin("HaokangImport") is False


def test_local_admin_bootstrap_never_runs_in_production() -> None:
    configured = production_settings()

    assert should_ensure_local_admin(configured) is False
