"""Alembic env.py — async migration support for JARVIS."""

import asyncio
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

# Add src to path so jarvis package is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.config import settings  # noqa: E402
from jarvis.models.tables import Base  # noqa: E402

config = context.config

# Override sqlalchemy.url from settings
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:  # type: ignore[no-untyped-def]
    # P6 (2026-05-29): transaction_per_migration=True is required so that
    # `ALTER TYPE entitytype ADD VALUE 'episode_topic'` (revision
    # p6k7l8m9n0o1) is COMMITTED before the data-update migration that
    # references it (revision q7l8m9n0o1p2). Postgres raises
    # UnsafeNewEnumValueUsageError if a new enum value is referenced in the
    # same transaction in which it was added. Per-migration transactions are
    # also a generally safer default — partial failures don't rollback all
    # of history.
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
