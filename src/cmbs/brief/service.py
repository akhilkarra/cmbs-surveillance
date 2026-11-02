"""The committee brief on a deal's special-servicing book (grill log Q98). Read-only.

The brief is composed only from what asking gives, as known on one day (today by default):

- the deal's roster (`cmbs.ask.roster.loans`), which opens the brief with the panel's freshness
  and says which loans are in special servicing (Q96);
- for each loan in special servicing, its facts (`cmbs.ask.service.loan_facts`) and its months
  as filed (`cmbs.ask.history.history`).

Each loan in special servicing gets a section: its roster line, then the committee's questions
in a fixed order (`QUESTIONS`), each answered by the panel's own composer
(`cmbs.ask.answer.compose`) over the topics it names, so a field awaiting review follows Q97
exactly as in `ask`: no extracted value, the filer's sentence quoted and labelled, the tape's
value beside it. The 10-D's appraisal reduction is cited to the report table that gives it. A
topic the panel holds nothing on for the loan says so, citing the tape fields the month left
empty. Then the loan's history, condensed to one page (committee_brief.feature's revision): how
many months the panel holds, the first and the last, each cited to its tape, then the last
`SHOWN_MONTHS` months as filed. Last, the topics the panel never holds (`NOT_IN_PANEL`). The
other loans get one line each: returned with its date, paid off since its month, or never
transferred.

Every value is a value asking gives, as it reads there, with one exception the revision asks
for: the number of months held, which counts the months `history` gives. Nothing is summed,
compared or estimated, and nothing is written.
"""

import dataclasses
import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from cmbs import clock
from cmbs.access.roles import Action
from cmbs.access.service import authorize
from cmbs.ask.answer import NOT_IN_DATA, Answer, Citation, Fact, Kind, compose
from cmbs.ask.history import Month, history
from cmbs.ask.roster import RosterLoan, State, loans
from cmbs.ask.service import ASSET_TAGS, TAPE_FIELDS, loan_facts

# The topics a committee asks about that the panel never holds, in the order the brief lists
# them; each is a concept `ask` answers "not in the data" for (`cmbs.ask.answer.OUTSIDE_WORDS`).
NOT_IN_PANEL = (
    "ASER",
    "appraised value",
    "LTV",
    "cap rate",
    "sponsor equity",
    "guarantor",
    "reserves",
    "tenants and leases",
    "maturity date",
    "interest rate",
    "market",
)
NOT_IN_PANEL_HEAD = "Not in the panel:"
HISTORY_HEADING = "History the panel holds (up to the latest six months as filed)"
SHOWN_MONTHS = 6

# The Schedule AL tags a tape reports each topic in, for saying which fields a month left empty.
TAPE_TAGS: dict[str, tuple[str, ...]] = {}
for _spec in (*TAPE_FIELDS, *ASSET_TAGS):
    TAPE_TAGS[_spec.topic] = (*TAPE_TAGS.get(_spec.topic, ()), _spec.tag)
RETURN_TAG = "mostRecentMasterServicerReturnDate"
TRANSFER_TAG = "mostRecentSpecialServicerTransferDate"
BALANCE_TAG = "reportPeriodEndActualBalanceAmount"


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


@dataclass(frozen=True)
class Held:
    """How many months of a loan the panel holds, and the first and last, each citing its tape."""

    months: int
    first: Month
    last: Month

    def text(self) -> str:
        """'21 months held, January 2025 to September 2026 (ABS-EE ... to ABS-EE ...)'."""
        first, last = self.first.citation, self.last.citation
        if self.months == 1:
            return f"1 month held, {self.first.period} ({first.form} {first.accession})"
        return (
            f"{self.months} months held, {self.first.period} to {self.last.period} "
            f"({first.form} {first.accession} to {last.form} {last.accession})"
        )


def condensed(months: Sequence[Month]) -> tuple[Held | None, list[Month]]:
    """The months held, and the last SHOWN_MONTHS of them, from a loan's months oldest first."""
    if not months:
        return None, []
    return Held(len(months), months[0], months[-1]), list(months[-SHOWN_MONTHS:])


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
    cited = month.citation
    tags = ", ".join(value.tape_field for value in month.values)
    return f"{month.period}: {month.reading()} ({cited.form} {cited.accession}: {tags})"


def other_line(row: RosterLoan) -> str:
    """'loan 4, Ridge Industrial: returned to the master servicer on 2023-09-15 (ABS-EE ...)'."""
    name = row.property_name or "property name not reported"
    cited = row.citation
    if row.state is State.RETURNED:
        what, tags = f"returned to the master servicer on {row.since}", (RETURN_TAG,)
    elif row.state is State.PAID_OFF:
        what, tags = f"paid off, zero balance since {row.since}", (BALANCE_TAG,)
        cited = row.paid_off_citation or cited
    else:
        what, tags = "never transferred to the special servicer", (TRANSFER_TAG, RETURN_TAG)
    if row.workout_strategy is not None:
        what += f"; tape workout strategy: {row.workout_strategy}"
    return f"loan {row.loan}, {name}: {what} ({cited.form} {cited.accession}: {', '.join(tags)})"


@dataclass(frozen=True)
class Brief:
    deal: str
    as_of: dt.date
    freshness: str
    period: str | None
    sections: list[Section] = field(default_factory=list)
    others: list[RosterLoan] = field(default_factory=list)

    def text(self) -> str:
        if self.period is None:
            return self.freshness
        lines = [self.freshness]
        if self.sections:
            lines.append(
                f"Committee brief on {self.deal}, as known on {self.as_of.isoformat()}: the "
                f"loans in special servicing on the {self.period} tape, every value cited."
            )
        else:
            lines.append(
                f"Committee brief on {self.deal}, as known on {self.as_of.isoformat()}: no loan "
                f"is in special servicing on the {self.period} tape."
            )
        for section in self.sections:
            lines += ["", *section.lines()]
        if self.others:
            lines += ["", "The other loans:"]
            lines += [f"- {other_line(row)}" for row in self.others]
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


def brief(db: Session, reader: str, *, deal: str, as_of: dt.date | None = None) -> Brief:
    """The committee brief on the deal's special-servicing book as known on `as_of` (today by
    default)."""
    authorize(db, reader, Action.ASK)
    day = as_of or clock.today()
    roster = loans(db, reader, deal=deal, as_of=day)
    if roster.period is None:
        return Brief(roster.deal, day, roster.freshness, None)
    special = [row for row in roster.loans if row.state is State.SPECIAL_SERVICING]
    sections = [
        section_of(db, reader, deal=roster.deal, row=row, period=roster.period, day=day)
        for row in special
    ]
    others = [row for row in roster.loans if row.state is not State.SPECIAL_SERVICING]
    return Brief(roster.deal, day, roster.freshness, roster.period, sections, others)
