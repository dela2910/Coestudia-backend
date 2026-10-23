from logging.config import fileConfig

from alembic import context

from app import models  # noqa: F401  (registra las tablas en Base.metadata)
from app.db import Base, database_url, make_engine

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def url() -> str:
    # Los tests pueden pasar una URL propia; si no, se usa DATABASE_URL
    return config.attributes.get("url") or database_url()


def run_migrations_offline() -> None:
    # Genera el SQL sin conectarse a la base (alembic upgrade head --sql)
    context.configure(
        url=url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = make_engine(url())
    with engine.connect() as connection:
        # render_as_batch permite alterar tablas también en SQLite
        context.configure(
            connection=connection, target_metadata=target_metadata, render_as_batch=True
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
