"""The Jev provider over a fake transport: what it sends, and how failures surface."""

import io
import json
import urllib.error

import pytest

from cmbs.model.providers import TYPESAFE_URL, Jev, ProviderFailed, provider_for
from cmbs.model.types import ChoiceAnswer, ModelRequest, ModelResponse, Noul, NoulCriteria

REQUEST = ModelRequest(
    model="jev-1.14.0",
    state={"comment": "The loan is late."},
    questions={"late": Noul(instructions="Is the loan late?")},
)
ANSWER = {
    "model": "jev-1.14.0",
    "answers": {"late": {"type": "noul", "noul": 0.93}},
    "usage": {"input_tokens": 120, "output_tokens": 4},
}


class Transport:
    def __init__(self, reply: bytes | Exception) -> None:
        self.reply = reply
        self.calls: list[tuple[str, dict, dict[str, str]]] = []

    def __call__(self, url: str, body: bytes, headers: dict[str, str]) -> bytes:
        self.calls.append((url, json.loads(body), headers))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def test_jev_posts_the_request_with_the_key_and_reads_the_answer():
    transport = Transport(json.dumps(ANSWER).encode())
    response = Jev("secret", post=transport).ask(REQUEST)
    assert response == ModelResponse.model_validate(ANSWER)
    [(url, body, headers)] = transport.calls
    assert url == "https://api.typesafe.ai/v1/systemone"
    assert body == {
        "model": "jev-1.14.0",
        "state": {"comment": "The loan is late."},
        "questions": {"late": {"type": "noul", "instructions": "Is the loan late?"}},
    }
    assert headers == {
        "Authorization": "Bearer secret",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def test_criteria_are_sent_and_none_values_left_out():
    request = REQUEST.model_copy(
        update={
            "questions": {
                "late": Noul(
                    instructions="Is the loan late?",
                    criteria=NoulCriteria(true="A payment was missed.", false="It is current."),
                )
            }
        }
    )
    transport = Transport(json.dumps(ANSWER).encode())
    Jev("secret", post=transport).ask(request)
    assert transport.calls[0][1]["questions"]["late"]["criteria"] == {
        "true": "A payment was missed.",
        "false": "It is current.",
    }


def test_the_base_url_and_key_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "from-env")
    monkeypatch.setenv("TYPESAFE_BASE_URL", "https://example.test/")
    transport = Transport(json.dumps(ANSWER).encode())
    Jev(post=transport).ask(REQUEST)
    url, _, headers = transport.calls[0]
    assert (url, headers["Authorization"]) == (
        "https://example.test/v1/systemone",
        "Bearer from-env",
    )


def test_without_a_key_nothing_is_sent(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("TYPESAFE_BASE_URL", raising=False)
    transport = Transport(b"{}")
    provider = Jev(post=transport)
    assert provider.url == TYPESAFE_URL + "/v1/systemone"
    with pytest.raises(ProviderFailed) as refused:
        provider.ask(REQUEST)
    assert str(refused.value) == "set TYPESAFE_API_KEY to ask the model provider"
    assert transport.calls == []


def test_an_http_error_is_a_failure_not_an_answer():
    error = urllib.error.HTTPError(TYPESAFE_URL, 429, "Too Many", {}, io.BytesIO())  # type: ignore[arg-type]
    with pytest.raises(ProviderFailed) as refused:
        Jev("secret", post=Transport(error)).ask(REQUEST)
    assert str(refused.value) == "the model provider answered HTTP 429"


def test_an_unreachable_provider_is_a_failure():
    with pytest.raises(ProviderFailed) as refused:
        Jev("secret", post=Transport(urllib.error.URLError("no route"))).ask(REQUEST)
    assert str(refused.value) == "the model provider could not be reached: no route"


@pytest.mark.parametrize("reply", [b"not json", b'{"model": "jev-1.14.0"}'])
def test_a_reply_that_is_not_an_answer_is_a_failure(reply):
    with pytest.raises(ProviderFailed) as refused:
        Jev("secret", post=Transport(reply)).ask(REQUEST)
    assert str(refused.value) == "the model provider sent a response that is not an answer"


def test_choice_answers_parse_with_their_probabilities():
    reply = {
        "model": "jev-1.14.0",
        "answers": {
            "s": {
                "type": "choice",
                "choice": "late",
                "confidence": 0.8,
                "probabilities": {"late": 0.8, "current": 0.2},
            }
        },
    }
    response = Jev("secret", post=Transport(json.dumps(reply).encode())).ask(REQUEST)
    assert response.answers["s"] == ChoiceAnswer(
        choice="late", confidence=0.8, probabilities={"late": 0.8, "current": 0.2}
    )


def test_jev_models_get_the_jev_provider():
    provider = provider_for("jev-1.14.0", api_key="k")
    assert isinstance(provider, Jev) and provider.api_key == "k"


def test_a_model_without_a_provider_is_refused():
    with pytest.raises(ProviderFailed) as refused:
        provider_for("clef-0.1.0")
    assert str(refused.value) == "no provider is configured for model clef-0.1.0"
