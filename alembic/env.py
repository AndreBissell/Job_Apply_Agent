"""Alembic migration environment.

Wired to the application's own engine and metadata so migrations always target
the same ``DATABASE_URL`` the app uses, with no duplicated connection config in
``alembic.ini``. ``render_as_batch`` is enabled so future ``ALTER`` migrations
work on SQLite (which lacks full ALTER support) while remaining a no-op on
Postgres.
"""

from logging.config import fileConfig

from alembic import context

# Make the project importable when Alembic runs from the repo root.
from app.db import DATABASE_URL, Base, engine
from app import models  # noqa: F401  (imported for side effect: registers tables)

# Alembic Config object — access to values in alembic.ini.
config = context.config

# Keep alembic.ini in sync with the app's resolved URL (also used offline).
config.set_main_option("sqlalchemy.url", DATABASE_URL)

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# SQLite needs batch mode for ALTER; harmless elsewhere.
RENDER_AS_BATCH = engine.dialect.name == "sqlite"


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL, no DBAPI needed)."""
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=RENDER_AS_BATCH,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode using the application's engine."""
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=RENDER_AS_BATCH,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
