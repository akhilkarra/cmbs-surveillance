"""One run per deal at a time: a run claims its deals before it loads anything.

The claim is a Postgres session-level advisory lock, keyed on the deal, held on a connection of
its own for the whole run. It outlives the run's many transactions, and when the run's process
dies its connection closes and Postgres lets the lock go, so a crashed run never blocks a deal.

`app.run_claim` says which run holds each deal, so a refused run can name it ("run 42 is already
refreshing Fixture Trust 2026-F1"). A run writes its rows only once it holds the locks and
deletes them before letting the locks go. A row whose lock is free was therefore left by a run
that stopped without finishing: the next run to claim the deal takes the row over and finishes
the old run as failed (`cmbs.runs.service.abandon`).

The lock connection runs in autocommit, so holding a claim never holds a transaction open.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field

from sqlalchemy import Connection, Engine, create_engine, delete, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from cmbs import clock
from cmbs.db import engine as db_engine
from cmbs.deals.tables import Deal
from cmbs.errors import Refused
from cmbs.refresh.tables import RunClaim
from cmbs.runs import service as runs
from cmbs.runs.tables import Run

# The advisory-lock space for run claims; the deal id is the key within it. Deal registration
# uses 0x0C1C (`cmbs.deals.service.CIK_LOCK_SPACE`).
RUN_LOCK_SPACE = 0x0C1D
ABANDONED = "stopped without finishing: its connection to the database closed"


class AlreadyRefreshing(Refused):
    """Another run holds the deal."""


def held_message(db: Session, deal: Deal) -> str:
    """'run 42 is already refreshing Fixture Trust 2026-F1', naming the holder when known."""
    holder = db.get(RunClaim, deal.id, populate_existing=True)
    who = f"run {holder.run_id}" if holder is not None else "another run"
    return f"{who} is already refreshing {deal.name}"


@dataclass
class Claims:
    """The advisory locks a run holds, on a connection of their own."""

    _engine: Engine
    _connection: Connection
    held: list[int] = field(default_factory=list)

    @classmethod
    def open(cls) -> "Claims":
        # Unpooled: closing the connection must really close it, so no lock outlives the run.
        url = db_engine.engine().url
        separate = create_engine(url, poolclass=NullPool)
        connection = separate.connect().execution_options(isolation_level="AUTOCOMMIT")
        return cls(separate, connection)

    def try_take(self, deal_id: int) -> bool:
        taken = self._connection.execute(
            text("SELECT pg_try_advisory_lock(CAST(:space AS integer), CAST(:deal AS integer))"),
            {"space": RUN_LOCK_SPACE, "deal": deal_id},
        ).scalar_one()
        if taken:
            self.held.append(deal_id)
        return bool(taken)

    def close(self) -> None:
        """Let every lock go and close the connection."""
        try:
            for deal_id in self.held:
                self._connection.execute(
                    text(
                        "SELECT pg_advisory_unlock(CAST(:space AS integer), CAST(:deal AS integer))"
                    ),
                    {"space": RUN_LOCK_SPACE, "deal": deal_id},
                )
        finally:
            self.held.clear()
            self._connection.close()
            self._engine.dispose()


def record_claims(db: Session, run: Run, deal_ids: Iterable[int]) -> list[int]:
    """Write the run's claim rows, taking over rows left by runs that died; returns their ids.

    Call only while holding the deals' locks: a row whose lock this run holds cannot belong to
    a live run.
    """
    abandoned: set[int] = set()
    for deal_id in sorted(deal_ids):
        stale = db.get(RunClaim, deal_id, with_for_update=True, populate_existing=True)
        if stale is not None and stale.run_id != run.id:
            abandoned.add(stale.run_id)
        db.execute(
            insert(RunClaim)
            .values(deal_id=deal_id, run_id=run.id, claimed_at=clock.now())
            .on_conflict_do_update(
                index_elements=[RunClaim.deal_id],
                set_={"run_id": run.id, "claimed_at": clock.now()},
            )
        )
    for number in sorted(abandoned):
        old = db.get(Run, number)
        if old is not None and old.status == runs.RUNNING:
            runs.abandon(db, old, ABANDONED)
    db.flush()
    return sorted(abandoned)


def release_claims(db: Session, run: Run) -> None:
    db.execute(delete(RunClaim).where(RunClaim.run_id == run.id))
    db.flush()


def holders(db: Session) -> dict[int, int]:
    """Deal id to the run holding it, as recorded."""
    return {claim.deal_id: claim.run_id for claim in db.scalars(select(RunClaim))}
