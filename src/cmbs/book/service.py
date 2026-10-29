"""Load positions files and read the book as of a date.

A positions file lists deal, class and face amount, and carries the date it is as of. The book
on a date is the latest file dated on or before it; among files with the same date, the one
loaded last. Exposure to a deal is the total face held in it (Q38).

Parsing and checks are pure functions; loading checks everything before changing anything.
"""

import csv
import datetime as dt
import io
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from cmbs import clock
from cmbs.access.roles import PERMISSIONS, Action
from cmbs.access.service import Actor, authorize
from cmbs.audit.service import record
from cmbs.book.tables import Position, PositionsFile
from cmbs.deals.service import get_or_create
from cmbs.deals.tables import Deal
from cmbs.domain import format_money, money
from cmbs.errors import Refused

COLUMNS = ("deal", "class", "face amount")


class InvalidPositions(Refused):
    """A positions file that cannot be loaded as written."""


@dataclass(frozen=True)
class PositionLine:
    deal: str
    tranche: str
    face: Decimal


@dataclass(frozen=True)
class Held:
    deal_id: int
    deal: str
    tranche: str
    face: Decimal


@dataclass(frozen=True)
class Book:
    file_id: int
    as_of: dt.date
    positions: tuple[Held, ...]

    def exposure(self) -> dict[str, Decimal]:
        return exposures(PositionLine(h.deal, h.tranche, h.face) for h in self.positions)

    def classes_of(self, deal: str) -> list[str]:
        return sorted(h.tranche for h in self.positions if h.deal == deal)


def _header(name: str) -> str:
    return " ".join(name.replace("_", " ").split()).lower()


def position_line(deal: str, tranche: str, face: str, *, where: str = "") -> PositionLine:
    """One position from text: whitespace in the deal name normalized, face read as money."""
    try:
        amount = money(face)
    except ValueError:
        raise InvalidPositions(f"{where}{face.strip()!r} is not a face amount") from None
    return PositionLine(deal=" ".join(deal.split()), tranche=tranche.strip(), face=amount)


def parse_positions(text: str) -> list[PositionLine]:
    """CSV with the columns deal, class and face amount (any case; '_' reads as a space).

    Blank lines are skipped. Every problem is reported with its line number.
    """
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if header is None:
        raise InvalidPositions("the positions file is empty")
    names = [_header(name) for name in header]
    missing = [column for column in COLUMNS if column not in names]
    if missing:
        raise InvalidPositions(f"the positions file has no {', '.join(missing)} column")
    index = {column: names.index(column) for column in COLUMNS}
    lines = []
    for number, row in enumerate(reader, start=2):
        if not any(cell.strip() for cell in row):
            continue
        if len(row) < len(names):
            raise InvalidPositions(f"line {number} has {len(row)} fields, not {len(names)}")
        lines.append(
            position_line(
                row[index["deal"]],
                row[index["class"]],
                row[index["face amount"]],
                where=f"line {number}: ",
            )
        )
    return lines


def check_lines(lines: Sequence[PositionLine]) -> None:
    """Refuse lines without a deal or class, with a face that is not positive, or repeated."""
    seen: set[tuple[str, str]] = set()
    for number, line in enumerate(lines, start=1):
        if not line.deal:
            raise InvalidPositions(f"position {number} names no deal")
        if not line.tranche:
            raise InvalidPositions(f"position {number} in {line.deal} names no class")
        if line.face <= 0:
            raise InvalidPositions(
                f"Class {line.tranche} of {line.deal} has face {format_money(line.face)}; "
                "a position must hold more than zero"
            )
        key = (line.deal, line.tranche)
        if key in seen:
            raise InvalidPositions(f"Class {line.tranche} of {line.deal} is listed twice")
        seen.add(key)


def exposures(lines: Iterable[PositionLine]) -> dict[str, Decimal]:
    """Total face held per deal."""
    total: dict[str, Decimal] = defaultdict(Decimal)
    for line in lines:
        total[line.deal] += line.face
    return dict(total)


def load_positions(
    db: Session,
    *,
    user: str,
    as_of: dt.date,
    lines: Sequence[PositionLine],
    source_name: str | None = None,
) -> PositionsFile:
    """Store a positions file as the book from `as_of`. Needs the right to load positions."""
    actor = authorize(db, user, Action.LOAD_POSITIONS)
    return store_positions(db, actor=actor, as_of=as_of, lines=lines, source_name=source_name)


def store_positions(
    db: Session,
    *,
    actor: Actor,
    as_of: dt.date,
    lines: Sequence[PositionLine],
    source_name: str | None = None,
) -> PositionsFile:
    """Store a positions file for an actor already authorized to load positions.

    Deals are looked up, and their rows locked, in name order: two files naming the same deals
    in different orders, loaded at once, would otherwise wait on each other's locks.
    """
    if Action.LOAD_POSITIONS not in PERMISSIONS[actor.role]:
        raise ValueError(f"an actor acting as {actor.role.value} cannot load positions")
    check_lines(lines)
    deal_ids = {name: get_or_create(db, name).id for name in sorted({line.deal for line in lines})}
    held: dict[tuple[int, str], str] = {}
    for line in lines:
        key = (deal_ids[line.deal], line.tranche)
        if key in held:
            raise InvalidPositions(
                f"Class {line.tranche} of {held[key]} is listed twice, also as {line.deal}"
            )
        held[key] = line.deal
    loaded = PositionsFile(
        as_of=as_of, loaded_by=actor.user, loaded_at=clock.now(), source_name=source_name
    )
    db.add(loaded)
    db.flush()
    db.add_all(
        Position(
            file_id=loaded.id,
            deal_id=deal_ids[line.deal],
            tranche=line.tranche,
            face_amount=line.face,
        )
        for line in lines
    )
    db.flush()
    total = sum((line.face for line in lines), Decimal(0))
    record(
        db,
        user=actor.user,
        role=actor.role.value,
        action="load positions",
        subject="positions_file",
        subject_key=str(loaded.id),
        after=(
            f"{len(lines)} positions in {len(set(deal_ids.values()))} deals as of "
            f"{as_of.isoformat()}, "
            f"face {format_money(total)}"
        ),
        detail={
            "as_of": as_of.isoformat(),
            "positions": len(lines),
            "deals": len(set(deal_ids.values())),
            "total_face": str(money(total)),
            "source_name": source_name,
        },
    )
    return loaded


def book_as_of(db: Session, on: dt.date) -> Book | None:
    """The latest positions file dated on or before `on`, or None if there is none."""
    latest = db.scalars(
        select(PositionsFile)
        .where(PositionsFile.as_of <= on)
        .order_by(PositionsFile.as_of.desc(), PositionsFile.id.desc())
        .limit(1)
    ).one_or_none()
    if latest is None:
        return None
    rows = db.execute(
        select(Position.deal_id, Deal.name, Position.tranche, Position.face_amount)
        .join(Deal, Deal.id == Position.deal_id)
        .where(Position.file_id == latest.id)
        .order_by(Deal.name, Position.tranche)
    ).all()
    return Book(
        file_id=latest.id,
        as_of=latest.as_of,
        positions=tuple(
            Held(deal_id, name, tranche, face) for deal_id, name, tranche, face in rows
        ),
    )


def exposure_by_deal(db: Session, on: dt.date) -> dict[str, Decimal]:
    """Total face held per deal in the book as of `on`; empty when there is no book yet."""
    book = book_as_of(db, on)
    return book.exposure() if book is not None else {}


def exposure_for(db: Session, *, user: str, on: dt.date) -> dict[str, Decimal]:
    """Exposure per deal for a user who may read the panel."""
    authorize(db, user, Action.READ_PANEL)
    return exposure_by_deal(db, on)
