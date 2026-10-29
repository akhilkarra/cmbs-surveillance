"""Facts with their citations, and a plain answer composed from them. Pure, no database.

A fact is one value the panel holds for a loan: a field extracted from the servicer's comment
(cited by its 10-D accession and sentence number, with the sentence quoted) or a value the loan
tape reports (cited by its ABS-EE accession and the Schedule AL field). A comment field waiting
for human review is a fact with no value. Nothing here estimates or derives a figure: every
value is one the panel stores, formatted for reading.

`compose` answers a question from the facts, conservatively:
- a request to change something ("Mark loan 2 as resolved") is refused: asking is read-only;
- the facts whose topic the question names are stated, each with its citation;
- a topic with a value awaiting review states no value from any source, only that it awaits
  review: an answer must not present as settled what a reviewer has not settled;
- a question no fact covers gets "not in the data".
Recognising topics by their words is deliberately narrow; the `/cmbs-ask` skill reads the same
facts (`cmbs ask facts`) and composes fuller answers under the same rules.
"""

import datetime as dt
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

NEEDS_REVIEW = "needs review"
COMMENT_FORM = "10-D"
TAPE_FORM = "ABS-EE"
READ_ONLY = "asking is read-only"
NOT_IN_DATA = "not in the data"


@dataclass(frozen=True)
class Citation:
    """Where a fact comes from: the filing, and the sentence (10-D) or tape field (ABS-EE)."""

    form: str
    accession: str
    filing_date: dt.date
    url: str
    sentence: int | None = None
    quote: str | None = None
    tape_field: str | None = None

    def text(self) -> str:
        """'10-D 0009900001-26-000418, sentence 3: "The borrower has ..."'."""
        head = f"{self.form} {self.accession}"
        if self.sentence is not None:
            return f'{head}, sentence {self.sentence}: "{self.quote}"'
        return f"{head}, {self.tape_field}"


@dataclass(frozen=True)
class Fact:
    """One value the panel holds for a loan and month; `value` is None while awaiting review."""

    name: str
    label: str
    topic: str
    period: str
    value: str | None
    status: str
    citation: Citation | None
    set_by: str | None = None

    @property
    def awaiting_review(self) -> bool:
        return self.status == NEEDS_REVIEW

    def text(self) -> str:
        """'borrower request: maturity extension (auto-accepted; 10-D ..., sentence 3: "...")'."""
        if self.value is None:
            return f"{self.label}: awaiting human review ({self.period} comment)"
        how = self.status if self.set_by is None else f"{self.status} by {self.set_by}"
        cited = f"; {self.citation.text()}" if self.citation is not None else ""
        return f"{self.label}: {self.value} ({self.period}, {how}{cited})"


# What names each topic in a question: regular expressions matched from the start of a word,
# in lower case; `\b` at the end asks for the whole word.
TOPIC_WORDS: dict[str, tuple[str, ...]] = {
    "special servicing transfer": (r"special servic", r"transfer"),
    "borrower request": (r"borrower", r"request", r"extension\b", r"forbearance", r"want"),
    "workout strategy": (r"workout", r"strateg", r"resolution plan"),
    "DSCR": (r"dscr", r"debt service", r"coverage"),
    "occupancy": (r"occupan", r"vacan", r"occupied"),
    "balance": (r"balance", r"outstanding", r"owed\b"),
    "payment status": (r"delinquen", r"payment status", r"paid through", r"late\b", r"current\b"),
    "financials": (r"financials", r"noi\b", r"net operating income", r"operating statement"),
    "modification": (r"modif",),
    "property": (r"property type", r"property name", r"kind of property", r"type of property"),
}

# Verbs that ask the panel to change something, as the first word of a request.
WRITE_VERBS = frozenset(
    {
        "mark", "set", "change", "update", "edit", "delete", "remove", "close", "reopen",
        "resolve", "acknowledge", "assign", "approve", "reject", "override", "decide",
        "decline", "load", "refresh", "run", "backfill", "add", "create", "record", "write",
        "flag", "unflag", "clear", "reset", "replace", "correct", "fix", "save", "store",
        "import", "insert", "propose",
    }
)  # fmt: skip
POLITE = ("please", "can you", "could you", "would you", "will you", "kindly", "go ahead and")
_LOAN = re.compile(r"\bloan\s+(?:no\.?\s*|number\s+|#\s*)?([0-9][0-9A-Za-z-]*)", re.IGNORECASE)


def is_write_request(question: str) -> bool:
    """An imperative to change data: 'Mark loan 2 as resolved', 'Please close the alert'."""
    text = question.strip().lower()
    stripped = True
    while stripped:
        stripped = False
        for phrase in POLITE:
            if text.startswith(phrase + " ") or text.startswith(phrase + ","):
                text = text[len(phrase) :].lstrip(" ,")
                stripped = True
    words = re.findall(r"[a-z]+", text)
    return bool(words) and words[0] in WRITE_VERBS


def loan_in(question: str) -> str | None:
    """'Why is loan 2 in special servicing?' -> '2'; None when no loan is named."""
    found = _LOAN.search(question)
    return found.group(1) if found else None


def topics_in(question: str) -> list[str]:
    """The topics the question names, in TOPIC_WORDS order."""
    text = question.lower()
    return [
        topic
        for topic, words in TOPIC_WORDS.items()
        if any(re.search(r"\b" + word, text) for word in words)
    ]


class Kind(StrEnum):
    ANSWERED = "answered"
    NOT_IN_DATA = "not in the data"
    READ_ONLY = "read-only"


@dataclass(frozen=True)
class Answer:
    question: str
    deal: str
    loan: str
    kind: Kind
    stated: list[Fact] = field(default_factory=list)
    awaiting_review: list[str] = field(default_factory=list)

    def text(self) -> str:
        subject = f"loan {self.loan} of {self.deal}"
        if self.kind is Kind.READ_ONLY:
            return (
                f"{READ_ONLY}: questions can read the panel but never change it. Make changes "
                "through the review queue, overrides or a refresh."
            )
        if self.kind is Kind.NOT_IN_DATA:
            return (
                f"{NOT_IN_DATA}: the panel does not contain that information for {subject}, "
                "and nothing is estimated."
            )
        lines = [f"From the panel, for {subject}:"]
        lines += [f"- {fact.text()}" for fact in self.stated]
        lines += [f"- the {label} is awaiting human review" for label in self.awaiting_review]
        return "\n".join(lines)


def compose(question: str, *, deal: str, loan: str, facts: Sequence[Fact]) -> Answer:
    """The answer the facts give to the question, under the rules in the module docstring."""
    if is_write_request(question):
        return Answer(question, deal, loan, Kind.READ_ONLY)
    asked = topics_in(question)
    relevant = [fact for fact in facts if fact.topic in asked]
    if not relevant:
        return Answer(question, deal, loan, Kind.NOT_IN_DATA)
    unsettled = {fact.topic for fact in relevant if fact.awaiting_review}
    stated = [fact for fact in relevant if fact.topic not in unsettled]
    waiting = [fact.label for fact in relevant if fact.awaiting_review]
    return Answer(question, deal, loan, Kind.ANSWERED, stated, waiting)
