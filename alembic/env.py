"""
alembic/env.py

Alembic's entry point. Connects through database.py, so migrations use the
same DATABASE_URL, psycopg driver and UTC session time zone as the app, and
compares the database against the models registered on Base.metadata.
"""

from logging.config import fileConfig

from alembic import context

import database
import models  # noqa: F401 — registers all tables on database.Base.metadata

config = context.config
if config.config_file_name is not None:
    # Keep pytest's and uvicorn's loggers working when run in-process
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = database.Base.metadata


def run_migrations_offline():
    """`alembic upgrade head --sql`: print the SQL instead of running it (handy for review)."""
    context.configure(
        url=database.SQLALCHEMY_DATABASE_URL.render_as_string(hide_password=False),
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    with database.engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
