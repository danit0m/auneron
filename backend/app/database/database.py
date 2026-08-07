import logging
from collections.abc import Generator
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy import event
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm import Session
from sqlalchemy.orm import sessionmaker

from app.core.config import settings


database_logger = logging.getLogger(
    "auneron.database"
)


class Base(DeclarativeBase):
    """Base declarativa única dos modelos do Auneron."""


def _build_engine() -> Engine:
    common_options: dict[str, Any] = {
        "echo": settings.database_echo,
        "pool_pre_ping": True,
    }

    if settings.is_sqlite:
        return create_engine(
            settings.database_url,
            connect_args={
                "check_same_thread": False
            },
            **common_options,
        )

    return create_engine(
        settings.database_url,
        connect_args={
            "connect_timeout": (
                settings.database_connect_timeout
            ),
        },
        pool_size=settings.database_pool_size,
        max_overflow=(
            settings.database_max_overflow
        ),
        pool_timeout=(
            settings.database_pool_timeout
        ),
        pool_recycle=(
            settings.database_pool_recycle
        ),
        pool_use_lifo=True,
        pool_reset_on_return="rollback",
        **common_options,
    )


def _configure_postgresql_session(
    dbapi_connection: Any,
    _: Any,
) -> None:
    session_settings = (
        (
            "application_name",
            settings.database_application_name,
        ),
        (
            "statement_timeout",
            (
                f"{settings.database_statement_timeout_ms}"
                "ms"
            ),
        ),
        (
            "lock_timeout",
            (
                f"{settings.database_lock_timeout_ms}"
                "ms"
            ),
        ),
        (
            "idle_in_transaction_session_timeout",
            (
                f"{settings.database_idle_transaction_timeout_ms}"
                "ms"
            ),
        ),
    )

    cursor = dbapi_connection.cursor()

    try:
        for parameter, value in session_settings:
            cursor.execute(
                "SELECT set_config(%s, %s, false)",
                (
                    parameter,
                    value,
                ),
            )

        dbapi_connection.commit()
    finally:
        cursor.close()


engine = _build_engine()

if settings.is_postgresql:
    event.listen(
        engine,
        "connect",
        _configure_postgresql_session,
    )


SessionLocal = sessionmaker(
    bind=engine,
    class_=Session,
    autoflush=False,
    expire_on_commit=False,
)


def get_db() -> Generator[
    Session,
    None,
    None,
]:
    db = SessionLocal()

    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def check_database_connection() -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(
                text("SELECT 1")
            )

        return True
    except Exception as error:
        database_logger.error(
            "database_health_check_failed",
            extra={
                "event": (
                    "database_health_check"
                ),
                "error_type": (
                    type(error).__name__
                ),
            },
        )

        return False
