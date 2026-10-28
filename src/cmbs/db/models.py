"""SQLAlchemy base. Each feature module declares its tables in its own `tables.py`.

`cmbs.db.registry.load_all_tables()` imports every one of them, so Alembic and the test
harness see the whole schema without a central list that every feature would edit.
"""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Medallion layers, each a Postgres schema (grill log Q20).
LAYERS = ("raw", "bronze", "silver", "gold")
# Application state that is not market data: users, audit, settings, runs, workflow.
APP_SCHEMA = "app"
SCHEMAS = (*LAYERS, APP_SCHEMA)

# Named constraints so Alembic can drop and alter them by name.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
