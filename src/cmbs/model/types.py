"""Questions, answers, requests and responses, in the shape TypeSafe's API uses.

Only the two question types the pipeline asks are modelled: a Noul (the probability that a
statement is true) and a Choice (one option out of named options, with a probability for each).
A provider for another model translates to and from these types. Probabilities are floats here
because that is what crosses the wire; code that compares them with settings converts them to
`Decimal` first (`probability`).
"""

from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class NoulCriteria(_Frozen):
    """What counts as yes and what counts as no, each as a concrete situation."""

    true: str
    false: str


class Noul(_Frozen):
    type: Literal["noul"] = "noul"
    instructions: str
    criteria: NoulCriteria | None = None


class Choice(_Frozen):
    """Option names are sent to the model, so they describe the option; a description may be
    None when the name says it all."""

    type: Literal["choice"] = "choice"
    instructions: str
    criteria: dict[str, str | None]


type Question = Annotated[Noul | Choice, Field(discriminator="type")]


class NoulAnswer(_Frozen):
    type: Literal["noul"] = "noul"
    noul: float


class ChoiceAnswer(_Frozen):
    type: Literal["choice"] = "choice"
    choice: str
    confidence: float
    probabilities: dict[str, float]


type Answer = Annotated[NoulAnswer | ChoiceAnswer, Field(discriminator="type")]


class ModelRequest(_Frozen):
    """One request: the state every question refers to, and the named questions."""

    model: str
    state: dict[str, Any]
    questions: dict[str, Question]


class ModelResponse(_Frozen):
    """`model` is the model that actually answered, which may differ from the one asked for."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    model: str
    answers: dict[str, Answer]


def probability(value: float) -> Decimal:
    """A probability as a Decimal, by its shortest decimal form: 0.55 -> Decimal('0.55')."""
    return Decimal(repr(float(value)))
