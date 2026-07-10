from __future__ import annotations

import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote_plus

from clickhouse_sqlalchemy import make_session
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .config import get_settings

if TYPE_CHECKING:
    from alembic.config import Config

settings = get_settings()
BASE_DIR = Path(__file__).resolve().parents[2]


def build_clickhouse_url(database: str | None = None) -> str:
    username = quote_plus(settings.clickhouse_username)
    password = quote_plus(settings.clickhouse_password)
    auth = f"{username}:{password}@" if settings.clickhouse_password else f"{username}@"

    return (
        f"clickhouse+{settings.clickhouse_driver}://"
        f"{auth}{settings.clickhouse_host}:{settings.clickhouse_port}/"
        f"{database or settings.clickhouse_database}"
    )


def make_engine(
    database: str | None = None,
    *,
    echo: bool | None = None,
) -> Engine:
    return create_engine(
        build_clickhouse_url(database),
        echo=settings.clickhouse_echo if echo is None else echo,
        pool_pre_ping=True,
    )


engine = make_engine()


def create_session() -> Session:
    return make_session(engine)


SessionLocal = create_session


def get_session() -> Generator[Session, None, None]:
    session = create_session()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    session = create_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def ping_clickhouse(database: str | None = None):
    target_engine = engine if database in (None, settings.clickhouse_database) else make_engine(
        database,
        echo=False,
    )
    should_dispose = target_engine is not engine

    try:
        with target_engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    finally:
        if should_dispose:
            target_engine.dispose()


def wait_for_clickhouse(database: str | None = None):
    database_name = database or settings.clickhouse_database
    deadline = time.monotonic() + settings.clickhouse_startup_timeout_seconds
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        try:
            ping_clickhouse(database)
            return
        except Exception as exc:  # pragma: no cover - startup retry path
            last_error = exc
            time.sleep(settings.clickhouse_retry_interval_seconds)

    raise RuntimeError(
        f"ClickHouse database `{database_name}` is not reachable after "
        f"{settings.clickhouse_startup_timeout_seconds} seconds"
    ) from last_error


def ensure_database_exists():
    admin_engine = make_engine("default", echo=False)
    try:
        with admin_engine.connect() as connection:
            connection.execute(
                text(
                    f"CREATE DATABASE IF NOT EXISTS `{settings.clickhouse_database}`"
                )
            )
    finally:
        admin_engine.dispose()


def get_alembic_config() -> Config:
    from alembic.config import Config

    config = Config(str(BASE_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BASE_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", build_clickhouse_url())
    return config


def upgrade_database(revision: str = "head"):
    from alembic import command

    command.upgrade(get_alembic_config(), revision)
