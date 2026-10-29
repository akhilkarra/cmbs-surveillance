"""Live Jev answers (tests/fixtures/model/live/) through today's questions and decision table."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from cmbs.extraction.judgments import decide_fields, read
from cmbs.extraction.questions import plan
from cmbs.extraction.sentences import split
from cmbs.model.types import ModelRequest, ModelResponse

LIVE = Path(__file__).parent / "fixtures" / "model" / "live"
LOW, HIGH = Decimal("0.30"), Decimal("0.70")  # the abstention band of settings version 7

ACCEPT, REVIEW, NOT_STATED = "auto-accept", "review", "not stated"
# What an analyst reading each comment would record: (outcome, value) per field asked.
EXPECTED = {
    "fixture-loan-2": {
        "transfer_reason": (ACCEPT, "imminent monetary default"),
        "transfer_date": (ACCEPT, "2025-03-06"),
        "dscr": (ACCEPT, "0.71"),
        "dscr_as_of": (ACCEPT, "2024-12-31"),
        "occupancy": (ACCEPT, "61.4"),
        "occupancy_as_of": (ACCEPT, "2024-12-31"),
        "borrower_request": (ACCEPT, "maturity extension"),
        "request_status": (ACCEPT, "pending"),
        "workout_strategy": (NOT_STATED, None),
    },
    "bnk13-loan-11": {
        "transfer_reason": (ACCEPT, "monetary default"),
        "transfer_date": (ACCEPT, "2026-06-04"),
        "dscr": (ACCEPT, "0.03"),
        "dscr_as_of": (ACCEPT, "2026-03-31"),
        "occupancy": (ACCEPT, "28.29"),
        "occupancy_as_of": (ACCEPT, "2026-03-31"),
        "borrower_request": (NOT_STATED, None),
        "request_status": (NOT_STATED, None),
        "workout_strategy": (REVIEW, None),  # "engage counsel, execute PNA": unsettled
    },
    "bnk13-loan-57": {
        "transfer_reason": (NOT_STATED, None),  # a covenant breach, but no stated reason
        "borrower_request": (NOT_STATED, None),
        "request_status": (NOT_STATED, None),
        "workout_strategy": (REVIEW, None),
    },
    "hard-no-reason-no-request": {
        "transfer_reason": (NOT_STATED, None),
        "borrower_request": (NOT_STATED, None),
        "request_status": (NOT_STATED, None),
        "workout_strategy": (NOT_STATED, None),
    },
    "hard-request-granted": {
        "transfer_reason": (ACCEPT, "imminent monetary default"),
        "transfer_date": (ACCEPT, "2025-03-06"),
        "dscr_as_of": (NOT_STATED, None),
        "occupancy_as_of": (NOT_STATED, None),
        "borrower_request": (ACCEPT, "maturity extension"),
        "request_status": (ACCEPT, "granted"),
        "workout_strategy": (REVIEW, None),  # an extension granted implies one; unsettled
    },
}


def _case(name: str) -> dict:
    return json.loads((LIVE / f"{name}.json").read_text())


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_todays_questions_are_the_ones_recorded(name):
    case = _case(name)
    asked = plan(case["comment"], split(case["comment"]))
    request = ModelRequest(model="jev-1.14.0", state=asked.state, questions=asked.questions)
    assert request.model_dump(mode="json", exclude_none=True) == case["request"]


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_live_answers_decide_as_an_analyst_would(name):
    case = _case(name)
    sentences = split(case["comment"])
    asked = plan(case["comment"], sentences)
    response = ModelResponse.model_validate(case["response"])
    results = decide_fields(read(asked, sentences, response), low=LOW, high=HIGH)
    found = {
        field: (
            result.decision.outcome.value,
            result.judgment.value
            if result.judgment and result.decision.outcome.value == ACCEPT
            else None,
        )
        for field, result in results.items()
        if result.judgment is not None
    }
    assert found == EXPECTED[name]


def test_the_live_answers_came_from_the_pinned_model():
    for name in EXPECTED:
        assert _case(name)["response"]["model"] == "jev-1.14.0"
