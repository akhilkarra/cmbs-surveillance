"""The scripted provider used where a scenario needs a request to reach a provider."""

import pytest

from cmbs.model.scripted import DEFAULT_NOUL, ScriptedProvider, choice_answer, respond
from cmbs.model.types import Choice, ChoiceAnswer, ModelRequest, Noul, NoulAnswer

THREE = Choice(instructions="Which?", criteria={"a": None, "b": None, "c": None})
REQUEST = ModelRequest(
    model="jev-1.14.0",
    state={"text": "x"},
    questions={"pick": THREE, "yes": Noul(instructions="Yes?")},
)


def test_the_chosen_option_gets_its_probability_and_the_rest_share():
    answer = choice_answer(THREE, "b", 0.5)
    assert answer == ChoiceAnswer(
        choice="b", confidence=0.5, probabilities={"b": 0.5, "a": 0.25, "c": 0.25}
    )


def test_a_low_probability_choice_is_not_the_top_answer():
    answer = choice_answer(THREE, "b", 0.1)
    assert (answer.choice, answer.confidence) == ("a", 0.45)


def test_a_tie_keeps_the_chosen_option_on_top():
    two = Choice(instructions="Which?", criteria={"a": None, "b": None})
    assert choice_answer(two, "b", 0.5).choice == "b"


def test_a_lone_option_is_certain():
    lone = Choice(instructions="Which?", criteria={"only": None})
    assert choice_answer(lone, "only", 0.4) == ChoiceAnswer(
        choice="only", confidence=1.0, probabilities={"only": 1.0}
    )


def test_an_unknown_option_is_an_error():
    with pytest.raises(ValueError, match="'d' is not an option of 'Which\\?'"):
        choice_answer(THREE, "d", 0.9)


def test_respond_fills_unscripted_questions_with_unsettled_noes():
    response = respond(REQUEST, {})
    assert response.model == "jev-1.14.0"
    assert response.answers["yes"] == NoulAnswer(noul=DEFAULT_NOUL)
    assert response.answers["pick"] == choice_answer(THREE, "a", 1 / 3)


def test_respond_keeps_given_answers_and_may_name_another_model():
    given = NoulAnswer(noul=0.9)
    response = respond(REQUEST, {"yes": given}, model="jev-1.15.0")
    assert (response.model, response.answers["yes"]) == ("jev-1.15.0", given)


def test_the_provider_keeps_every_request():
    provider = ScriptedProvider()
    assert provider.ask(REQUEST) == respond(REQUEST, {})
    assert provider.requests == [REQUEST]
