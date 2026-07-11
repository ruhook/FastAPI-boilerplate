import os
from enum import Enum

from cryptography.fernet import Fernet
from pydantic import SecretStr, computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    APP_NAME: str = "FastAPI app"
    APP_DESCRIPTION: str | None = None
    APP_VERSION: str | None = None
    LICENSE_NAME: str | None = None
    CONTACT_NAME: str | None = None
    CONTACT_EMAIL: str | None = None


class CryptSettings(BaseSettings):
    SECRET_KEY: SecretStr = SecretStr("secret-key")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 15
    ADMIN_ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    ADMIN_REFRESH_TOKEN_EXPIRE_DAYS: int = 30


class FileLoggerSettings(BaseSettings):
    FILE_LOG_DIR: str | None = None
    FILE_LOG_FILENAME: str = "app.log"
    FILE_LOG_MAX_BYTES: int = 10 * 1024 * 1024
    FILE_LOG_BACKUP_COUNT: int = 5
    FILE_LOG_FORMAT_JSON: bool = True
    FILE_LOG_LEVEL: str = "INFO"

    # Include request ID, path, method, client host, and status code in the file log
    FILE_LOG_INCLUDE_REQUEST_ID: bool = True
    FILE_LOG_INCLUDE_PATH: bool = True
    FILE_LOG_INCLUDE_METHOD: bool = True
    FILE_LOG_INCLUDE_CLIENT_HOST: bool = True
    FILE_LOG_INCLUDE_STATUS_CODE: bool = True


class ConsoleLoggerSettings(BaseSettings):
    CONSOLE_LOG_LEVEL: str = "INFO"
    CONSOLE_LOG_FORMAT_JSON: bool = False

    # Include request ID, path, method, client host, and status code in the console log
    CONSOLE_LOG_INCLUDE_REQUEST_ID: bool = False
    CONSOLE_LOG_INCLUDE_PATH: bool = False
    CONSOLE_LOG_INCLUDE_METHOD: bool = False
    CONSOLE_LOG_INCLUDE_CLIENT_HOST: bool = False
    CONSOLE_LOG_INCLUDE_STATUS_CODE: bool = False


class DatabaseSettings(BaseSettings):
    DATABASE_BACKEND: str = "mysql"
    DATABASE_POOL_PRE_PING: bool = True
    DATABASE_POOL_RECYCLE_SECONDS: int = 1800


class SQLiteSettings(DatabaseSettings):
    SQLITE_URI: str = "./sql_app.db"
    SQLITE_SYNC_PREFIX: str = "sqlite:///"
    SQLITE_ASYNC_PREFIX: str = "sqlite+aiosqlite:///"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def SQLITE_SYNC_URL(self) -> str:
        return f"{self.SQLITE_SYNC_PREFIX}{self.SQLITE_URI}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def SQLITE_ASYNC_URL(self) -> str:
        return f"{self.SQLITE_ASYNC_PREFIX}{self.SQLITE_URI}"


class MySQLSettings(DatabaseSettings):
    MYSQL_USER: str = "username"
    MYSQL_PASSWORD: str = "password"
    MYSQL_SERVER: str = "localhost"
    MYSQL_PORT: int = 3306
    MYSQL_DB: str = "dbname"
    MYSQL_SYNC_PREFIX: str = "mysql+pymysql://"
    MYSQL_ASYNC_PREFIX: str = "mysql+aiomysql://"
    MYSQL_URL: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def MYSQL_URI(self) -> str:
        if self.MYSQL_URL:
            return self.MYSQL_URL
        credentials = f"{self.MYSQL_USER}:{self.MYSQL_PASSWORD}"
        location = f"{self.MYSQL_SERVER}:{self.MYSQL_PORT}/{self.MYSQL_DB}"
        return f"{credentials}@{location}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def MYSQL_SYNC_URL(self) -> str:
        return f"{self.MYSQL_SYNC_PREFIX}{self.MYSQL_URI}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def MYSQL_ASYNC_URL(self) -> str:
        return f"{self.MYSQL_ASYNC_PREFIX}{self.MYSQL_URI}"


class PostgresSettings(DatabaseSettings):
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_SERVER: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "postgres"
    POSTGRES_SYNC_PREFIX: str = "postgresql://"
    POSTGRES_ASYNC_PREFIX: str = "postgresql+asyncpg://"
    POSTGRES_URL: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def POSTGRES_URI(self) -> str:
        if self.POSTGRES_URL:
            return self.POSTGRES_URL
        credentials = f"{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
        location = f"{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        return f"{credentials}@{location}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def POSTGRES_SYNC_URL(self) -> str:
        return f"{self.POSTGRES_SYNC_PREFIX}{self.POSTGRES_URI}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def POSTGRES_ASYNC_URL(self) -> str:
        return f"{self.POSTGRES_ASYNC_PREFIX}{self.POSTGRES_URI}"


class TestSettings(BaseSettings):
    ALLOW_TEST_DATABASE_CLEANUP: bool = False
    TEST_DATABASE_NAME_ALLOWLIST: str = "hr_server"


class RedisCacheSettings(BaseSettings):
    REDIS_CACHE_HOST: str = "localhost"
    REDIS_CACHE_PORT: int = 6379
    REDIS_CONNECT_TIMEOUT_SECONDS: float = 2.0
    REDIS_SOCKET_TIMEOUT_SECONDS: float = 2.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def REDIS_CACHE_URL(self) -> str:
        return f"redis://{self.REDIS_CACHE_HOST}:{self.REDIS_CACHE_PORT}"


class EventSettings(BaseSettings):
    EVENT_QUEUE_PREFIX: str = "hr-mq:"
    EVENT_CONSUMER_GROUP: str = "hr_event_consumer"
    EVENT_CONSUMER_CONCURRENCY: int = 3
    EVENT_CONSUMER_BUFFER_SIZE: int = 12
    EVENT_CONSUMER_MAX_DELIVERIES: int = 5
    EVENT_CONSUMER_SHUTDOWN_TIMEOUT_SECONDS: float = 10.0
    EVENT_STATS_INTERVAL: int = 30
    EVENT_PENDING_IDLE_MS: int = 60_000
    EVENT_DEAD_LETTER_MAXLEN: int = 10_000
    EVENT_DEAD_LETTER_RAW_MAX_CHARS: int = 4_000
    EVENT_DEAD_LETTER_ERROR_MAX_CHARS: int = 500
    EVENT_OUTBOX_BATCH_SIZE: int = 50
    EVENT_OUTBOX_LEASE_SECONDS: int = 60
    EVENT_OUTBOX_POLL_SECONDS: float = 1.0


class MailDeliverySettings(BaseSettings):
    MAIL_DELIVERY_MODE: str | None = None


class MailCredentialSettings(BaseSettings):
    MAIL_CREDENTIAL_ENCRYPTION_KEY: SecretStr = SecretStr("")


class MailTaskRecoverySettings(BaseSettings):
    MAIL_TASK_PROCESSING_LEASE_SECONDS: int = 120
    MAIL_TASK_RECOVERY_INTERVAL_SECONDS: float = 30.0
    MAIL_TASK_RECOVERY_BATCH_SIZE: int = 50


class HealthSettings(BaseSettings):
    HEALTH_CHECK_TIMEOUT_SECONDS: float = 2.0


class LocalDevelopmentSettings(BaseSettings):
    ENABLE_LOCAL_AUTH_BYPASS: bool = False
    ENABLE_LOCAL_ADMIN_BOOTSTRAP: bool = False


class CandidateRegisterVerificationSettings(BaseSettings):
    CANDIDATE_WEB_BASE_URL: str = "http://localhost:3002"
    CANDIDATE_REGISTER_VERIFICATION_ENABLED: bool = True
    CANDIDATE_REGISTER_VERIFICATION_SENDER_NAME: str = "T-Maxx Recruit"
    CANDIDATE_REGISTER_VERIFICATION_SENDER_EMAIL: str = ""
    CANDIDATE_REGISTER_VERIFICATION_SMTP_USERNAME: str = ""
    CANDIDATE_REGISTER_VERIFICATION_SMTP_HOST: str = ""
    CANDIDATE_REGISTER_VERIFICATION_SMTP_PORT: int = 465
    CANDIDATE_REGISTER_VERIFICATION_SMTP_SECURITY_MODE: str = "ssl"
    CANDIDATE_REGISTER_VERIFICATION_AUTH_SECRET: SecretStr = SecretStr("")
    CANDIDATE_REGISTER_VERIFICATION_SUBJECT: str = "Your verification code"
    CANDIDATE_REGISTER_VERIFICATION_CODE_TTL_SECONDS: int = 600
    CANDIDATE_REGISTER_VERIFICATION_RESEND_COOLDOWN_SECONDS: int = 60
    CANDIDATE_REGISTER_VERIFICATION_MAX_ATTEMPTS: int = 5
    CANDIDATE_REGISTER_VERIFICATION_CODE_LENGTH: int = 6
    CANDIDATE_REGISTER_VERIFICATION_REDIS_PREFIX: str = "candidate:register:verification:"
    CANDIDATE_PASSWORD_RESET_VERIFICATION_REDIS_PREFIX: str = "candidate:password-reset:verification:"


class AssetStorageSettings(BaseSettings):
    ASSET_STORAGE_PROVIDER: str = "local"
    ASSET_STORAGE_DIR: str = os.path.join(
        os.path.dirname(os.path.realpath(__file__)),
        "..",
        "..",
        "storage",
        "assets",
    )
    ASSET_STORAGE_KEY_PREFIX: str = "hr-assets"
    ASSET_MAX_UPLOAD_BYTES: int = 25 * 1024 * 1024
    ASSET_UPLOAD_CHUNK_BYTES: int = 1024 * 1024
    ASSET_BATCH_MAX_FILES: int = 50
    ASSET_BATCH_MAX_BYTES: int = 100 * 1024 * 1024
    ASSET_ZIP_SPOOL_MAX_BYTES: int = 8 * 1024 * 1024


class AliyunOSSSettings(BaseSettings):
    ALIYUN_OSS_ENDPOINT: str = ""
    ALIYUN_OSS_ACCESS_KEY_ID: str = ""
    ALIYUN_OSS_ACCESS_KEY_SECRET: SecretStr = SecretStr("")
    ALIYUN_OSS_BUCKET_PRODUCTION: str = "primnota"
    ALIYUN_OSS_BUCKET_NON_PRODUCTION: str = "primnota-test"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ALIYUN_OSS_BUCKET(self) -> str:
        if getattr(self, "ENVIRONMENT", "local") == "production":
            return self.ALIYUN_OSS_BUCKET_PRODUCTION
        return self.ALIYUN_OSS_BUCKET_NON_PRODUCTION


class EnvironmentOption(str, Enum):
    LOCAL = "local"
    STAGING = "staging"
    PRODUCTION = "production"


class EnvironmentSettings(BaseSettings):
    ENVIRONMENT: EnvironmentOption = EnvironmentOption.LOCAL


class CORSSettings(BaseSettings):
    CORS_ORIGINS: list[str] = ["*"]
    CORS_METHODS: list[str] = ["*"]
    CORS_HEADERS: list[str] = ["*"]
    CORS_ALLOW_CREDENTIALS: bool = True


class Settings(
    AppSettings,
    SQLiteSettings,
    MySQLSettings,
    PostgresSettings,
    CryptSettings,
    TestSettings,
    RedisCacheSettings,
    EventSettings,
    MailDeliverySettings,
    MailCredentialSettings,
    MailTaskRecoverySettings,
    HealthSettings,
    LocalDevelopmentSettings,
    CandidateRegisterVerificationSettings,
    AssetStorageSettings,
    AliyunOSSSettings,
    EnvironmentSettings,
    CORSSettings,
    FileLoggerSettings,
    ConsoleLoggerSettings,
):
    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", ".env"),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    @model_validator(mode="after")
    def validate_runtime_security(self) -> "Settings":
        positive_setting_names = (
            "EVENT_CONSUMER_CONCURRENCY",
            "EVENT_CONSUMER_BUFFER_SIZE",
            "EVENT_CONSUMER_MAX_DELIVERIES",
            "EVENT_CONSUMER_SHUTDOWN_TIMEOUT_SECONDS",
            "EVENT_PENDING_IDLE_MS",
            "EVENT_DEAD_LETTER_MAXLEN",
            "EVENT_DEAD_LETTER_RAW_MAX_CHARS",
            "EVENT_DEAD_LETTER_ERROR_MAX_CHARS",
            "HEALTH_CHECK_TIMEOUT_SECONDS",
            "REDIS_CONNECT_TIMEOUT_SECONDS",
            "REDIS_SOCKET_TIMEOUT_SECONDS",
            "MAIL_TASK_PROCESSING_LEASE_SECONDS",
            "MAIL_TASK_RECOVERY_INTERVAL_SECONDS",
            "MAIL_TASK_RECOVERY_BATCH_SIZE",
        )
        for setting_name in positive_setting_names:
            if getattr(self, setting_name) <= 0:
                raise ValueError(f"{setting_name} must be positive.")

        is_local = self.ENVIRONMENT == EnvironmentOption.LOCAL
        if self.ENABLE_LOCAL_AUTH_BYPASS and not is_local:
            raise ValueError("ENABLE_LOCAL_AUTH_BYPASS is only allowed when ENVIRONMENT=local.")
        if self.ENABLE_LOCAL_ADMIN_BOOTSTRAP and not is_local:
            raise ValueError("ENABLE_LOCAL_ADMIN_BOOTSTRAP is only allowed when ENVIRONMENT=local.")

        if self.ENVIRONMENT != EnvironmentOption.PRODUCTION:
            return self

        secret_key = self.SECRET_KEY.get_secret_value().strip()
        if len(secret_key) < 32 or secret_key.lower() in {
            "secret-key",
            "change-this-secret-key",
            "replace-with-a-real-secret",
        }:
            raise ValueError("SECRET_KEY must be a non-placeholder value of at least 32 characters in production.")

        if "*" in self.CORS_ORIGINS and self.CORS_ALLOW_CREDENTIALS:
            raise ValueError("CORS_ORIGINS cannot contain '*' when CORS_ALLOW_CREDENTIALS is enabled in production.")

        mail_credential_key = self.MAIL_CREDENTIAL_ENCRYPTION_KEY.get_secret_value().strip()
        if not mail_credential_key:
            raise ValueError("MAIL_CREDENTIAL_ENCRYPTION_KEY is required in production.")
        try:
            Fernet(mail_credential_key.encode())
        except (TypeError, ValueError):
            raise ValueError(
                "MAIL_CREDENTIAL_ENCRYPTION_KEY must be a valid URL-safe base64-encoded 32-byte Fernet key."
            ) from None

        if self.CANDIDATE_REGISTER_VERIFICATION_ENABLED:
            verification_values = {
                "CANDIDATE_REGISTER_VERIFICATION_SENDER_EMAIL": self.CANDIDATE_REGISTER_VERIFICATION_SENDER_EMAIL,
                "CANDIDATE_REGISTER_VERIFICATION_SMTP_USERNAME": self.CANDIDATE_REGISTER_VERIFICATION_SMTP_USERNAME,
                "CANDIDATE_REGISTER_VERIFICATION_SMTP_HOST": self.CANDIDATE_REGISTER_VERIFICATION_SMTP_HOST,
                "CANDIDATE_REGISTER_VERIFICATION_AUTH_SECRET": (
                    self.CANDIDATE_REGISTER_VERIFICATION_AUTH_SECRET.get_secret_value()
                ),
            }
            missing = [name for name, value in verification_values.items() if not value.strip()]
            if missing:
                raise ValueError(
                    "Candidate registration verification SMTP settings are incomplete: " + ", ".join(missing)
                )

        if self.ASSET_STORAGE_PROVIDER.strip().lower() == "aliyun_oss":
            oss_values = {
                "ALIYUN_OSS_ENDPOINT": self.ALIYUN_OSS_ENDPOINT,
                "ALIYUN_OSS_ACCESS_KEY_ID": self.ALIYUN_OSS_ACCESS_KEY_ID,
                "ALIYUN_OSS_ACCESS_KEY_SECRET": self.ALIYUN_OSS_ACCESS_KEY_SECRET.get_secret_value(),
                "ALIYUN_OSS_BUCKET": self.ALIYUN_OSS_BUCKET,
            }
            missing = [name for name, value in oss_values.items() if not value.strip()]
            if missing:
                raise ValueError("Aliyun OSS settings are incomplete: " + ", ".join(missing))

        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def DATABASE_SYNC_URL(self) -> str:
        backend = self.DATABASE_BACKEND.lower()
        if backend == "sqlite":
            return self.SQLITE_SYNC_URL
        if backend == "postgresql":
            return self.POSTGRES_SYNC_URL
        return self.MYSQL_SYNC_URL

    @computed_field  # type: ignore[prop-decorator]
    @property
    def DATABASE_ASYNC_URL(self) -> str:
        backend = self.DATABASE_BACKEND.lower()
        if backend == "sqlite":
            return self.SQLITE_ASYNC_URL
        if backend == "postgresql":
            return self.POSTGRES_ASYNC_URL
        return self.MYSQL_ASYNC_URL


settings = Settings()
