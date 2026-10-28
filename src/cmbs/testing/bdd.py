"""Helpers for behave step modules. Steps call the service layer through these."""

from collections.abc import Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from cmbs.db import engine
from cmbs.db.models import LAYERS
from cmbs.errors import Refused


def attempt[T](context: Any, action: Callable[[], T]) -> T | None:
    """Run an action that the domain may refuse; keep the refusal for a later Then step."""
    context.refusal = None
    try:
        return action()
    except Refused as refusal:
        context.refusal = refusal
        return None


def in_session[T](action: Callable[[Session], T]) -> T:
    """Run `action` in its own committed unit of work, as one API request would."""
    with engine.session() as db:
        return action(db)


def data_snapshot() -> dict[str, str]:
    """A fingerprint of every table in the data layers. Logs (audit, runs) are not data."""
    fingerprints: dict[str, str] = {}
    with engine.session() as db:
        tables = db.execute(
            text(
                "SELECT table_schema, table_name FROM information_schema.tables "
                "WHERE table_schema = ANY(:schemas) AND table_type = 'BASE TABLE' "
                "ORDER BY 1, 2"
            ),
            {"schemas": list(LAYERS)},
        ).all()
        for schema, table in tables:
            digest = db.execute(
                text(
                    f"SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY t::text), '')) "
                    f'FROM "{schema}"."{table}" t'
                )
            ).scalar_one()
            fingerprints[f"{schema}.{table}"] = digest
    return fingerprints


def changed_tables(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Tables whose contents differ, including tables added or dropped since `before`."""
    return sorted(
        name for name in before.keys() | after.keys() if before.get(name) != after.get(name)
    )
