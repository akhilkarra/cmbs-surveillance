"""Facts with their citations, and a plain answer composed from them. Pure, no database.

A fact is one value the panel holds for a loan: a field extracted from the servicer's comment
(cited by its 10-D accession and sentence number, with the sentence quoted) or a value the loan
tape reports (cited by its ABS-EE accession and the Schedule AL field). A comment field waiting
for human review is a fact with no value. Nothing here estimates or derives a figure: every
value is one the panel stores, formatted for reading.

`compose` answers a question from the facts, conservatively, and never substitutes one question
for another:
- a request to change something, anywhere in the question ("I want you to mark loan 2 as
  resolved"), is refused: asking is read-only;
- the facts whose topic the question names are stated, each with its citation;
- a topic with a value awaiting review states no extracted value, only that it awaits review:
  an answer must not present as settled what a reviewer has not settled. It may still show what
  is not the model's reading (grill log Q97): the comment's sentence on the field, quoted and
  labelled "unreviewed: not a panel fact", and the tape's values on the topic, each labelled as
  the tape's and cited by its Schedule AL field;
- every other part of the question is "not in the data": a concept the panel does not hold
  (equity, the sponsor, an appraisal, LTV, ...), and a topic it holds but has no value of for
  the loan. A question with no part the panel covers is "not in the data" as a whole.
Recognising topics by their words is deliberately narrow, and a word that could belong to
another subject ("coverage", "current", "strategy") names no topic on its own; the `/cmbs-ask`
skill reads the same facts (`cmbs ask facts`) and composes fuller answers under the same rules.
"""

import datetime as dt
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

NEEDS_REVIEW = "needs review"
REPORT_FORM = "10-D"
COMMENT_FORM = REPORT_FORM
TAPE_FORM = "ABS-EE"
READ_ONLY = "asking is read-only"
NOT_IN_DATA = "not in the data"
# How the filer's own sentence on a field awaiting review is labelled (grill log Q97), and what
# is said instead when the model found no sentence that states the field (support low, Q87).
UNREVIEWED = "unreviewed: not a panel fact"
NO_SENTENCE = "the comment has no sentence on it (unreviewed)"
LOW_SUPPORT = "low"


@dataclass(frozen=True)
class Citation:
    """Where a fact comes from: the filing, and the sentence or report table (10-D) or the tape
    field (ABS-EE). A value a report table dates (an appraisal reduction) carries that date."""

    form: str
    accession: str
    filing_date: dt.date
    url: str
    sentence: int | None = None
    quote: str | None = None
    tape_field: str | None = None
    report_table: str | None = None
    dated: dt.date | None = None

    def text(self) -> str:
        """'10-D 0009900001-26-000418, sentence 3: "The borrower has ..."',
        'ABS-EE 0009900001-26-000419, workoutStrategyCode' or
        '10-D 0009900002-26-000418, Mortgage Loan Detail (Part 2), dated 2026-08-15'."""
        head = f"{self.form} {self.accession}"
        if self.sentence is not None:
            return f'{head}, sentence {self.sentence}: "{self.quote}"'
        if self.report_table is not None:
            dated = f", dated {self.dated.isoformat()}" if self.dated is not None else ""
            return f"{head}, {self.report_table}{dated}"
        return f"{head}, {self.tape_field}"


@dataclass(frozen=True)
class Fact:
    """One value the panel holds for a loan and month; `value` is None while awaiting review.

    A field awaiting review may still carry a citation: the comment's sentence on it, which the
    filer wrote and the model pointed to. It is quoted only as the filer's words, labelled
    "unreviewed: not a panel fact" (grill log Q97), never read as the field's value. `support` is
    the band of the model's support for that sentence (low, unsettled or high): with support
    low no sentence states the field (Q87), so none is quoted."""

    name: str
    label: str
    topic: str
    period: str
    value: str | None
    status: str
    citation: Citation | None
    set_by: str | None = None
    support: str | None = None

    @property
    def awaiting_review(self) -> bool:
        return self.status == NEEDS_REVIEW

    @property
    def from_tape(self) -> bool:
        return self.citation is not None and self.citation.form == TAPE_FORM

    def text(self) -> str:
        """'borrower request: maturity extension (auto-accepted; 10-D ..., sentence 3: "...")'."""
        if self.value is None:
            head = f"{self.label}: awaiting human review ({self.period} comment)"
            quoted = self.unreviewed_quote()
            return head if quoted is None else f"{head}; {quoted}"
        how = self.status if self.set_by is None else f"{self.status} by {self.set_by}"
        cited = f"; {self.citation.text()}" if self.citation is not None else ""
        return f"{self.label}: {self.value} ({self.period}, {how}{cited})"

    def unreviewed_quote(self) -> str | None:
        """For a field awaiting review: 'the comment's sentence on it, unreviewed: not a panel
        fact: "..." (10-D ..., sentence 4)' when its support is not low and its sentence is
        known; NO_SENTENCE when its support is low; None otherwise."""
        if not self.awaiting_review:
            return None
        if self.support == LOW_SUPPORT:
            return NO_SENTENCE
        cited = self.citation
        if cited is None or cited.quote is None:
            return None
        return (
            f'the comment\'s sentence on it, {UNREVIEWED}: "{cited.quote}" '
            f"({cited.form} {cited.accession}, sentence {cited.sentence})"
        )


# What names each topic in a question: regular expressions matched from the start of a word,
# in lower case; `\b` at the end asks for the whole word.
TOPIC_WORDS: dict[str, tuple[str, ...]] = {
    "special servicing transfer": (r"special servic", r"transfer", r"master.servic"),
    "borrower request": (r"borrower", r"forbearance", r"extension\b"),
    "advances": (r"advanc", r"non.?recoverab", r"p&i\b", r"t&i\b"),
    "realized loss": (r"realized loss", r"realised loss", r"loss(?:es)?\b"),
    "liquidation": (
        r"liquidat",
        r"disposition",
        r"disposed\b",
        r"prepa(?:y|id)",
        r"pa(?:y|id).?off",
        r"paid in full",
    ),
    "appraisal reduction": (r"appraisal reduction", r"ara\b"),
    "workout strategy": (r"workout", r"resolution plan"),
    "DSCR": (r"dscr", r"debt service coverage"),
    "occupancy": (r"occupan", r"vacan", r"occupied"),
    "balance": (r"balance\b", r"principal outstanding", r"outstanding principal", r"owed\b"),
    "payment status": (r"delinquen", r"payment status", r"paid through", r"past due"),
    "financials": (r"financials", r"noi\b", r"net operating income", r"operating statement"),
    "modification": (r"modif",),
    "property": (r"property type", r"property name", r"kind of property", r"type of property"),
}

# Concepts a question may ask about that the panel does not hold, by the words that name them.
# A question naming one gets "not in the data" for it, whatever else it asks.
OUTSIDE_WORDS: dict[str, tuple[str, ...]] = {
    "equity": (r"equity",),
    "sponsor": (r"sponsor",),
    # An appraisal reduction is a fact the 10-D gives; an appraisal itself is not held.
    "appraisal": (r"apprais(?!al reduction|al subordinate)",),
    "ASER": (r"aser\b", r"appraisal subordinate"),
    "LTV": (r"ltv\b", r"loan.to.value"),
    "cap rate": (r"cap rate", r"capitali[sz]ation rate"),
    "valuation": (r"valuation", r"value of the property", r"property value", r"worth\b"),
    "guarantor": (r"guarant",),
    "insurance": (r"insur",),
    "tenants and leases": (r"tenant", r"lease", r"rents?\b", r"rent roll"),
    "reserves": (r"reserve", r"escrow"),
    "interest rate": (r"interest rate", r"coupon"),
    "maturity date": (r"maturity date", r"matures?\b"),
    "market": (r"market", r"comparable", r"sale price", r"sold for"),
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
# Words that lead into a request, stripped before reading its first word.
LEAD_INS = (
    "please", "can you", "could you", "would you", "will you", "kindly", "go ahead and",
    "i want you to", "i'd like you to", "i would like you to", "i need you to", "i want to",
    "i'd like to", "you should", "you must", "you need to", "let's", "lets", "let us",
    "make sure to", "be sure to", "then", "also", "and", "now",
)  # fmt: skip
# A question is read in parts: sentences, and clauses joined by "and" or "then".
_PARTS = re.compile(r"[.;:!?,]+|\band\b|\bthen\b")
_LOAN = re.compile(r"\bloan\s+(?:no\.?\s*|number\s+|#\s*)?([0-9][0-9A-Za-z-]*)", re.IGNORECASE)


def _requests_change(part: str) -> bool:
    """One part of a question, read as an imperative: does its first word change data?"""
    text = part.strip().lower()
    stripped = True
    while stripped:
        stripped = False
        for phrase in LEAD_INS:
            if text == phrase or text.startswith(phrase + " "):
                text = text[len(phrase) :].lstrip(" ,")
                stripped = True
    words = re.findall(r"[a-z]+", text)
    return bool(words) and words[0] in WRITE_VERBS


def is_write_request(question: str) -> bool:
    """A request to change data anywhere in the question: 'Mark loan 2 as resolved', 'I want
    you to mark loan 2 as resolved', 'Show the balance, then close the alert'. A question about
    a change ('Was loan 2 marked as resolved?') is not one."""
    return any(_requests_change(part) for part in _PARTS.split(question))


def loan_in(question: str) -> str | None:
    """'Why is loan 2 in special servicing?' -> '2'; None when no loan is named."""
    found = _LOAN.search(question)
    return found.group(1) if found else None


def _named(question: str, vocabulary: dict[str, tuple[str, ...]]) -> list[str]:
    text = question.lower()
    return [
        name
        for name, words in vocabulary.items()
        if any(re.search(r"\b" + word, text) for word in words)
    ]


def topics_in(question: str) -> list[str]:
    """The panel's topics the question names, in TOPIC_WORDS order."""
    return _named(question, TOPIC_WORDS)


def outside_in(question: str) -> list[str]:
    """The concepts the question names that the panel does not hold, in OUTSIDE_WORDS order."""
    return _named(question, OUTSIDE_WORDS)


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
    # Fields awaiting review whose comment sentence is quoted, labelled UNREVIEWED (Q97).
    unreviewed: list[Fact] = field(default_factory=list)
    # What the question asks that the panel does not hold for the loan.
    not_in_data: list[str] = field(default_factory=list)

    def text(self) -> str:
        subject = f"loan {self.loan} of {self.deal}"
        if self.kind is Kind.READ_ONLY:
            return (
                f"{READ_ONLY}: questions can read the panel but never change it. Make changes "
                "through the review queue, overrides or a refresh."
            )
        if self.kind is Kind.NOT_IN_DATA:
            which = f" ({', '.join(self.not_in_data)})" if self.not_in_data else ""
            return (
                f"{NOT_IN_DATA}: the panel does not contain that information{which} for "
                f"{subject}, and nothing is estimated."
            )
        lines = [f"From the panel, for {subject}:"]
        lines += [f"- {fact.text()}" for fact in self.stated]
        quotes = {fact.label: fact.unreviewed_quote() for fact in self.unreviewed}
        lines += [
            f"- the {label} is awaiting human review"
            + (f"; {quotes[label]}" if quotes.get(label) else "")
            for label in self.awaiting_review
        ]
        lines += [
            f"- {name}: {NOT_IN_DATA}; the panel does not contain it, and nothing is estimated"
            for name in self.not_in_data
        ]
        return "\n".join(lines)


def compose(question: str, *, deal: str, loan: str, facts: Sequence[Fact]) -> Answer:
    """The answer the facts give to the question, under the rules in the module docstring."""
    if is_write_request(question):
        return Answer(question, deal, loan, Kind.READ_ONLY)
    asked = topics_in(question)
    relevant = [fact for fact in facts if fact.topic in asked]
    held = {fact.topic for fact in relevant}
    missing = outside_in(question) + [topic for topic in asked if topic not in held]
    if not relevant:
        return Answer(question, deal, loan, Kind.NOT_IN_DATA, not_in_data=missing)
    unsettled = {fact.topic for fact in relevant if fact.awaiting_review}
    # On an unsettled topic only the tape's values are stated: no extracted value is.
    stated = [fact for fact in relevant if fact.topic not in unsettled or fact.from_tape]
    waiting = [fact for fact in relevant if fact.awaiting_review]
    quoted = [fact for fact in waiting if fact.unreviewed_quote() is not None]
    return Answer(
        question,
        deal,
        loan,
        Kind.ANSWERED,
        stated=stated,
        awaiting_review=[fact.label for fact in waiting],
        unreviewed=quoted,
        not_in_data=missing,
    )
