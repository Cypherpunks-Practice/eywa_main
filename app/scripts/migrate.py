from __future__ import annotations

import logging

from ..core.database import (
    ensure_database_exists,
    upgrade_database,
    wait_for_clickhouse,
)

logger = logging.getLogger(__name__)


def apply_migrations():
    logger.info("Waiting for ClickHouse default database")
    wait_for_clickhouse(database="default")
    logger.info("Ensuring application database exists")
    ensure_database_exists()
    logger.info("Waiting for ClickHouse application database")
    wait_for_clickhouse()
    logger.info("Applying Alembic migrations")
    upgrade_database()
    logger.info("Alembic migrations applied")


def main():
    apply_migrations()


if __name__ == "__main__":
    main()
