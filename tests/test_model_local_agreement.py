"""The local model's recorded answers, and the agreement report between two models."""

import json
from decimal import Decimal

import pytest

from cmbs.extraction.decision import Outcome
from cmbs.extraction.questions import PROMPT_VERSION, plan, sentence_name, support_name, value_name
from cmbs.extraction.sentences import split
from cmbs.extraction.service import call_key
from cmbs.model.local import CLEF_FLASH_2026_10_01
from cmbs.model.policy import Classification, MalformedAnswer
from cmbs.model.scripted import choice_answer, respond
from cmbs.model.types import Choice, ModelRequest, ModelResponse, NoulAnswer
from cmbs.quality.agreement import (
    Answer,
    Case,
    NotComparable,
    compare,
    decide_case,
    report_text,
)
from tests.fixtures.model.local.cases import HERE, HIGH, LIVE, LOW, case, cases, local_recordings
from tests.fixtures.model.synthetic import loan_2_september_comment

MODEL = CLEF_FLASH_2026_10_01.name
NAMES = [
    "bnk13-loan-11",
    "bnk13-loan-57",
    "fixture-loan-2",
    "hard-no-reason-no-request",
    "hard-request-granted",
]


# The recordings.


def test_every_live_comment_has_a_local_recording_from_the_pinned_model():
    recordings = local_recordings()
    assert sorted(recordings) == NAMES
    for recording in recordings.values():
        assert (recording.model, recording.response.model) == (MODEL, MODEL)
        assert recording.prompt_version == PROMPT_VERSION
        assert recording.classification is Classification.PUBLIC
        assert recording.synthetic is False


def test_each_recording_is_keyed_as_the_extraction_asks_for_its_comment():
    for name, recording in local_recordings().items():
        comment = json.loads((LIVE / f"{name}.json").read_text())["comment"]
        assert recording.call_id == call_key(comment, model=MODEL, prompt_version=PROMPT_VERSION)


def test_the_fixture_deals_loan_2_comment_is_replayed_by_its_recording():
    comment = loan_2_september_comment()
    key = call_key(comment, model=MODEL, prompt_version=PROMPT_VERSION)
    assert local_recordings()["fixture-loan-2"].call_id == key


def test_clefs_answers_carry_its_own_confidence_the_top_probability():
    """Recorded as received: Clef's `confidence` is p_max, which is why it is never read."""
    for recording in local_recordings().values():
        for answer in recording.response.answers.values():
            if answer.type == "choice":
                assert answer.confidence == max(answer.probabilities.values())


def test_a_recording_for_another_comment_or_other_questions_is_not_paired():
    recording = local_recordings()["fixture-loan-2"]
    with pytest.raises(ValueError) as wrong:
        case("bnk13-loan-57", recording)
    assert str(wrong.value) == "bnk13-loan-57: the local recording answers another comment"
    other = recording.request.model_copy(update={"state": {"comment": "other"}})
    with pytest.raises(ValueError) as asked:
        case("fixture-loan-2", recording.model_copy(update={"request": other}))
    assert str(asked.value) == "fixture-loan-2: the two models were asked different questions"


# Deciding one comment.

COMMENT = (
    "The loan transferred to Special Servicing due to monetary default. "
    "The borrower has requested a forbearance."
)


def _response(model: str, settled: dict[str, tuple[str, float, float]]) -> ModelResponse:
    """Answers for COMMENT: per field (value, its probability, support), citing sentence 1."""
    sentences = split(COMMENT)
    asked = plan(COMMENT, sentences)
    request = ModelRequest(model=model, state=asked.state, questions=asked.questions)
    answers = {}
    for field, (value, p, support) in settled.items():
        value_question = request.questions[value_name(field)]
        sentence_question = request.questions[sentence_name(field)]
        assert isinstance(value_question, Choice) and isinstance(sentence_question, Choice)
        answers[value_name(field)] = choice_answer(value_question, value, p)
        answers[support_name(field)] = NoulAnswer(noul=support)
        answers[sentence_name(field)] = choice_answer(sentence_question, sentences[0].text, 0.95)
    return respond(request, answers)


def test_a_comment_is_decided_field_by_field_with_the_judgments_behind_each():
    response = _response("jev-1.14.0", {"transfer_reason": ("monetary default", 0.9, 0.95)})
    decided = decide_case(COMMENT, response, low=LOW, high=HIGH)
    assert set(decided) == {
        "transfer_reason",
        "borrower_request",
        "request_status",
        "workout_strategy",
    }
    assert decided["transfer_reason"] == Answer(
        model="jev-1.14.0",
        outcome=Outcome.AUTO_ACCEPT,
        reason=None,
        value="monetary default",
        answer_probability=Decimal("0.9"),
        support_probability=Decimal("0.95"),
        sentence=1,
    )
    assert decided["borrower_request"].outcome is Outcome.NOT_STATED
    assert decided["borrower_request"].stated is False
    assert decided["transfer_reason"].stated is True


def test_a_response_to_other_questions_cannot_be_decided():
    response = ModelResponse(model="jev-1.14.0", answers={})
    with pytest.raises(MalformedAnswer):
        decide_case(COMMENT, response, low=LOW, high=HIGH)


def test_an_answer_reads_with_its_outcome_value_probabilities_and_sentence():
    answer = Answer(
        model=MODEL,
        outcome=Outcome.REVIEW,
        reason="unsettled answer",
        value="imminent monetary default",
        answer_probability=Decimal("0.6725"),
        support_probability=Decimal("0.9522"),
        sentence=1,
    )
    assert answer.text() == (
        'clef-flash-2026-10-01: review (unsettled answer), "imminent monetary default" at '
        "0.6725, support 0.9522, sentence 1"
    )
    unfound = Answer(
        model="jev-1.14.0",
        outcome=Outcome.NOT_STATED,
        reason=None,
        value="2025-03-06",
        answer_probability=Decimal("0.0"),
        support_probability=Decimal("0.01"),
        sentence=None,
    )
    assert unfound.text() == (
        'jev-1.14.0: not stated, "2025-03-06" at 0.0, support 0.01, no sentence found'
    )


# Comparing.


def _case(name: str, first: dict, second: dict) -> Case:
    return Case(
        name=name,
        comment=COMMENT,
        first=_response("jev-1.14.0", first),
        second=_response(MODEL, second),
    )


def test_outcomes_and_values_are_counted_per_field_and_overall():
    same = _case(
        "same",
        {"transfer_reason": ("monetary default", 0.9, 0.95)},
        {"transfer_reason": ("monetary default", 0.8, 0.9)},
    )
    other_value = _case(
        "other value",
        {"transfer_reason": ("monetary default", 0.9, 0.95)},
        {"transfer_reason": ("bankruptcy", 0.9, 0.95)},
    )
    other_outcome = _case(
        "other outcome",
        {"borrower_request": ("forbearance", 0.9, 0.95)},
        {"borrower_request": ("forbearance", 0.5, 0.95)},
    )
    report = compare([same, other_value, other_outcome], low=LOW, high=HIGH)
    assert (report.first_model, report.second_model, report.cases) == ("jev-1.14.0", MODEL, 3)
    by_field = {agreement.field: agreement for agreement in report.fields}
    assert list(by_field) == [
        "transfer_reason",
        "borrower_request",
        "request_status",
        "workout_strategy",
    ]
    reason = by_field["transfer_reason"]
    assert (reason.outcomes.successes, reason.outcomes.trials) == (3, 3)
    assert (reason.values.successes, reason.values.trials) == (1, 2)
    request = by_field["borrower_request"]
    assert (request.outcomes.successes, request.outcomes.trials) == (2, 3)
    assert (request.values.successes, request.values.trials) == (1, 1)
    assert (report.overall.outcomes.successes, report.overall.outcomes.trials) == (11, 12)
    assert (report.overall.values.successes, report.overall.values.trials) == (2, 3)
    assert [(item.case, item.field) for item in report.disagreements] == [
        ("other value", "transfer_reason"),
        ("other outcome", "borrower_request"),
    ]
    disagreement = report.disagreements[1]
    assert (disagreement.first.outcome, disagreement.second.outcome) == (
        Outcome.AUTO_ACCEPT,
        Outcome.REVIEW,
    )


def test_values_are_not_compared_when_either_model_finds_the_field_not_stated():
    differ = _case(
        "differ",
        {"workout_strategy": ("foreclosure", 0.9, 0.1)},
        {"workout_strategy": ("modification", 0.9, 0.1)},
    )
    report = compare([differ], low=LOW, high=HIGH)
    strategy = next(a for a in report.fields if a.field == "workout_strategy")
    assert (strategy.outcomes.successes, strategy.values.trials) == (1, 0)
    assert report.disagreements == ()


def test_only_two_different_models_on_at_least_one_comment_are_compared():
    with pytest.raises(NotComparable) as empty:
        compare([], low=LOW, high=HIGH)
    assert str(empty.value) == "no comments to compare"
    jev = _response("jev-1.14.0", {})
    with pytest.raises(NotComparable) as itself:
        compare([Case("a", COMMENT, jev, jev)], low=LOW, high=HIGH)
    assert str(itself.value) == "both answers come from jev-1.14.0"
    mixed = [
        _case("a", {}, {}),
        Case("b", COMMENT, _response("jev-1.13.0", {}), _response(MODEL, {})),
    ]
    with pytest.raises(NotComparable) as several:
        compare(mixed, low=LOW, high=HIGH)
    assert str(several.value) == (
        "the first answers come from 2 models: ['jev-1.13.0', 'jev-1.14.0']"
    )


# The report on the recorded answers.

REPORT = HERE / "agreement-report.md"


def test_the_report_on_the_recorded_answers():
    report = compare(cases(), low=LOW, high=HIGH)
    assert report_text(report, low=LOW, high=HIGH) == REPORT.read_text(), (
        "regenerate with: uv run python -m tests.fixtures.model.local.cases "
        "> tests/fixtures/model/local/agreement-report.md"
    )
