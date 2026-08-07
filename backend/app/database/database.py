import logging
from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


database_logger = logging.getLogger(
    "auneron.database"
)


class Base(DeclarativeBase):
    """Base declarativa única dos modelos do Auneron."""


def _build_engine() -> Engine:
    common_options = {
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
        **common_options,
    )


engine = _build_engine()

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
