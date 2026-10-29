"""The decision table, checked over every combination of its inputs.

This is the pytest stand-in for the `decision_table_total` theorem until the Lean proof lands:
each of the 3 x 3 x 2 combinations has exactly one outcome, and it is the one in the table.
"""

import itertools
from decimal import Decimal

import pytest

from cmbs.extraction.decision import (
    NO_SUPPORTING_SENTENCE,
    REVIEW_REASONS,
    UNSETTLED_ANSWER,
    UNSETTLED_SUPPORT,
    Band,
    Decision,
    Outcome,
    band,
    decide,
)
from cmbs.extraction.sentences import EVIDENCE_NOT_FOUND

L, U, H = Band.LOW, Band.UNSETTLED, Band.HIGH
ACCEPT = Decision(Outcome.AUTO_ACCEPT)
NOT_STATED = Decision(Outcome.NOT_STATED)


def review(reason: str) -> Decision:
    return Decision(Outcome.REVIEW, reason)


# (support, evidence found, answer) -> outcome, written out in full.
TABLE = {
    (L, False, L): NOT_STATED,
    (L, False, U): NOT_STATED,
    (L, False, H): review(NO_SUPPORTING_SENTENCE),
    (L, True, L): NOT_STATED,
    (L, True, U): NOT_STATED,
    (L, True, H): review(NO_SUPPORTING_SENTENCE),
    (U, False, L): review(EVIDENCE_NOT_FOUND),
    (U, False, U): review(EVIDENCE_NOT_FOUND),
    (U, False, H): review(EVIDENCE_NOT_FOUND),
    (U, True, L): review(UNSETTLED_SUPPORT),
    (U, True, U): review(UNSETTLED_SUPPORT),
    (U, True, H): review(UNSETTLED_SUPPORT),
    (H, False, L): review(EVIDENCE_NOT_FOUND),
    (H, False, U): review(EVIDENCE_NOT_FOUND),
    (H, False, H): review(EVIDENCE_NOT_FOUND),
    (H, True, L): review(UNSETTLED_ANSWER),
    (H, True, U): review(UNSETTLED_ANSWER),
    (H, True, H): ACCEPT,
}


def test_the_table_covers_every_combination_once():
    combinations = list(itertools.product(Band, (False, True), Band))
    assert len(combinations) == 18
    assert sorted(TABLE, key=str) == sorted(combinations, key=str)


@pytest.mark.parametrize(("inputs", "expected"), TABLE.items(), ids=str)
def test_each_combination_has_its_one_outcome(inputs, expected):
    support, evidence_found, answer = inputs
    decision = decide(answer=answer, support=support, evidence_found=evidence_found)
    assert decision == expected
    assert (decision.reason is not None) == (decision.outcome is Outcome.REVIEW)
    assert decision.reason is None or decision.reason in REVIEW_REASONS


def test_only_a_confident_supported_answer_with_evidence_is_accepted():
    accepted = [inputs for inputs, outcome in TABLE.items() if outcome == ACCEPT]
    assert accepted == [(H, True, H)]


@pytest.mark.parametrize(
    ("probability", "expected"),
    [
        ("0", L),
        ("0.2999", L),
        ("0.30", U),
        ("0.5", U),
        ("0.70", U),
        ("0.7001", H),
        ("1", H),
    ],
)
def test_the_abstention_band_includes_its_ends(probability, expected):
    assert band(Decimal(probability), low=Decimal("0.30"), high=Decimal("0.70")) is expected


def test_the_band_follows_the_settings():
    assert band(Decimal("0.75"), low=Decimal("0.20"), high=Decimal("0.80")) is U
    assert band(Decimal("0.25"), low=Decimal("0.20"), high=Decimal("0.80")) is U
    assert band(Decimal("0.15"), low=Decimal("0.20"), high=Decimal("0.80")) is L
