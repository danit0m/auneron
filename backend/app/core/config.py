from functools import lru_cache
from pathlib import Path
from typing import Literal
from typing import Self
from urllib.parse import urlsplit

from pydantic import Field
from pydantic import SecretStr
from pydantic import model_validator
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict
from sqlalchemy.engine import make_url


BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Auneron AI"
    app_version: str = "3.0 Alpha"
    environment: Literal[
        "development",
        "test",
        "production",
    ] = Field(
        default="development",
        validation_alias="APP_ENV",
    )
    debug: bool = Field(
        default=False,
        validation_alias="DEBUG",
    )

    database_url: str = Field(
        validation_alias="DATABASE_URL",
        min_length=1,
    )
    database_echo: bool = Field(
        default=False,
        validation_alias="DATABASE_ECHO",
    )
    database_pool_size: int = Field(
        default=5,
        ge=1,
        le=50,
    )
    database_max_overflow: int = Field(
        default=5,
        ge=0,
        le=50,
    )
    database_pool_timeout: int = Field(
        default=10,
        ge=1,
        le=120,
    )
    database_pool_recycle: int = Field(
        default=900,
        ge=60,
        le=86400,
    )
    database_connect_timeout: int = Field(
        default=5,
        ge=1,
        le=60,
    )
    database_statement_timeout_ms: int = Field(
        default=30000,
        ge=1000,
        le=300000,
    )
    database_lock_timeout_ms: int = Field(
        default=5000,
        ge=100,
        le=60000,
    )
    database_idle_transaction_timeout_ms: int = Field(
        default=60000,
        ge=1000,
        le=600000,
    )
    database_application_name: str = Field(
        default="auneron-api",
        min_length=1,
        max_length=63,
    )

    api_key: SecretStr | None = Field(
        default=None,
        validation_alias="API_KEY",
    )

    auth_cookie_name: str = Field(
        default="auneron_session",
        validation_alias="AUTH_COOKIE_NAME",
        min_length=3,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    auth_session_ttl_minutes: int = Field(
        default=480,
        validation_alias="AUTH_SESSION_TTL_MINUTES",
        ge=15,
        le=10080,
    )
    auth_elevation_ttl_minutes: int = Field(
        default=10,
        validation_alias="AUTH_ELEVATION_TTL_MINUTES",
        ge=1,
        le=60,
    )

    auth_login_account_max_failures: int = Field(
        default=5,
        validation_alias="AUTH_LOGIN_ACCOUNT_MAX_FAILURES",
        ge=2,
        le=50,
    )
    auth_login_ip_max_failures: int = Field(
        default=25,
        validation_alias="AUTH_LOGIN_IP_MAX_FAILURES",
        ge=5,
        le=500,
    )
    auth_login_window_seconds: int = Field(
        default=900,
        validation_alias="AUTH_LOGIN_WINDOW_SECONDS",
        ge=60,
        le=3600,
    )
    auth_elevation_user_max_failures: int = Field(
        default=5,
        validation_alias="AUTH_ELEVATION_USER_MAX_FAILURES",
        ge=2,
        le=50,
    )
    auth_elevation_ip_max_failures: int = Field(
        default=15,
        validation_alias="AUTH_ELEVATION_IP_MAX_FAILURES",
        ge=5,
        le=200,
    )
    auth_elevation_window_seconds: int = Field(
        default=600,
        validation_alias="AUTH_ELEVATION_WINDOW_SECONDS",
        ge=60,
        le=3600,
    )

    auth_session_cleanup_interval_seconds: int = Field(
        default=3600,
        validation_alias="AUTH_SESSION_CLEANUP_INTERVAL_SECONDS",
        ge=60,
        le=86400,
    )
    auth_revoked_session_retention_hours: int = Field(
        default=24,
        validation_alias="AUTH_REVOKED_SESSION_RETENTION_HOURS",
        ge=1,
        le=720,
    )

    skill_runtime_max_workers: int = Field(
        default=4,
        validation_alias="SKILL_RUNTIME_MAX_WORKERS",
        ge=1,
        le=32,
    )
    skill_autonomy_process_max_workers: int = Field(
        default=2,
        validation_alias="SKILL_AUTONOMY_PROCESS_MAX_WORKERS",
        ge=1,
        le=8,
    )
    skill_autonomy_process_kill_grace_seconds: int = Field(
        default=2,
        validation_alias=(
            "SKILL_AUTONOMY_PROCESS_KILL_GRACE_SECONDS"
        ),
        ge=1,
        le=10,
    )
    skill_rate_limit_user_max_requests: int = Field(
        default=60,
        validation_alias="SKILL_RATE_LIMIT_USER_MAX_REQUESTS",
        ge=1,
        le=10000,
    )
    skill_rate_limit_window_seconds: int = Field(
        default=60,
        validation_alias="SKILL_RATE_LIMIT_WINDOW_SECONDS",
        ge=1,
        le=3600,
    )
    skill_stale_running_seconds: int = Field(
        default=600,
        validation_alias="SKILL_STALE_RUNNING_SECONDS",
        ge=301,
        le=86400,
    )
    skill_recovery_interval_seconds: int = Field(
        default=60,
        validation_alias="SKILL_RECOVERY_INTERVAL_SECONDS",
        ge=30,
        le=3600,
    )
    skill_recovery_batch_size: int = Field(
        default=100,
        validation_alias="SKILL_RECOVERY_BATCH_SIZE",
        ge=1,
        le=1000,
    )

    work_skill_dispatch_max_requests: int = Field(
        default=30,
        validation_alias="WORK_SKILL_DISPATCH_MAX_REQUESTS",
        ge=1,
        le=10000,
    )
    work_skill_dispatch_window_seconds: int = Field(
        default=60,
        validation_alias="WORK_SKILL_DISPATCH_WINDOW_SECONDS",
        ge=1,
        le=3600,
    )
    work_skill_recovery_interval_seconds: int = Field(
        default=60,
        validation_alias="WORK_SKILL_RECOVERY_INTERVAL_SECONDS",
        ge=30,
        le=3600,
    )
    work_skill_recovery_batch_size: int = Field(
        default=100,
        validation_alias="WORK_SKILL_RECOVERY_BATCH_SIZE",
        ge=1,
        le=1000,
    )

    approval_request_ttl_minutes: int = Field(
        default=1440,
        validation_alias="APPROVAL_REQUEST_TTL_MINUTES",
        ge=5,
        le=10080,
    )

    log_level: Literal[
        "DEBUG",
        "INFO",
        "WARNING",
        "ERROR",
        "CRITICAL",
    ] = Field(
        default="INFO",
        validation_alias="LOG_LEVEL",
    )

    cors_origins: str = Field(
        default=(
            "http://localhost:5173,"
            "http://127.0.0.1:5173"
        ),
        validation_alias="CORS_ORIGINS",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [
            item.strip().rstrip("/")
            for item in self.cors_origins.split(",")
            if item.strip()
        ]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def is_postgresql(self) -> bool:
        return self.database_url.startswith(
            (
                "postgresql://",
                "postgresql+psycopg://",
            )
        )

    @property
    def database_name(self) -> str:
        return make_url(
            self.database_url
        ).database or ""

    @property
    def auth_cookie_secure(self) -> bool:
        return self.environment == "production"

    def _validate_cors(self) -> None:
        origins = self.cors_origin_list

        if len(origins) != len(set(origins)):
            raise ValueError(
                "CORS_ORIGINS não pode conter origens duplicadas."
            )

        for origin in origins:
            if origin == "*":
                raise ValueError(
                    "CORS_ORIGINS não pode usar wildcard '*'."
                )

            parsed = urlsplit(origin)

            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(
                    "CORS_ORIGINS deve conter apenas origens "
                    "HTTP(S) completas, sem caminho, query "
                    "ou fragmento."
                )

            if self.environment == "production":
                if parsed.scheme != "https":
                    raise ValueError(
                        "CORS_ORIGINS em production deve usar HTTPS."
                    )

                if parsed.hostname in {
                    "localhost",
                    "127.0.0.1",
                    "::1",
                }:
                    raise ValueError(
                        "CORS_ORIGINS em production não pode "
                        "apontar para loopback."
                    )

    @model_validator(mode="after")
    def validate_environment(self) -> Self:
        try:
            database_name = self.database_name
        except Exception as error:
            raise ValueError(
                "DATABASE_URL inválida."
            ) from error

        if not database_name:
            raise ValueError(
                "DATABASE_URL precisa informar o banco."
            )

        if (
            self.environment == "test"
            and database_name != "auneron_test"
        ):
            raise ValueError(
                "APP_ENV=test somente pode usar "
                "o banco auneron_test."
            )

        if (
            self.environment != "test"
            and database_name == "auneron_test"
        ):
            raise ValueError(
                "O banco auneron_test exige APP_ENV=test."
            )

        if self.api_key is not None:
            api_key_value = self.api_key.get_secret_value()

            if len(api_key_value) < 32:
                raise ValueError(
                    "API_KEY precisa ter pelo menos "
                    "32 caracteres."
                )

        if self.environment == "production":
            if self.api_key is None:
                raise ValueError(
                    "API_KEY é obrigatória em production."
                )

            api_key_value = (
                self.api_key.get_secret_value()
            )
            normalized_api_key = (
                api_key_value.strip().lower()
            )

            placeholder_markers = (
                "change_me",
                "change-me",
                "replace_me",
                "replace-me",
                "troque",
                "example",
                "auneron_dev",
                "auneron-dev",
            )

            if any(
                marker in normalized_api_key
                for marker in placeholder_markers
            ):
                raise ValueError(
                    "API_KEY de production não pode "
                    "usar valor placeholder."
                )

            if len(set(api_key_value)) < 12:
                raise ValueError(
                    "API_KEY de production possui "
                    "baixa diversidade de caracteres."
                )

            if not self.is_postgresql:
                raise ValueError(
                    "APP_ENV=production exige PostgreSQL."
                )

            if self.debug:
                raise ValueError(
                    "DEBUG deve ser false em production."
                )

            if self.database_echo:
                raise ValueError(
                    "DATABASE_ECHO deve ser false "
                    "em production."
                )

        if (
            self.auth_elevation_ttl_minutes
            > self.auth_session_ttl_minutes
        ):
            raise ValueError(
                "AUTH_ELEVATION_TTL_MINUTES não pode "
                "ser maior que AUTH_SESSION_TTL_MINUTES."
            )

        if (
            self.auth_login_ip_max_failures
            < self.auth_login_account_max_failures
        ):
            raise ValueError(
                "AUTH_LOGIN_IP_MAX_FAILURES não pode "
                "ser menor que AUTH_LOGIN_ACCOUNT_MAX_FAILURES."
            )

        if (
            self.auth_elevation_ip_max_failures
            < self.auth_elevation_user_max_failures
        ):
            raise ValueError(
                "AUTH_ELEVATION_IP_MAX_FAILURES não pode "
                "ser menor que AUTH_ELEVATION_USER_MAX_FAILURES."
            )

        self._validate_cors()

        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
