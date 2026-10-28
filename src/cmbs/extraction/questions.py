"""Every question asked of the model about a comment: prompt version 3.

All question wording lives here, so it can be read and audited in one place. Changing any of it
is a new prompt version: the settings ledger approves it with a quality report (grill log Q46),
and its recordings are keyed apart from the old ones.

The state is the comment as the servicer wrote it. For each field, one request asks three
questions (grill log Q15):

- `<field>.value`: a Choice of the field's value. A category's options are its names, each with
  a description; a figure's options are the figures of its kind printed in the comment, plus
  "none of these" (a lone candidate would otherwise be a choice of one).
- `<field>.support`: a Noul, whether one sentence of the comment states the field. This is the
  null case: as of Jev 1.14 a companion Noul works better than a "none of the above" option.
- `<field>.sentence`: a Choice of the sentence that states it, the options being the comment's
  sentences verbatim, so the evidence is always text the comment contains.

A figure field whose kind the comment never prints (no ratio, say) is not asked: nothing in the
comment could be the value, so it is not stated.
"""

from dataclasses import dataclass
from typing import Any

from cmbs.errors import Refused
from cmbs.extraction.fields import FIELDS, FieldSpec, Kind, candidates
from cmbs.extraction.sentences import Sentence
from cmbs.model.types import Choice, Noul, NoulCriteria, Question

PROMPT_VERSION = 3
NONE_OF_THESE = "none of these"
NONE_DESCRIPTION = "The comment does not give this figure; the figures listed are other figures."


class UnknownPromptVersion(Refused):
    """The settings name a prompt version this code does not ask."""


@dataclass(frozen=True)
class Wording:
    value: str
    support: str
    support_true: str
    support_false: str
    sentence: str
    descriptions: dict[str, str | None]


WORDING: dict[str, Wording] = {
    "transfer_reason": Wording(
        value="Why was the loan transferred to special servicing?",
        support="Does a sentence of `comment` say why the loan was transferred to special "
        "servicing?",
        support_true="A sentence names the reason for the transfer, such as a default.",
        support_false="The comment mentions the transfer or the loan's troubles without "
        "saying what caused the transfer.",
        sentence="Which sentence says why the loan was transferred to special servicing?",
        descriptions={
            "imminent monetary default": "The servicer expects the borrower to stop paying "
            "soon, but payments have not yet been missed.",
            "monetary default": "The borrower has missed required payments.",
            "imminent maturity default": "The borrower is not expected to repay the loan "
            "when it matures.",
            "maturity default": "The loan matured and was not repaid.",
            "non-monetary default": "The borrower broke a loan term other than paying, such "
            "as a cash management or reporting requirement.",
            "bankruptcy": "The borrower or a guarantor filed for bankruptcy.",
            "other": "A reason that is none of the above.",
        },
    ),
    "transfer_date": Wording(
        value="On which date was the loan transferred to special servicing?",
        support="Does a sentence of `comment` give the date the loan was transferred to "
        "special servicing?",
        support_true="A sentence gives the date of the transfer to special servicing.",
        support_false="The comment gives dates of other events, such as financial statements, "
        "but not the transfer date.",
        sentence="Which sentence gives the date the loan was transferred to special servicing?",
        descriptions={},
    ),
    "dscr": Wording(
        value="Which figure is the property's debt service coverage ratio (DSCR)?",
        support="Does a sentence of `comment` give the property's debt service coverage ratio "
        "(DSCR)?",
        support_true="A sentence gives the DSCR as a number of times, such as 1.25x.",
        support_false="The comment gives no DSCR, or only other figures such as occupancy.",
        sentence="Which sentence gives the property's debt service coverage ratio (DSCR)?",
        descriptions={},
    ),
    "dscr_as_of": Wording(
        value="As of which date was the debt service coverage ratio (DSCR) measured?",
        support="Does a sentence of `comment` give the date the debt service coverage ratio "
        "(DSCR) was measured as of?",
        support_true="A sentence gives the date or period end the DSCR was measured as of.",
        support_false="The comment gives a DSCR with no date, or gives no DSCR.",
        sentence="Which sentence gives the date the debt service coverage ratio (DSCR) was "
        "measured as of?",
        descriptions={},
    ),
    "occupancy": Wording(
        value="Which figure is the property's occupancy?",
        support="Does a sentence of `comment` give the property's occupancy?",
        support_true="A sentence gives the share of the property that is occupied or leased.",
        support_false="The comment gives no occupancy, or only other percentages such as an "
        "interest rate.",
        sentence="Which sentence gives the property's occupancy?",
        descriptions={},
    ),
    "occupancy_as_of": Wording(
        value="As of which date was the property's occupancy measured?",
        support="Does a sentence of `comment` give the date the property's occupancy was "
        "measured as of?",
        support_true="A sentence gives the date the occupancy figure was measured as of.",
        support_false="The comment gives an occupancy with no date, or gives no occupancy.",
        sentence="Which sentence gives the date the property's occupancy was measured as of?",
        descriptions={},
    ),
    "borrower_request": Wording(
        value="What has the borrower asked the lender or special servicer for?",
        support="Does a sentence of `comment` say the borrower has asked for something?",
        support_true="A sentence says the borrower requested or proposed something.",
        support_false="The comment describes the servicer's own plans or actions, not a "
        "request from the borrower.",
        sentence="Which sentence says what the borrower has asked for?",
        descriptions={
            "maturity extension": "More time to repay the loan after its maturity date.",
            "forbearance": "A temporary pause in, or reduction of, payments.",
            "loan modification": "A lasting change to the loan's terms, such as its rate or "
            "amortization.",
            "discounted payoff": "Repaying the loan for less than the amount owed.",
            "loan assumption": "A new borrower taking over the loan.",
            "collateral release": "Releasing a property, or part of one, from the loan.",
            "other": "A request that is none of the above.",
        },
    ),
    "request_status": Wording(
        value="Where does the borrower's request stand?",
        support="Does a sentence of `comment` say where the borrower's request stands?",
        support_true="A sentence says the request is being reviewed, was approved or was rejected.",
        support_false="The comment mentions a request but says nothing about its progress.",
        sentence="Which sentence says where the borrower's request stands?",
        descriptions={
            "pending": "The request is being reviewed or negotiated; no decision is stated.",
            "granted": "The lender or servicer approved or agreed to the request.",
            "denied": "The lender or servicer rejected the request.",
        },
    ),
    "workout_strategy": Wording(
        value="Which workout strategy is the special servicer pursuing?",
        support="Does a sentence of `comment` name a workout strategy the special servicer is "
        "pursuing?",
        support_true="A sentence names a strategy the servicer has chosen, such as foreclosure "
        "or a modification.",
        support_false="The comment describes a borrower request, or the servicer reviewing "
        "one, without naming a strategy the servicer has chosen.",
        sentence="Which sentence names the workout strategy the special servicer is pursuing?",
        descriptions={
            "property in REO": "The lender owns the property after foreclosure (real estate "
            "owned).",
            "representations and warranties": "A claim against the loan seller for breaching "
            "its representations and warranties.",
            "pending return to master servicer": "The loan is performing again and will "
            "return to the master servicer.",
        },
    ),
}


@dataclass(frozen=True)
class Plan:
    """The questions for one comment, and which fields they cover."""

    questions: dict[str, Question]
    state: dict[str, Any]
    asked: tuple[str, ...]
    options: dict[str, tuple[str, ...]]


def check_prompt_version(version: int) -> None:
    if version != PROMPT_VERSION:
        raise UnknownPromptVersion(
            f"prompt version {version} is not defined here; this code asks prompt version "
            f"{PROMPT_VERSION}"
        )


def value_name(field: str) -> str:
    return f"{field}.value"


def support_name(field: str) -> str:
    return f"{field}.support"


def sentence_name(field: str) -> str:
    return f"{field}.sentence"


def _options(spec: FieldSpec, comment: str) -> tuple[str, ...]:
    return spec.options if spec.kind is Kind.CATEGORY else candidates(spec, comment)


def _value_question(spec: FieldSpec, wording: Wording, options: tuple[str, ...]) -> Choice:
    criteria = {option: wording.descriptions.get(option) for option in options}
    if spec.kind is not Kind.CATEGORY:
        criteria[NONE_OF_THESE] = NONE_DESCRIPTION
    return Choice(instructions=wording.value, criteria=criteria)


def plan(comment: str, sentences: list[Sentence]) -> Plan:
    """The request for one comment: its state, and three questions per field it could state."""
    by_text = {sentence.text: None for sentence in sentences}
    questions: dict[str, Question] = {}
    asked: list[str] = []
    options: dict[str, tuple[str, ...]] = {}
    for spec in FIELDS:
        found = _options(spec, comment)
        if not found:
            continue
        wording = WORDING[spec.name]
        asked.append(spec.name)
        options[spec.name] = found
        questions[value_name(spec.name)] = _value_question(spec, wording, found)
        questions[support_name(spec.name)] = Noul(
            instructions=wording.support,
            criteria=NoulCriteria(true=wording.support_true, false=wording.support_false),
        )
        questions[sentence_name(spec.name)] = Choice(
            instructions=wording.sentence, criteria=dict(by_text)
        )
    return Plan(
        questions=questions,
        state={"comment": " ".join(comment.split())},
        asked=tuple(asked),
        options=options,
    )
