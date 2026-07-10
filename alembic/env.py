from __future__ import annotations

import logging
from logging.config import fileConfig

from alembic import context
from alembic.ddl import impl
from clickhouse_sqlalchemy import engines, types
from clickhouse_sqlalchemy.sql.ddl import DropTable
from sqlalchemy import Column, create_engine, func, pool

from app.core.database import build_clickhouse_url
from app.models import Base

config = context.config

if config.config_file_name is not None and not logging.getLogger().handlers:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", build_clickhouse_url())
target_metadata = Base.metadata


class ClickHouseDialectImpl(impl.DefaultImpl):
    __dialect__ = "clickhouse"
    transactional_ddl = False

    def drop_table(self, table):
        table.dispatch.before_drop(
            table,
            self.connection,
            checkfirst=False,
            _ddl_runner=self,
        )
        self._exec(DropTable(table))
        table.dispatch.after_drop(
            table,
            self.connection,
            checkfirst=False,
            _ddl_runner=self,
        )


def patch_alembic_version_table() -> None:
    migration_context = context.get_context()
    version = migration_context._version

    dt = Column("dt", types.DateTime, server_default=func.now())
    version_num = Column("version_num", types.String, primary_key=True)
    version.append_column(dt)
    version.append_column(version_num, replace_existing=True)
    version.engine = engines.ReplacingMergeTree(version=dt, order_by=func.tuple())


def run_migrations_offline() -> None:
    context.configure(
        url=build_clickhouse_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
        transactional_ddl=False,
    )
    patch_alembic_version_table()

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(
        build_clickhouse_url(),
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            transactional_ddl=False,
        )
        patch_alembic_version_table()

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
