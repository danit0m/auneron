from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    environment: str = Field(default="development", validation_alias="APP_ENV")
    debug: bool = False

    database_url: str = Field(
        default="postgresql+psycopg://auneron:auneron_dev_password@localhost:5432/auneron",
        validation_alias="DATABASE_URL",
    )
    database_echo: bool = False
    database_pool_size: int = 10
    database_max_overflow: int = 20
    database_pool_timeout: int = 30
    database_pool_recycle: int = 1800

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

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
            ("postgresql://", "postgresql+psycopg://")
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
