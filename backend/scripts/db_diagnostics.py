import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(
        0,
        str(BACKEND_DIR),
    )

from sqlalchemy import text

from app.core.config import settings
from app.database.database import engine


def main() -> None:
    print("=== AUNERON DATABASE DIAGNOSTICS ===")
    print(
        "environment:",
        settings.environment,
    )
    print(
        "database_name:",
        settings.database_name,
    )
    print(
        "dialect:",
        engine.dialect.name,
    )
    print(
        "driver:",
        engine.dialect.driver,
    )
    print(
        "pool_class:",
        type(engine.pool).__name__,
    )

    if hasattr(engine.pool, "size"):
        print(
            "pool_size:",
            engine.pool.size(),
        )

    print(
        "configured_max_overflow:",
        settings.database_max_overflow,
    )
    print(
        "configured_pool_timeout:",
        settings.database_pool_timeout,
    )
    print(
        "configured_pool_recycle:",
        settings.database_pool_recycle,
    )
    print(
        "configured_connect_timeout:",
        settings.database_connect_timeout,
    )

    with engine.connect() as connection:
        runtime = connection.execute(
            text(
                """
                SELECT
                    current_database() AS database,
                    current_user AS database_user,
                    current_setting(
                        'application_name'
                    ) AS application_name,
                    current_setting(
                        'statement_timeout'
                    ) AS statement_timeout,
                    current_setting(
                        'lock_timeout'
                    ) AS lock_timeout,
                    current_setting(
                        'idle_in_transaction_session_timeout'
                    ) AS idle_transaction_timeout,
                    current_setting(
                        'transaction_isolation'
                    ) AS transaction_isolation
                """
            )
        ).mappings().one()

    print("=== SESSION RUNTIME ===")

    for key, value in runtime.items():
        print(
            f"{key}: {value}"
        )

    print(
        "pool_status:",
        engine.pool.status(),
    )


if __name__ == "__main__":
    main()
