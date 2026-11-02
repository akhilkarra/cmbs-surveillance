"""The committee brief on a deal's special-servicing book (grill log Q98, Q100). Read-only.

The brief is composed only from what asking gives, as known on one day (today by default):

- the deal's roster (`cmbs.ask.roster.loans`), which opens the brief with the panel's freshness
  and says which loans are in special servicing (Q96), and the roster of the tape before, to
  compare property names;
- for each loan in special servicing, its facts (`cmbs.ask.service.loan_facts`) and its months
  as filed (`cmbs.ask.history.history`).

Under the opening lines, notices: when most loans on both the current and the previous tape
carry another property name on the current one, that it may be another deal's tape
(docs/MESS-REGISTER.md M-029); and how many loans the current tape omits or quarantined, and how
many of them were last known in special servicing (Q100).

Each loan in special servicing gets a section: its roster line, then the committee's questions
in a fixed order (`QUESTIONS`), each answered by the panel's own composer
(`cmbs.ask.answer.compose`) over the topics it names, so a field awaiting review follows Q97
exactly as in `ask`: no extracted value, the filer's sentence quoted and labelled, the tape's
value beside it. The 10-D's appraisal reduction is cited to the report table that gives it; the
tape's valuation, maturity date, interest rate and largest tenant are cited to their fields
(Q100). A topic the panel holds nothing on for the loan says so, citing the tape fields the
month left empty. Then the loan's history, condensed to one page: the months held (months whose
tape gives a row of the loan; those that quarantined or omitted it are counted apart), the first
and the last, each cited to its tape, then the last `SHOWN_MONTHS` months of tapes as filed.
Last, the topics no filing field gives (`NOT_IN_PANEL`).

A loan the current tape omits or quarantined that was last known in special servicing gets a
short section (`LastKnown`): its state, marked as last known on its own last tape, why the
current tape does not show it, and that last month as filed. The brief never says no loan is in
special servicing while one is last known there.

The other loans fit a page (`others_lines`): one line each for a loan of interest (returned with
its date, liquidated with its realized loss, paid off since its month, or a loan the tape does
not show last known in another state), then one cited line for the loans never transferred, one
for those off the tape and last known never transferred or paid off, and one for those the tape
quarantined for the same reasons and last known so.

Every value is a value asking gives, as it reads there, with counts as the only exceptions: the
months held, the loans a grouped line names, the loans a notice counts and the names that
differ. Nothing is summed, compared beyond those counts, or estimated, and nothing is written.
"""

import dataclasses
import datetime as dt
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from cmbs import clock
from cmbs.access.roles import Action
from cmbs.access.service import authorize
from cmbs.ask.answer import NOT_IN_DATA, Answer, Citation, Fact, Kind, compose
from cmbs.ask.history import Month, history
from cmbs.ask.roster import ROSTER_FIELDS, Roster, RosterLoan, State, loans
from cmbs.ask.service import ASSET_TAGS, PROPERTY_TAGS, TAPE_FIELDS, UnknownLoan, loan_facts
from cmbs.panel import service as panel

# The topics a committee asks about that no filing field gives, in the order the brief lists
# them; each is a concept `ask` answers "not in the data" for (`cmbs.ask.answer.OUTSIDE_WORDS`).
# What the tape does give (the valuation, maturity, rate and largest tenant) is not here (Q100).
NOT_IN_PANEL = ("ASER", "LTV", "cap rate", "sponsor and guarantor", "reserves")
NOT_IN_PANEL_HEAD = "Not in the panel:"
HISTORY_HEADING = "History the panel holds (up to the latest six months as filed)"
SHOWN_MONTHS = 6

# The Schedule AL tags a tape reports each topic in, for saying which fields a month left empty.
TAPE_TAGS: dict[str, tuple[str, ...]] = {}
for _spec in (*TAPE_FIELDS, *ASSET_TAGS, *PROPERTY_TAGS):
    TAPE_TAGS[_spec.topic] = (*TAPE_TAGS.get(_spec.topic, ()), _spec.tag)
RETURN_TAG = "mostRecentMasterServicerReturnDate"
TRANSFER_TAG = "mostRecentSpecialServicerTransferDate"
BALANCE_TAG = "reportPeriodEndActualBalanceAmount"
LOSS_TAG = "realizedLossToTrustAmount"
# How the roster and history mark a loan or month the load quarantined (`cmbs.ask.roster`).
QUARANTINED = "quarantined:"


@dataclass(frozen=True)
class Question:
    """One question a committee asks of a loan, and the panel's topics that answer it."""

    heading: str
    topics: tuple[str, ...]

    @property
    def asked(self) -> str:
        """The question as the composer reads it: its topics by name ('DSCR and occupancy')."""
        return " and ".join(self.topics)


QUESTIONS = (
    Question("Transfer date and reason", ("special servicing transfer",)),
    Question("Payment status", ("payment status",)),
    Question("DSCR and occupancy, with their dates", ("DSCR", "occupancy", "financials")),
    Question("The servicer's stated plan", ("workout strategy", "modification")),
    Question("The borrower's request", ("borrower request",)),
    Question("Advances outstanding", ("advances",)),
    Question(
        "Value, term and tenancy", ("valuation", "maturity date", "interest rate", "largest tenant")
    ),
    Question(
        "Appraisal reduction, loss and liquidation",
        ("appraisal reduction", "realized loss", "liquidation"),
    ),
)


def not_reported(topic: str, tape: Citation, period: str) -> str:
    """What the brief says of a topic the panel holds nothing on for the loan: for a topic the
    tape reports, which fields of which month's tape were empty."""
    tags = TAPE_TAGS.get(topic)
    if not tags:
        return f"{topic}: {NOT_IN_DATA}; the panel holds none for this loan"
    return (
        f"{topic}: not reported on the {period} tape, so the panel holds none "
        f"({tape.form} {tape.accession}: {', '.join(tags)})"
    )


@dataclass(frozen=True)
class Item:
    """A committee question answered for a loan: the composer's answer, and what it lacks."""

    heading: str
    answer: Answer
    not_reported: list[str] = field(default_factory=list)

    @property
    def stated(self) -> list[Fact]:
        return self.answer.stated

    def lines(self) -> list[str]:
        lines = [f"{self.heading}:"]
        if self.answer.kind is Kind.ANSWERED:
            # The answer's own lines, after its head; what it lacks is said below instead.
            lines += dataclasses.replace(self.answer, not_in_data=[]).text().splitlines()[1:]
        return lines + [f"- {line}" for line in self.not_reported]


def answer_item(
    question: Question, *, deal: str, row: RosterLoan, period: str, facts: Sequence[Fact]
) -> Item:
    """The composer's answer to the question from the loan's facts; a topic it holds nothing
    on is said to be not reported."""
    answer = compose(question.asked, deal=deal, loan=row.loan, facts=facts)
    missing = [not_reported(topic, row.citation, period) for topic in answer.not_in_data]
    return Item(question.heading, answer, missing)


def _months(count: int) -> str:
    return f"{count} month" if count == 1 else f"{count} months"


@dataclass(frozen=True)
class Held:
    """How many months' tapes give a row of the loan, the first and the last of them, each
    citing its tape, and how many months' tapes quarantined it or omitted it. Only a month with
    a row counts as held: a tape that quarantined or omitted the loan holds nothing of it."""

    months: int
    first: Month
    last: Month
    quarantined: int = 0
    missing: int = 0

    def text(self) -> str:
        """'21 months held, January 2025 to September 2026 (ABS-EE ... to ABS-EE ...)', with
        ', besides 1 month quarantined and 2 months not on the tape' before the citation."""
        first, last = self.first.citation, self.last.citation
        held = f"{_months(self.months)} held, {self.first.period}"
        cited = f"{first.form} {first.accession}"
        if self.months > 1:
            held += f" to {self.last.period}"
            cited += f" to {last.form} {last.accession}"
        besides = [
            f"{_months(count)} {what}"
            for count, what in (
                (self.quarantined, "quarantined"),
                (self.missing, "not on the tape"),
            )
            if count
        ]
        if besides:
            held += f", besides {' and '.join(besides)}"
        return f"{held} ({cited})"


def condensed(months: Sequence[Month]) -> tuple[Held | None, list[Month]]:
    """The months held, and the last SHOWN_MONTHS months of tapes (with any that quarantined or
    omitted the loan, as they read), from a loan's months oldest first."""
    rows = [month for month in months if month.note is None]
    if not rows:
        return None, list(months[-SHOWN_MONTHS:])
    quarantined = sum(1 for m in months if m.note is not None and m.note.startswith(QUARANTINED))
    missing = len(months) - len(rows) - quarantined
    return Held(len(rows), rows[0], rows[-1], quarantined, missing), list(months[-SHOWN_MONTHS:])


@dataclass(frozen=True)
class Section:
    """A loan in special servicing: its roster row, the committee's questions, its months."""

    loan: RosterLoan
    items: list[Item]
    held: Held | None
    # The last SHOWN_MONTHS months as filed, oldest first.
    history: list[Month]

    def lines(self) -> list[str]:
        lines = [self.loan.text()]
        for item in self.items:
            lines += item.lines()
        lines.append(f"{HISTORY_HEADING}:")
        if self.held is not None:
            lines.append(f"- {self.held.text()}")
        lines += [f"- {month_line(month)}" for month in self.history]
        lines.append(f"{NOT_IN_PANEL_HEAD} {', '.join(NOT_IN_PANEL)}.")
        return lines


def month_line(month: Month) -> str:
    """'September 2026: ending balance $30,000,000.00; ... (ABS-EE <accession>: <its fields>)'."""
    return f"{month.period}: {month.reading()} ({_cites((month.citation, _tags(month)))})"


def _tags(month: Month) -> tuple[str, ...]:
    return tuple(value.tape_field for value in month.values)


def _cites(*cited: tuple[Citation, Sequence[str]]) -> str:
    """'ABS-EE <accession>: <field>, <field>; ABS-EE <accession>: <field>'; a filing cited
    twice is named once with all its fields, and a tape cited for no field (a month that omits
    the loan) is named alone."""
    merged: dict[tuple[str, str], list[str]] = {}
    for c, tags in cited:
        fields = merged.setdefault((c.form, c.accession), [])
        fields += [tag for tag in tags if tag not in fields]
    return "; ".join(
        f"{form} {accession}" + (f": {', '.join(tags)}" if tags else "")
        for (form, accession), tags in merged.items()
    )


def other_line(row: RosterLoan) -> str:
    """'loan 4, Ridge Industrial: returned to the master servicer on 2023-09-15 (ABS-EE ...)'.

    A loan the current tape omits or whose load quarantined it reads as the roster reads it:
    why, then its last known state (Q99), citing the tape the roster cites."""
    name = row.property_name or "property name not reported"
    if row.note is not None:
        return (
            f"loan {row.loan}, {name}: {row.reading()} ({_cites((row.citation, row.tape_fields))})"
        )
    cites = [(row.citation, (TRANSFER_TAG, RETURN_TAG))]
    if row.state is State.RETURNED:
        what = f"returned to the master servicer on {row.since}"
        cites = [(row.citation, (RETURN_TAG,))]
    elif row.state in (State.PAID_OFF, State.LIQUIDATED):
        what = f"paid off, zero balance since {row.since}"
        if row.state is State.LIQUIDATED:
            what = f"liquidated, realized loss {row.realized_loss}, zero balance since {row.since}"
        cites = [(row.paid_off_citation or row.citation, (BALANCE_TAG,))]
        if row.loss_citation is not None:
            cites.append((row.loss_citation, (LOSS_TAG,)))
    else:
        what = "never transferred to the special servicer"
    if row.workout_strategy is not None:
        what += f"; tape workout strategy: {row.workout_strategy}"
    return f"loan {row.loan}, {name}: {what} ({_cites(*cites)})"


# A loan off the current tape, as the roster notes it (`cmbs.ask.roster.off_tape`).
_OFF_TAPE = re.compile(r"^(?P<missing>not on the .+ tape); last reported (?P<last>.+?), balance ")
# Last known states of a loan the tape does not show that hold no surveillance interest: grouped.
_QUIET = (State.NEVER_TRANSFERRED, State.PAID_OFF)
_QUIET_TAGS = {
    State.NEVER_TRANSFERRED: (TRANSFER_TAG, RETURN_TAG),
    State.PAID_OFF: (BALANCE_TAG,),
}


def _loans(rows: Sequence[RosterLoan]) -> str:
    """'loan 1 (1 loan)', 'loans 1, 4, 5 (3 loans)'."""
    names = ", ".join(row.loan for row in rows)
    return f"loan {names} (1 loan" if len(rows) == 1 else f"loans {names} ({len(rows)} loans"


def others_lines(rows: Sequence[RosterLoan], period: str) -> list[str]:
    """The other loans, fitted to a page: one line each for the loans of interest (returned,
    liquidated, paid off, and a loan the `period` tape does not show when it was last known
    in another state), in the roster's order; then one line for the loans never transferred,
    one for each tape that omits loans last known never transferred or paid off, and one for
    the loans the `period` tape quarantined for the same reasons and last known so, grouped by
    that state."""
    lines: list[str] = []
    never: dict[str, list[RosterLoan]] = {}
    gone: dict[tuple[str, str, State, str], list[RosterLoan]] = {}
    held: dict[tuple[str, State, str], list[RosterLoan]] = {}
    for row in rows:
        found = _OFF_TAPE.match(row.note or "")
        quiet = row.state is not None and row.state in _QUIET
        if row.note is None and row.state is State.NEVER_TRANSFERRED:
            never.setdefault(row.citation.accession, []).append(row)
        elif found is not None and quiet and row.state is not None:
            key = (found["missing"], found["last"], row.state, row.citation.accession)
            gone.setdefault(key, []).append(row)
        elif row.note is not None and row.note.startswith(QUARANTINED) and quiet:
            assert row.state is not None
            held.setdefault((row.note, row.state, row.citation.accession), []).append(row)
        else:
            lines.append(other_line(row))
    for group in never.values():
        tags = (TRANSFER_TAG, RETURN_TAG)
        lines.append(f"never transferred: {_loans(group)}) ({_cites((group[0].citation, tags))})")
    for (missing, last, state, _), group in gone.items():
        cited = _cites((group[0].citation, _QUIET_TAGS[state]))
        lines.append(
            f"{missing}, last known {state.value}: {_loans(group)}; last reported {last}) ({cited})"
        )
    for (note, state, _), group in held.items():
        reasons = note.removeprefix(QUARANTINED).strip()
        lines.append(
            f"quarantined on the {period} tape ({reasons}), last known {state.value}: "
            f"{_loans(group)}) ({_cites((group[0].citation, ()))})"
        )
    return lines


@dataclass(frozen=True)
class LastKnown:
    """A loan the current tape does not show (omitted or quarantined) that was last known in
    special servicing: a short section on its last month as filed, so the brief never drops it
    (grill log Q100)."""

    loan: RosterLoan
    tape: Citation
    month: Month

    def lines(self) -> list[str]:
        row, month = self.loan, self.month
        name = row.property_name or "property name not reported"
        last = _cites((month.citation, ROSTER_FIELDS))
        lines = [
            f"loan {row.loan}, {name}, last known on the {month.period} tape: "
            f"{row.state_reading()} ({last})"
        ]
        why = _cites((self.tape, ()))
        if _OFF_TAPE.match(row.note or ""):
            why = _cites((self.tape, ()), (month.citation, (BALANCE_TAG,)))
        lines.append(f"- {row.note} ({why})")
        lines.append(f"- {month_line(month)}")
        return lines


def _were(count: int) -> tuple[str, str, str]:
    return ("loan", "is", "was") if count == 1 else ("loans", "are", "were")


def unseen_notice(rows: Sequence[RosterLoan], period: str, *, quarantined: bool) -> str | None:
    """'1 loan is not on the September 2026 tape and was last known in special servicing.', or
    the loans the tape quarantined, saying how many were last known in special servicing."""
    count = len(rows)
    if not count:
        return None
    special = sum(1 for row in rows if row.state is State.SPECIAL_SERVICING)
    noun, is_, was = _were(count)
    head = (
        f"{count} {noun} on the {period} tape {was} quarantined"
        if quarantined
        else f"{count} {noun} {is_} not on the {period} tape"
    )
    if special == count:
        return f"{head} and {was} last known in special servicing."
    if special:
        return f"{head}, {special} of them last known in special servicing."
    return f"{head}, none last known in special servicing."


def renamed_notice(current: Roster, previous: Roster) -> str | None:
    """When most loans on both tapes carry another property name on the current one, the
    current tape may be another deal's (docs/MESS-REGISTER.md M-029): say so."""
    if previous.period is None or previous.period == current.period:
        return None
    before = {row.loan: row.property_name for row in previous.loans if row.note is None}
    pairs = [
        (before[row.loan], row.property_name)
        for row in current.loans
        if row.note is None and row.loan in before
    ]
    named = [(a, b) for a, b in pairs if a and b]
    differ = sum(1 for a, b in named if a.strip().casefold() != b.strip().casefold())
    if not named or differ * 2 <= len(named):
        return None
    return (
        f"The property names of {differ} of the {len(named)} loans on both the {current.period} "
        f"and {previous.period} tapes differ: the {current.period} tape may be another deal's "
        "(docs/MESS-REGISTER.md M-029)."
    )


@dataclass(frozen=True)
class Brief:
    deal: str
    as_of: dt.date
    freshness: str
    period: str | None
    sections: list[Section] = field(default_factory=list)
    others: list[RosterLoan] = field(default_factory=list)
    last_known: list[LastKnown] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)

    def text(self) -> str:
        if self.period is None:
            return self.freshness
        lines = [self.freshness]
        head = f"Committee brief on {self.deal}, as known on {self.as_of.isoformat()}: "
        # A loan the tape does not show but last known in special servicing is never left
        # out of "no loan is in special servicing" (grill log Q100).
        unseen = self.last_known or any(row.state is State.SPECIAL_SERVICING for row in self.others)
        if self.sections or unseen:
            what = f"the loans in special servicing on the {self.period} tape"
            if unseen:
                what += ", and those last known there"
            lines.append(f"{head}{what}, every value cited.")
        else:
            lines.append(f"{head}no loan is in special servicing on the {self.period} tape.")
        lines += self.notices
        for section in self.sections:
            lines += ["", *section.lines()]
        for known in self.last_known:
            lines += ["", *known.lines()]
        if self.others:
            lines += ["", "The other loans:"]
            lines += [f"- {line}" for line in others_lines(self.others, self.period)]
        return "\n".join(lines)


def section_of(
    db: Session, reader: str, *, deal: str, row: RosterLoan, period: str, day: dt.date
) -> Section:
    facts = loan_facts(db, reader, deal=deal, loan=row.loan, as_of=day).facts
    items = [
        answer_item(question, deal=deal, row=row, period=period, facts=facts)
        for question in QUESTIONS
    ]
    held, shown = condensed(history(db, reader, deal=deal, loan=row.loan, as_of=day).months)
    return Section(row, items, held, shown)


def split_book(
    rows: Sequence[RosterLoan],
) -> tuple[list[RosterLoan], list[RosterLoan], list[RosterLoan]]:
    """The loans to brief (in special servicing on the current tape), those the tape does not
    show (omitted or quarantined) that were last known in special servicing, and the others,
    each in the roster's order."""
    special = [row for row in rows if row.state is State.SPECIAL_SERVICING and row.note is None]
    unseen = [row for row in rows if row.state is State.SPECIAL_SERVICING and row.note is not None]
    return special, unseen, [row for row in rows if row not in special and row not in unseen]


def last_known_of(
    db: Session, reader: str, *, deal: str, row: RosterLoan, tape: Citation, day: dt.date
) -> LastKnown | None:
    """The loan's last month as filed with a row, from its history; None when the panel holds
    none."""
    try:
        months = history(db, reader, deal=deal, loan=row.loan, as_of=day).months
    except UnknownLoan:
        return None
    rows = [month for month in months if month.note is None]
    return LastKnown(row, tape, rows[-1]) if rows else None


def _tape_of(roster: Roster) -> Citation | None:
    """The current tape, as the roster's rows cite it."""
    for row in roster.loans:
        if row.note is None or row.note.startswith(QUARANTINED):
            return row.citation
    return None


def brief(db: Session, reader: str, *, deal: str, as_of: dt.date | None = None) -> Brief:
    """The committee brief on the deal's special-servicing book as known on `as_of` (today by
    default). All of it is read in one snapshot, so a load committing meanwhile cannot make its
    roster, facts and histories disagree."""
    panel.snapshot(db)
    authorize(db, reader, Action.ASK)
    day = as_of or clock.today()
    roster = loans(db, reader, deal=deal, as_of=day)
    if roster.period is None:
        return Brief(roster.deal, day, roster.freshness, None)
    period = roster.period
    special, unseen, others = split_book(roster.loans)
    sections = [
        section_of(db, reader, deal=roster.deal, row=row, period=period, day=day) for row in special
    ]
    tape = _tape_of(roster)
    notices = [
        notice
        for notice in (
            unseen_notice(
                [r for r in roster.loans if _OFF_TAPE.match(r.note or "")],
                period,
                quarantined=False,
            ),
            unseen_notice(
                [r for r in roster.loans if (r.note or "").startswith(QUARANTINED)],
                period,
                quarantined=True,
            ),
        )
        if notice is not None
    ]
    if tape is not None:
        previous = loans(db, reader, deal=deal, as_of=tape.filing_date - dt.timedelta(days=1))
        renamed = renamed_notice(roster, previous)
        if renamed is not None:
            notices.insert(0, renamed)
    known: list[LastKnown] = []
    for row in unseen:
        found = None
        if tape is not None:
            found = last_known_of(db, reader, deal=roster.deal, row=row, tape=tape, day=day)
        if found is None:
            others.append(row)
        else:
            known.append(found)
    return Brief(roster.deal, day, roster.freshness, period, sections, others, known, notices)
