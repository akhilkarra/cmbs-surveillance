"""The pure rules of every model call: pinned versions, sending policy, answer checks, keys."""

import pytest

from cmbs.model.policy import (
    Classification,
    MalformedAnswer,
    ModelMismatch,
    RestrictedText,
    UnpinnedModel,
    canonical,
    check_answered_by,
    check_answers,
    check_may_send,
    check_pinned,
    key_for,
    recording_key,
    request_hashes,
    sha256,
)
from cmbs.model.types import (
    Choice,
    ChoiceAnswer,
    ModelRequest,
    ModelResponse,
    Noul,
    NoulAnswer,
    probability,
)

REQUEST = ModelRequest(
    model="jev-1.14.0",
    state={"comment": "The loan is current."},
    questions={
        "late": Noul(instructions="Is the loan late?"),
        "status": Choice(instructions="What status?", criteria={"current": None, "late": "Late"}),
    },
)


def _response(**answers) -> ModelResponse:
    defaults = {
        "late": NoulAnswer(noul=0.1),
        "status": ChoiceAnswer(
            choice="current", confidence=0.9, probabilities={"current": 0.9, "late": 0.1}
        ),
    }
    return ModelResponse(model="jev-1.14.0", answers={**defaults, **answers})


@pytest.mark.parametrize("model", ["jev-1.14.0", "jev-2.0.10", "clef-mini-0.3.1"])
def test_exact_versions_are_pinned(model):
    check_pinned(model)


@pytest.mark.parametrize("model", ["jev-latest", "jev-1.14", "jev", "1.14.0", "Jev-1.14.0", ""])
def test_aliases_and_partial_versions_are_refused(model):
    with pytest.raises(UnpinnedModel) as refused:
        check_pinned(model)
    assert str(refused.value) == f"pin an exact model version, not {model}"


def test_the_model_that_answered_must_be_the_one_asked():
    check_answered_by("jev-1.14.0", "jev-1.14.0")
    with pytest.raises(ModelMismatch) as refused:
        check_answered_by("jev-1.14.0", "jev-1.15.0")
    assert str(refused.value) == "model version mismatch: asked for jev-1.14.0, got jev-1.15.0"


def test_public_text_may_always_be_sent():
    check_may_send(Classification.PUBLIC, zero_data_retention=False)
    check_may_send(Classification.PUBLIC, zero_data_retention=True)


def test_restricted_text_needs_zero_data_retention():
    check_may_send(Classification.RESTRICTED, zero_data_retention=True)
    with pytest.raises(RestrictedText) as refused:
        check_may_send(Classification.RESTRICTED, zero_data_retention=False)
    assert str(refused.value) == (
        "restricted text cannot be sent to the provider without zero data retention"
    )


def test_a_complete_response_passes():
    check_answers(REQUEST, _response())


def test_a_missing_answer_is_malformed():
    response = ModelResponse(model="jev-1.14.0", answers={"late": NoulAnswer(noul=0.1)})
    with pytest.raises(MalformedAnswer) as refused:
        check_answers(REQUEST, response)
    assert str(refused.value) == "the model did not answer 'status'"


def test_an_answer_of_the_wrong_type_is_malformed():
    with pytest.raises(MalformedAnswer) as refused:
        check_answers(REQUEST, _response(status=NoulAnswer(noul=0.5)))
    assert str(refused.value) == "'status' was asked as choice, answered as noul"


@pytest.mark.parametrize(
    "answer",
    [
        ChoiceAnswer(choice="paid off", confidence=1.0, probabilities={"current": 1.0}),
        ChoiceAnswer(
            choice="current", confidence=0.8, probabilities={"current": 0.8, "paid off": 0.2}
        ),
    ],
)
def test_a_choice_outside_the_options_is_malformed(answer):
    with pytest.raises(MalformedAnswer) as refused:
        check_answers(REQUEST, _response(status=answer))
    assert str(refused.value) == "'status' was answered with an option it does not offer"


def test_canonical_json_sorts_keys_and_keeps_text():
    assert canonical({"b": 1, "a": ["é", None]}) == '{"a":["é",null],"b":1}'


def test_hashes_cover_questions_state_and_exact_input():
    hashes = request_hashes(REQUEST, "The loan is current.")
    assert hashes["input_sha256"] == sha256("The loan is current.")
    assert hashes["state_sha256"] == sha256('{"comment":"The loan is current."}')
    assert hashes["questions_sha256"] == sha256(
        canonical(REQUEST.model_dump(mode="json")["questions"])
    )


def test_the_key_changes_with_every_part():
    base = {
        "model": "jev-1.14.0",
        "prompt_version": 3,
        "questions_sha256": "q",
        "state_sha256": "s",
        "input_sha256": "i",
    }
    key = recording_key(**base)
    assert key == sha256(
        '{"input":"i","model":"jev-1.14.0","prompt_version":3,"questions":"q","state":"s"}'
    )
    for name, other in [
        ("model", "jev-1.15.0"),
        ("prompt_version", 4),
        ("questions_sha256", "q2"),
        ("state_sha256", "s2"),
        ("input_sha256", "i2"),
    ]:
        assert recording_key(**{**base, name: other}) != key, name


def test_key_for_is_the_key_of_the_request_hashes():
    expected = recording_key(
        model="jev-1.14.0", prompt_version=3, **request_hashes(REQUEST, "text")
    )
    assert key_for(REQUEST, prompt_version=3, input_text="text") == expected
    assert key_for(REQUEST, prompt_version=3, input_text="text ") != expected


def test_probabilities_become_decimals_by_their_shortest_form():
    assert str(probability(0.55)) == "0.55"
    assert str(probability(1)) == "1.0"
