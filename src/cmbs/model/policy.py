"""The rules every model call follows, as pure functions.

- The model is named by an exact version ("jev-1.14.0"); a floating alias ("jev-latest") could
  change answers between two runs without anyone noticing, so it is refused.
- The model that answered must be the one asked for.
- Text is classified public (SEC filings) or restricted. Restricted text goes to the provider
  only when zero data retention is on in the settings in force (grill log Q42).
- A recording is keyed on the model, the prompt version, the questions and the exact input, so
  a changed comment, question or model never reuses an old answer.
"""

import hashlib
import json
import re
from enum import StrEnum
from typing import Any

from cmbs.errors import Refused
from cmbs.model.types import ModelRequest, ModelResponse

# A model family, a dash and a three-part version: "jev-1.14.0".
_PINNED = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z][a-z0-9]*)*-\d+\.\d+\.\d+$")


class Classification(StrEnum):
    PUBLIC = "public"
    RESTRICTED = "restricted"


class UnpinnedModel(Refused):
    """The configured model is an alias, not an exact version."""


class ModelMismatch(Refused):
    """An answer came from, or was recorded under, another model than the one asked for."""


class RestrictedText(Refused):
    """Restricted text may not leave without zero data retention."""


class MalformedAnswer(Refused):
    """A response or recording does not answer the questions that were asked."""


def check_pinned(model: str) -> None:
    if not _PINNED.match(model):
        raise UnpinnedModel(f"pin an exact model version, not {model}")


def check_answered_by(asked: str, answered: str) -> None:
    if answered != asked:
        raise ModelMismatch(f"model version mismatch: asked for {asked}, got {answered}")


def check_may_send(classification: Classification, *, zero_data_retention: bool) -> None:
    if classification is Classification.RESTRICTED and not zero_data_retention:
        raise RestrictedText(
            "restricted text cannot be sent to the provider without zero data retention"
        )


def check_answers(request: ModelRequest, response: ModelResponse) -> None:
    """Every question answered, with an answer of its own type, and a choice among its options."""
    missing = sorted(set(request.questions) - set(response.answers))
    if missing:
        raise MalformedAnswer(f"the model did not answer {missing[0]!r}")
    for name, question in request.questions.items():
        answer = response.answers[name]
        if answer.type != question.type:
            raise MalformedAnswer(
                f"{name!r} was asked as {question.type}, answered as {answer.type}"
            )
        if answer.type == "choice" and question.type == "choice":
            unknown = sorted(set(answer.probabilities) - set(question.criteria))
            if answer.choice not in question.criteria or unknown:
                raise MalformedAnswer(f"{name!r} was answered with an option it does not offer")


def canonical(value: Any) -> str:
    """JSON with sorted keys and no spaces, so equal content always hashes the same."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def recording_key(
    *, model: str, prompt_version: int, questions_sha256: str, state_sha256: str, input_sha256: str
) -> str:
    return sha256(
        canonical(
            {
                "model": model,
                "prompt_version": prompt_version,
                "questions": questions_sha256,
                "state": state_sha256,
                "input": input_sha256,
            }
        )
    )


def request_hashes(request: ModelRequest, input_text: str) -> dict[str, str]:
    """The hashes a recording is keyed on: questions, state and the exact input text."""
    dumped = request.model_dump(mode="json")
    return {
        "questions_sha256": sha256(canonical(dumped["questions"])),
        "state_sha256": sha256(canonical(dumped["state"])),
        "input_sha256": sha256(input_text),
    }


def key_for(request: ModelRequest, *, prompt_version: int, input_text: str) -> str:
    """The recording key of a request about `input_text`."""
    return recording_key(
        model=request.model, prompt_version=prompt_version, **request_hashes(request, input_text)
    )
