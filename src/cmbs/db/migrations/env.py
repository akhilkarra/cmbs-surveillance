"""Alembic environment: connects with DATABASE_URL and migrates the `cmbs.db.models` metadata."""

import logging
import os

from alembic import context
from sqlalchemy import create_engine, pool

from cmbs.db.models import SCHEMAS as OUR_SCHEMAS
from cmbs.db.models import Base
from cmbs.db.registry import load_all_tables

logging.basicConfig(format="%(levelname)-5.5s [%(name)s] %(message)s")
logging.getLogger("alembic").setLevel(logging.INFO)
logging.getLogger("alembic.runtime.plugins").setLevel(logging.WARNING)

SCHEMAS = {"public", *OUR_SCHEMAS}

# Every feature declares its tables in cmbs.<feature>.tables; import them all.
load_all_tables()


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set; see .env.example.")
    return url


def include_name(name, type_, parent_names) -> bool:
    """Autogenerate compares only our schemas, not Postgres's own."""
    return name in SCHEMAS if type_ == "schema" else True


def configure(**kwargs) -> None:
    context.configure(
        target_metadata=Base.metadata,
        include_schemas=True,
        include_name=include_name,
        **kwargs,
    )


if context.is_offline_mode():
    configure(url=database_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
