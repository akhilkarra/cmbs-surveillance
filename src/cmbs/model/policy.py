"""The rules every model call follows, as pure functions.

- The model is named by an exact version ("jev-1.14.0"), or is a local model whose name this
  code binds to weights at one commit ("clef-flash-2026-10-01", `cmbs.model.local`); a floating
  alias ("jev-latest", "clef-flash") could change answers between two runs without anyone
  noticing, so it is refused.
- The model that answered must be the one asked for. A local model's answer must also carry
  the revision and reference-code hash of its pin, live and on replay.
- Text is classified public (SEC filings) or restricted, and where it may go depends on who
  would read it (`Reach`; grill log Q42, narrowed for local models):
  - the model in this process reads anything: the text never leaves this process;
  - TypeSafe (Jev) reads restricted text only when zero data retention is on in the settings
    in force. That setting is TypeSafe's agreement and applies to TypeSafe only;
  - any other model server, a Clef server on a loopback address included, never reads
    restricted text: a tunnel, a port-forward or a remote Docker context all look like
    127.0.0.1, and there is no retention agreement to configure for it.
- Every choice answer's probabilities must sum to 1 (within 1e-6).
- An answer's confidence is computed here from its probabilities, the same way for every
  provider, never taken from the provider's own field (Jev and Clef define it differently).
- A recording is keyed on the model, the prompt version, the questions and the exact input, so
  a changed comment, question or model never reuses an old answer.
"""

import hashlib
import json
import re
from decimal import Decimal
from enum import StrEnum
from typing import Any

from cmbs.errors import Refused
from cmbs.model.local import is_local_model, local_model
from cmbs.model.types import (
    Answer,
    Choice,
    ChoiceAnswer,
    ModelRequest,
    ModelResponse,
    NoulAnswer,
    probability,
)

# A model family, a dash and a three-part version: "jev-1.14.0".
_PINNED = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z][a-z0-9]*)*-\d+\.\d+\.\d+$")


class Classification(StrEnum):
    PUBLIC = "public"
    RESTRICTED = "restricted"


class Reach(StrEnum):
    """Who reads the text a provider is sent."""

    IN_PROCESS = "the model in this process"
    TYPESAFE = "TypeSafe"
    SERVER = "a model server"


SUM_TOLERANCE = 1e-6


class UnpinnedModel(Refused):
    """The configured model is an alias, not an exact version."""


class ModelMismatch(Refused):
    """An answer came from, or was recorded under, another model than the one asked for."""


class RestrictedText(Refused):
    """Restricted text may not leave without zero data retention."""


class MalformedAnswer(Refused):
    """A response or recording does not answer the questions that were asked."""


def check_pinned(model: str) -> None:
    if not (_PINNED.match(model) or is_local_model(model)):
        raise UnpinnedModel(f"pin an exact model version, not {model}")


def check_answered_by(asked: str, answered: str) -> None:
    if answered != asked:
        raise ModelMismatch(f"model version mismatch: asked for {asked}, got {answered}")


def check_may_send(
    classification: Classification, *, zero_data_retention: bool, reach: Reach
) -> None:
    """Restricted text goes to the model in this process, to TypeSafe only under zero data
    retention (a TypeSafe setting), and to no other server."""
    if classification is Classification.PUBLIC or reach is Reach.IN_PROCESS:
        return
    if reach is Reach.SERVER:
        raise RestrictedText(
            "restricted text cannot be sent to a model server; only the model in this process "
            "may read it"
        )
    if not zero_data_retention:
        raise RestrictedText(
            "restricted text cannot be sent to the provider without zero data retention"
        )


def check_pin(model: str, response: ModelResponse) -> None:
    """A local model's answer names the revision and reference-code hash it was pinned to."""
    if not is_local_model(model):
        return
    pin = local_model(model)
    if response.revision is None or response.reference_sha256 is None:
        raise ModelMismatch(f"{model} answered without its revision and reference code hash")
    if response.revision != pin.revision:
        raise ModelMismatch(
            f"{model} answered from revision {response.revision[:7]}, "
            f"not the pinned {pin.revision[:7]}"
        )
    if response.reference_sha256 != pin.reference_sha256:
        raise ModelMismatch(
            f"{model} answered with reference code {response.reference_sha256[:12]}, "
            f"not the pinned {pin.reference_sha256[:12]}"
        )


def confidence(question: Choice, answer: ChoiceAnswer) -> Decimal:
    """How far the top option stands above an even spread over the question's n options:
    (p_max - 1/n) / (1 - 1/n), computed exactly as (n * p_max - 1) / (n - 1).

    This is Jev's definition; Clef's own `confidence` field is p_max, so the provider's field is
    never read. 0 means the answer is no better than even, 1 that one option has it all; a top
    probability below the even share (an answer that was never checked) reads as 0. A question
    of fewer than two options has no confidence.
    """
    options = len(question.criteria)
    if options < 2:
        raise ValueError(
            f"confidence needs two options or more; {question.instructions!r} has {options}"
        )
    top = max((probability(value) for value in answer.probabilities.values()), default=Decimal(0))
    return max(Decimal(0), (options * top - 1) / (options - 1))


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
        if not all(_is_probability(value) for value in _probabilities(answer)):
            raise MalformedAnswer(f"{name!r} was answered with a probability outside 0 to 1")
        if isinstance(answer, ChoiceAnswer) and not _sums_to_one(answer.probabilities):
            raise MalformedAnswer(f"{name!r} was answered with probabilities that do not sum to 1")


def _probabilities(answer: Answer) -> list[float]:
    if isinstance(answer, NoulAnswer):
        return [answer.noul]
    return [answer.confidence, *answer.probabilities.values()]


def _sums_to_one(probabilities: dict[str, float]) -> bool:
    return abs(sum(probabilities.values()) - 1) <= SUM_TOLERANCE


def _is_probability(value: float) -> bool:
    """A finite number from 0 to 1 (NaN fails both comparisons)."""
    return 0.0 <= value <= 1.0


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
