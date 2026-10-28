"""Read the model's answers into one judgment per field, and decide each field.

The value is the option ranked first among the field's real options ("none of these" only
absorbs probability, so a figure the comment does not give reads as an unsettled answer). The
cited sentence is checked word for word against the comment's sentences.
"""

from dataclasses import dataclass
from decimal import Decimal

from cmbs.extraction.decision import Decision, Outcome, band, decide
from cmbs.extraction.fields import FIELDS, canonical, spec
from cmbs.extraction.questions import (
    NONE_OF_THESE,
    Plan,
    sentence_name,
    support_name,
    value_name,
)
from cmbs.extraction.sentences import EvidenceNotFound, Sentence, find
from cmbs.model.types import ChoiceAnswer, ModelResponse, NoulAnswer, probability


@dataclass(frozen=True)
class Judgment:
    """What the model said about one field. `value` is canonical; `printed` as offered."""

    field: str
    printed: str
    value: str
    answer_probability: Decimal
    support_probability: Decimal
    evidence: str
    sentence: int | None
    value_question: str
    support_question: str

    @property
    def evidence_found(self) -> bool:
        return self.sentence is not None


@dataclass(frozen=True)
class FieldResult:
    """A field's outcome. A field that was not asked has no judgment and is not stated."""

    field: str
    decision: Decision
    judgment: Judgment | None


def _choice(response: ModelResponse, name: str) -> ChoiceAnswer:
    answer = response.answers[name]
    if not isinstance(answer, ChoiceAnswer):
        raise TypeError(f"{name} is not a choice answer")
    return answer


def _noul(response: ModelResponse, name: str) -> NoulAnswer:
    answer = response.answers[name]
    if not isinstance(answer, NoulAnswer):
        raise TypeError(f"{name} is not a noul answer")
    return answer


def read(plan: Plan, sentences: list[Sentence], response: ModelResponse) -> dict[str, Judgment]:
    """One judgment per asked field. The response has been checked against the plan."""
    judgments: dict[str, Judgment] = {}
    for field in plan.asked:
        value_answer = _choice(response, value_name(field))
        real = [option for option in plan.options[field] if option != NONE_OF_THESE]
        # The first real option with the highest probability; a missing one counts as zero.
        top = max(real, key=lambda option: value_answer.probabilities.get(option, 0.0))
        cited = _choice(response, sentence_name(field)).choice
        try:
            sentence: int | None = find(sentences, cited).number
        except EvidenceNotFound:
            sentence = None
        value_question = plan.questions[value_name(field)]
        support_question = plan.questions[support_name(field)]
        judgments[field] = Judgment(
            field=field,
            printed=top,
            value=canonical(spec(field), top),
            answer_probability=probability(value_answer.probabilities.get(top, 0.0)),
            support_probability=probability(_noul(response, support_name(field)).noul),
            evidence=cited,
            sentence=sentence,
            value_question=value_question.instructions,
            support_question=support_question.instructions,
        )
    return judgments


def decide_fields(
    judgments: dict[str, Judgment], *, low: Decimal, high: Decimal
) -> dict[str, FieldResult]:
    """Every field's outcome, in field order; fields without a judgment are not stated."""
    results: dict[str, FieldResult] = {}
    for field_spec in FIELDS:
        judgment = judgments.get(field_spec.name)
        if judgment is None:
            decision = Decision(Outcome.NOT_STATED)
        else:
            decision = decide(
                answer=band(judgment.answer_probability, low=low, high=high),
                support=band(judgment.support_probability, low=low, high=high),
                evidence_found=judgment.evidence_found,
            )
        results[field_spec.name] = FieldResult(field_spec.name, decision, judgment)
    return results
