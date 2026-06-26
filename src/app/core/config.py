import os
from enum import Enum

from pydantic import SecretStr, computed_field
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
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 15
    ADMIN_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 30
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

    @computed_field  # type: ignore[prop-decorator]
    @property
    def REDIS_CACHE_URL(self) -> str:
        return f"redis://{self.REDIS_CACHE_HOST}:{self.REDIS_CACHE_PORT}"


class EventSettings(BaseSettings):
    EVENT_QUEUE_PREFIX: str = "hr-mq:"
    EVENT_CONSUMER_GROUP: str = "hr_event_consumer"
    EVENT_CONSUMER_CONCURRENCY: int = 3
    EVENT_STATS_INTERVAL: int = 30


class MailDeliverySettings(BaseSettings):
    MAIL_DELIVERY_MODE: str | None = None


class CandidateRegisterVerificationSettings(BaseSettings):
    CANDIDATE_WEB_BASE_URL: str = "http://localhost:3002"
    CANDIDATE_REGISTER_VERIFICATION_ENABLED: bool = True
    CANDIDATE_REGISTER_VERIFICATION_SENDER_NAME: str = "T-Maxx Recruit"
    CANDIDATE_REGISTER_VERIFICATION_SENDER_EMAIL: str = "betty-recruit@t-maxx.cc"
    CANDIDATE_REGISTER_VERIFICATION_SMTP_USERNAME: str = "betty-recruit@t-maxx.cc"
    CANDIDATE_REGISTER_VERIFICATION_SMTP_HOST: str = "smtp.feishu.cn"
    CANDIDATE_REGISTER_VERIFICATION_SMTP_PORT: int = 465
    CANDIDATE_REGISTER_VERIFICATION_SMTP_SECURITY_MODE: str = "ssl"
    CANDIDATE_REGISTER_VERIFICATION_AUTH_SECRET: SecretStr = SecretStr("iojfbFwhmtFZdh2m")
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


class FeishuPreviewSettings(BaseSettings):
    FEISHU_PREVIEW_ENABLED: bool = False
    FEISHU_APP_ID: str = ""
    FEISHU_APP_SECRET: SecretStr = SecretStr("")
    FEISHU_PREVIEW_FOLDER_URL: str = ""
    FEISHU_PREVIEW_PUBLIC_LINK_ENABLED: bool = False
    FEISHU_PREVIEW_MAX_FILE_SIZE_MB: int = 20
    FEISHU_PREVIEW_POLL_INTERVAL_SECONDS: float = 1.0
    FEISHU_PREVIEW_POLL_TIMEOUT_SECONDS: float = 30.0
    FEISHU_OPEN_API_BASE_URL: str = "https://open.feishu.cn/open-apis"


class AliyunOSSSettings(BaseSettings):
    ALIYUN_OSS_ENDPOINT: str = ""
    ALIYUN_OSS_ACCESS_KEY_ID: str = ""
    ALIYUN_OSS_ACCESS_KEY_SECRET: SecretStr = SecretStr("")
    ALIYUN_OSS_BUCKET_PRODUCTION: str = "primnota"
    ALIYUN_OSS_BUCKET_NON_PRODUCTION: str = "primnota-test"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ALIYUN_OSS_BUCKET(self) -> str:
        if self.ENVIRONMENT == EnvironmentOption.PRODUCTION:
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
    CandidateRegisterVerificationSettings,
    AssetStorageSettings,
    FeishuPreviewSettings,
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
