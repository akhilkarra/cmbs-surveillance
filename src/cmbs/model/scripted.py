"""A provider that answers from a script, for scenarios and tests that need a live call.

It never touches the network. `respond` builds a full response: the answers given, and for every
other question a "no" Noul and a Choice spread evenly over its options, so code reading them
sees an unsettled, unsupported answer rather than a made-up one.
"""

from collections.abc import Callable, Mapping

from cmbs.model.policy import Reach
from cmbs.model.types import (
    Answer,
    Choice,
    ChoiceAnswer,
    ModelRequest,
    ModelResponse,
    NoulAnswer,
)

DEFAULT_NOUL = 0.05


def choice_answer(question: Choice, chosen: str, probability: float) -> ChoiceAnswer:
    """`chosen` at `probability`, the rest shared evenly by the other options."""
    if chosen not in question.criteria:
        raise ValueError(f"{chosen!r} is not an option of {question.instructions!r}")
    others = [option for option in question.criteria if option != chosen]
    if not others:
        return ChoiceAnswer(choice=chosen, confidence=1.0, probabilities={chosen: 1.0})
    share = (1 - probability) / len(others)
    # `chosen` first, so it is the top answer when another option ties with it.
    probabilities = {chosen: probability} | {option: share for option in others}
    top = max(probabilities, key=probabilities.__getitem__)
    return ChoiceAnswer(choice=top, confidence=probabilities[top], probabilities=probabilities)


def respond(
    request: ModelRequest, answers: Mapping[str, Answer], *, model: str | None = None
) -> ModelResponse:
    """A response to every question in `request`: `answers` where given, defaults elsewhere."""
    full: dict[str, Answer] = {}
    for name, question in request.questions.items():
        if name in answers:
            full[name] = answers[name]
        elif isinstance(question, Choice):
            options = list(question.criteria)
            full[name] = choice_answer(question, options[0], 1 / len(options))
        else:
            full[name] = NoulAnswer(noul=DEFAULT_NOUL)
    return ModelResponse(model=model or request.model, answers=full)


class ScriptedProvider:
    """Answers each request with `answer(request)` and keeps every request it was sent.
    `reach` says whom it stands in for: TypeSafe unless told otherwise."""

    def __init__(
        self,
        answer: Callable[[ModelRequest], ModelResponse] | None = None,
        *,
        reach: Reach = Reach.TYPESAFE,
    ) -> None:
        self.answer = answer or (lambda request: respond(request, {}))
        self.reach = reach
        self.requests: list[ModelRequest] = []

    def ask(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return self.answer(request)
