from functools import lru_cache
from pathlib import Path
from typing import Literal
from typing import Self

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
    debug: bool = False

    database_url: str = Field(
        validation_alias="DATABASE_URL",
        min_length=1,
    )
    database_echo: bool = False
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

    cors_origins: str = (
        "http://localhost:5173,"
        "http://127.0.0.1:5173"
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [
            item.strip()
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

        if (
            self.environment == "production"
            and self.api_key is None
        ):
            raise ValueError(
                "API_KEY é obrigatória em production."
            )

        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
