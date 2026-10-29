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
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from cmbs import clock
from cmbs.access.roles import PERMISSIONS, ROLE_ORDER, Action, Role
from cmbs.access.service import Actor, PermissionRefused, UnknownUser, authorize, roles_of
from cmbs.alerts.locks import lock_builds
from cmbs.audit.service import record, record_refusal, refuse
from cmbs.book.tables import Position, PositionsFile, WholeLoan, WholeLoanNote
from cmbs.deals.service import get_or_create, resolve
from cmbs.deals.tables import Deal
from cmbs.domain import format_money, money
from cmbs.errors import Refused
from cmbs.tape.tables import LoanPeriod

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

    def holding(self, deal_id: int) -> "Holding":
        """What the book holds of one deal; zero face and no classes when it holds nothing."""
        mine = [h for h in self.positions if h.deal_id == deal_id]
        return Holding(
            face=money(sum((h.face for h in mine), Decimal(0))),
            classes=tuple(sorted(h.tranche for h in mine)),
        )


@dataclass(frozen=True)
class Holding:
    face: Decimal
    classes: tuple[str, ...]


NOTHING_HELD = Holding(face=Decimal("0.00"), classes=())


def holding(book: Book | None, deal_id: int) -> Holding:
    """What a book (None when there is no book yet) holds of a deal."""
    return book.holding(deal_id) if book is not None else NOTHING_HELD


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


# ---------------------------------------------------------------------------- whole loans

# Linking whole loans is a quant's job (Q48). The shared role matrix has no action for it yet,
# so the check lives here, refuses in the matrix's words and audits the refusal like it.
LINKERS = frozenset({Role.QUANT})
LINK_WHOLE_LOANS = "link whole loans"
LINK_ACTION = "link whole loan"
WHOLE_LOAN = "whole loan"


class InvalidLink(Refused):
    """A whole-loan link that cannot be made as asked."""


@dataclass(frozen=True)
class NoteRef:
    """A deal's loan, by deal name, that is a note of a whole loan."""

    deal: str
    loan: str

    def __str__(self) -> str:
        return f"loan {self.loan} of {self.deal}"


def clean_notes(notes: Sequence[NoteRef]) -> list[NoteRef]:
    """The notes with names normalized: two or more, each naming a loan, in different deals."""
    cleaned = [NoteRef(" ".join(n.deal.split()), n.loan.strip()) for n in notes]
    if any(not note.deal or not note.loan for note in cleaned):
        raise InvalidLink("every note of a whole loan names a deal and a loan")
    if len(cleaned) < 2:
        raise InvalidLink("a whole loan links loans of at least two deals")
    seen: set[str] = set()
    for note in cleaned:
        if note.deal in seen:
            raise InvalidLink(f"a whole loan has one note in each deal; {note.deal} is named twice")
        seen.add(note.deal)
    return cleaned


def link_problem(
    notes: Sequence[NoteRef], linked: Sequence[int | None], occupied: Collection[str]
) -> InvalidLink | None:
    """Why the notes cannot be linked, given the whole loan each is already in (`linked`, same
    order) and the deals the one existing whole loan already has a note in (`occupied`).

    Pure. Unlinked notes may join one existing whole loan; two whole loans are never merged,
    and a whole loan never has two notes in one deal.
    """
    existing = sorted({wl for wl in linked if wl is not None})
    if len(existing) > 1:
        first, second = (
            next(n for n, wl in zip(notes, linked, strict=True) if wl == w) for w in existing[:2]
        )
        return InvalidLink(
            f"{first} is in whole loan {existing[0]} and {second} in whole loan {existing[1]}; "
            "whole loans are never merged"
        )
    if existing and all(wl is not None for wl in linked):
        return InvalidLink(f"these loans are already linked as whole loan {existing[0]}")
    for note, wl in zip(notes, linked, strict=True):
        if wl is None and note.deal in occupied:
            return InvalidLink(f"whole loan {existing[0]} already has a note in {note.deal}")
    return None


def authorize_linking(db: Session, user: str) -> Actor:
    """The actor linking whole loans, or PermissionRefused after auditing the refusal."""
    try:
        roles = roles_of(db, user)
    except UnknownUser as unknown:
        record_refusal(db, user=user, action=LINK_ACTION, message=str(unknown))
        raise
    role = next((role for role in ROLE_ORDER if role in roles and role in LINKERS), None)
    if role is None:
        names = " or ".join(r.value for r in ROLE_ORDER if r in roles) or "a user without roles"
        message = f"{names} may not {LINK_WHOLE_LOANS}"
        record_refusal(db, user=user, action=LINK_ACTION, message=message)
        raise PermissionRefused(message)
    return Actor(user=user, role=role)


def link_whole_loan(db: Session, *, user: str, notes: Sequence[NoteRef]) -> int:
    """Record that the notes are one whole loan; returns its id. Needs a quant.

    Every deal must already be registered (a link never creates a deal) and every loan must be on
    a loaded tape of its deal. The link first waits for any alert build of its deals (by deal
    id), so a build never reads the links before a link lands and writes its alerts after; then
    the deal rows are locked in name order, so two links naming the same deals take turns, and
    the existing links are read under those locks.
    """
    actor = authorize_linking(db, user)
    try:
        cleaned = clean_notes(notes)
        names = sorted({n.deal for n in cleaned})
        found = {name: resolve(db, name) for name in names}
        for name, deal in found.items():
            if deal is None:
                raise InvalidLink(f"no deal named {name!r}")
        for deal_id in sorted({deal.id for deal in found.values() if deal is not None}):
            lock_builds(db, deal_id)
        deal_ids = {name: _locked_deal(db, name) for name in names}
        for note in cleaned:
            if not _on_a_tape(db, deal_ids[note.deal], note.loan):
                raise InvalidLink(f"{note} is not on any loaded loan tape of that deal")
        linked = [_whole_loan_id(db, deal_ids[n.deal], n.loan) for n in cleaned]
        existing = {wl for wl in linked if wl is not None}
        occupied = set(
            db.scalars(
                select(Deal.name)
                .join(WholeLoanNote, WholeLoanNote.deal_id == Deal.id)
                .where(WholeLoanNote.whole_loan_id.in_(existing))
            )
        )
        problem = link_problem(cleaned, linked, occupied)
        if problem is not None:
            raise problem
    except Refused as refusal:
        refuse(
            db,
            refusal,
            user=actor.user,
            role=actor.role.value,
            action=LINK_ACTION,
            subject=WHOLE_LOAN,
        )
    whole_loan_id = next((wl for wl in linked if wl is not None), None)
    if whole_loan_id is None:
        whole = WholeLoan(linked_by=actor.user, linked_at=clock.now())
        db.add(whole)
        db.flush()
        whole_loan_id = whole.id
    db.add_all(
        WholeLoanNote(deal_id=deal_ids[n.deal], loan=n.loan, whole_loan_id=whole_loan_id)
        for n, wl in zip(cleaned, linked, strict=True)
        if wl is None
    )
    db.flush()
    record(
        db,
        user=actor.user,
        role=actor.role.value,
        action=LINK_ACTION,
        subject=WHOLE_LOAN,
        subject_key=str(whole_loan_id),
        after="; ".join(str(n) for n in cleaned),
        detail={"notes": [{"deal": n.deal, "loan": n.loan} for n in cleaned]},
    )
    return whole_loan_id


def _locked_deal(db: Session, name: str) -> int:
    """The registered deal's id, its row locked; InvalidLink if no deal has that name."""
    deal = resolve(db, name)
    if deal is None:
        raise InvalidLink(f"no deal named {name!r}")
    db.execute(select(Deal.id).where(Deal.id == deal.id).with_for_update())
    return deal.id


def _on_a_tape(db: Session, deal_id: int, loan: str) -> bool:
    """Whether any loaded tape of the deal reports the loan."""
    found = db.scalar(
        select(LoanPeriod.loan)
        .where(LoanPeriod.deal_id == deal_id, LoanPeriod.loan == loan)
        .limit(1)
    )
    return found is not None


def whole_loan_notes(db: Session, whole_loan_id: int) -> list[tuple[int, str]]:
    """The (deal id, loan) of every note of the whole loan, by deal."""
    rows = db.execute(
        select(WholeLoanNote.deal_id, WholeLoanNote.loan)
        .where(WholeLoanNote.whole_loan_id == whole_loan_id)
        .order_by(WholeLoanNote.deal_id)
    )
    return [(deal_id, loan) for deal_id, loan in rows]


def _whole_loan_id(db: Session, deal_id: int, loan: str) -> int | None:
    return db.scalar(
        select(WholeLoanNote.whole_loan_id).where(
            WholeLoanNote.deal_id == deal_id, WholeLoanNote.loan == loan
        )
    )


def whole_loans_of(db: Session, deal_id: int) -> dict[str, int]:
    """The deal's loans that are notes of a whole loan, with the whole loan's id."""
    rows = db.execute(
        select(WholeLoanNote.loan, WholeLoanNote.whole_loan_id).where(
            WholeLoanNote.deal_id == deal_id
        )
    )
    return {loan: whole_loan_id for loan, whole_loan_id in rows}
