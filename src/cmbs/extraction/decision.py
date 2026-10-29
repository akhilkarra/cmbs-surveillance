"""The decision table: every field's judgments lead to exactly one outcome.

The model supplies judgments; code decides (grill log Q16). For each field there are three:

- the answer: the field's value the model ranks first, and its probability;
- support: the probability that one sentence of the comment states the field (a Noul);
- evidence: whether the sentence the model cites is, word for word, a sentence of the comment.

Each probability is read against the abstention band in the settings in force (0.30 to 0.70 in
version 7): above the band is high, below it low, and on or inside it unsettled. Then:

| support    | evidence  | answer         | outcome                                      |
|------------|-----------|----------------|----------------------------------------------|
| low        | any       | low, unsettled | not stated                                   |
| low        | any       | high           | review: no supporting sentence               |
| unsettled  | not found | any            | review: evidence not found in source         |
| high       | not found | any            | review: evidence not found in source         |
| unsettled  | found     | any            | review: unsettled support                    |
| high       | found     | low, unsettled | review: unsettled answer                     |
| high       | found     | high           | auto-accept                                  |

A confident answer never passes on its own: it needs a supporting sentence that is really in
the comment. `tests/test_extraction_decision.py` checks all 18 combinations.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from cmbs.extraction.sentences import EVIDENCE_NOT_FOUND


class Band(StrEnum):
    LOW = "low"
    UNSETTLED = "unsettled"
    HIGH = "high"


class Outcome(StrEnum):
    AUTO_ACCEPT = "auto-accept"
    REVIEW = "review"
    NOT_STATED = "not stated"


NO_SUPPORTING_SENTENCE = "no supporting sentence"
UNSETTLED_SUPPORT = "unsettled support"
UNSETTLED_ANSWER = "unsettled answer"
REVIEW_REASONS = (NO_SUPPORTING_SENTENCE, EVIDENCE_NOT_FOUND, UNSETTLED_SUPPORT, UNSETTLED_ANSWER)


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    reason: str | None = None


def band(probability: Decimal, *, low: Decimal, high: Decimal) -> Band:
    """Below `low` is low, above `high` is high; the band itself, ends included, is unsettled."""
    if probability < low:
        return Band.LOW
    if probability > high:
        return Band.HIGH
    return Band.UNSETTLED


def decide(*, answer: Band, support: Band, evidence_found: bool) -> Decision:
    if support is Band.LOW:
        if answer is Band.HIGH:
            return Decision(Outcome.REVIEW, NO_SUPPORTING_SENTENCE)
        return Decision(Outcome.NOT_STATED)
    if not evidence_found:
        return Decision(Outcome.REVIEW, EVIDENCE_NOT_FOUND)
    if support is Band.UNSETTLED:
        return Decision(Outcome.REVIEW, UNSETTLED_SUPPORT)
    if answer is not Band.HIGH:
        return Decision(Outcome.REVIEW, UNSETTLED_ANSWER)
    return Decision(Outcome.AUTO_ACCEPT)
