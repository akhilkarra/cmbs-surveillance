"""How far two models agree on the same comments, field by field. Pure.

Before a second model is trusted (the local Clef-flash beside Jev, grill log Q7), both answer
the same questions about the same comments, and each answer goes through the same decision
table the panel uses (`cmbs.extraction.judgments`). Comparing decided fields, not raw
probabilities, measures what an analyst would see change if the panel switched models.

It lives in `cmbs.quality`, not `cmbs.model`: it reads answers through the extraction's
questions and decision table, which build on `cmbs.model`, and like the quality report it
measures extraction, here against another model rather than against golden labels.

Per field it reports:

- **same outcome**: of the comments where the field was asked, how many the two models decide
  the same way (auto-accept, review or not stated);
- **same value**: of the comments where both models say the comment states the field (outcome
  auto-accept or review), how many name the same value. When either reads the field as not
  stated, its top option is only where a forced choice landed, so the values are not compared.

Every field on which they differ in outcome, or in value where both state it, is listed with
both answers, each with the confidence of its value answer, computed from the probabilities
the same way for both models (`cmbs.model.policy.confidence`), never the model's own field.
Agreement is not accuracy: without golden labels it measures consistency only.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from cmbs.extraction.decision import Outcome
from cmbs.extraction.fields import FIELDS, spec
from cmbs.extraction.judgments import FieldResult, decide_fields, read
from cmbs.extraction.questions import Plan, plan, value_name
from cmbs.extraction.sentences import split
from cmbs.model.policy import check_answers, confidence
from cmbs.model.types import Choice, ChoiceAnswer, ModelRequest, ModelResponse
from cmbs.quality.stats import Proportion

PLACES = Decimal("0.0001")


def _shown(value: Decimal) -> Decimal:
    return value.quantize(PLACES, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Case:
    """One comment and the two models' responses to the questions it is asked."""

    name: str
    comment: str
    first: ModelResponse
    second: ModelResponse


@dataclass(frozen=True)
class Answer:
    """One model's decided field: the outcome, and the judgments behind it."""

    model: str
    outcome: Outcome
    reason: str | None
    value: str
    answer_probability: Decimal
    support_probability: Decimal
    sentence: int | None
    confidence: Decimal

    @property
    def stated(self) -> bool:
        return self.outcome is not Outcome.NOT_STATED

    def text(self) -> str:
        """'jev-1.14.0: review (unsettled answer), "imminent monetary default" at 0.6000,
        confidence 0.5333, support 0.9500, sentence 1', each figure to four places."""
        outcome = self.outcome.value + (f" ({self.reason})" if self.reason else "")
        cited = f"sentence {self.sentence}" if self.sentence is not None else "no sentence found"
        return (
            f'{self.model}: {outcome}, "{self.value}" at {_shown(self.answer_probability)}, '
            f"confidence {_shown(self.confidence)}, support {_shown(self.support_probability)}, "
            f"{cited}"
        )


@dataclass(frozen=True)
class Disagreement:
    case: str
    field: str
    first: Answer
    second: Answer


@dataclass(frozen=True)
class FieldAgreement:
    field: str
    outcomes: Proportion
    values: Proportion


@dataclass(frozen=True)
class AgreementReport:
    first_model: str
    second_model: str
    cases: int
    fields: tuple[FieldAgreement, ...]
    overall: FieldAgreement
    disagreements: tuple[Disagreement, ...]


class NotComparable(ValueError):
    """The cases do not hold two models' answers to the same questions."""


def _answers(
    plan_: Plan, response: ModelResponse, results: dict[str, FieldResult]
) -> dict[str, Answer]:
    answers: dict[str, Answer] = {}
    for field, result in results.items():
        judgment = result.judgment
        if judgment is None:  # not asked: the comment prints nothing the field could be
            continue
        name = value_name(field)
        question, answered = plan_.questions[name], response.answers[name]
        assert isinstance(question, Choice) and isinstance(answered, ChoiceAnswer)
        answers[field] = Answer(
            model=response.model,
            outcome=result.decision.outcome,
            reason=result.decision.reason,
            value=judgment.value,
            answer_probability=judgment.answer_probability,
            support_probability=judgment.support_probability,
            sentence=judgment.sentence,
            confidence=confidence(question, answered),
        )
    return answers


def decide_case(
    comment: str, response: ModelResponse, *, low: Decimal, high: Decimal
) -> dict[str, Answer]:
    """Every asked field of `comment` as `response` decides it under the band `low` to `high`.
    The response must answer exactly today's questions for the comment."""
    sentences = split(comment)
    asked = plan(comment, sentences)
    check_answers(
        ModelRequest(model=response.model, state=asked.state, questions=asked.questions), response
    )
    results = decide_fields(read(asked, sentences, response), low=low, high=high)
    return _answers(asked, response, results)


def _model(cases: Sequence[Case], which: str) -> str:
    models = {getattr(case, which).model for case in cases}
    if len(models) != 1:
        raise NotComparable(f"the {which} answers come from {len(models)} models: {sorted(models)}")
    return models.pop()


@dataclass
class _Tally:
    outcomes: int = 0
    same_outcome: int = 0
    stated: int = 0
    same_value: int = 0

    def add(self, first: Answer, second: Answer) -> bool:
        """Count one pair; True when they disagree."""
        self.outcomes += 1
        same_outcome = first.outcome is second.outcome
        self.same_outcome += same_outcome
        both_stated = first.stated and second.stated
        same_value = first.value == second.value
        if both_stated:
            self.stated += 1
            self.same_value += same_value
        return not same_outcome or (both_stated and not same_value)

    def agreement(self, field: str) -> FieldAgreement:
        return FieldAgreement(
            field=field,
            outcomes=Proportion(self.same_outcome, self.outcomes),
            values=Proportion(self.same_value, self.stated),
        )


def compare(cases: Sequence[Case], *, low: Decimal, high: Decimal) -> AgreementReport:
    """The agreement of the two models across `cases`, in field order."""
    if not cases:
        raise NotComparable("no comments to compare")
    first_model, second_model = _model(cases, "first"), _model(cases, "second")
    if first_model == second_model:
        raise NotComparable(f"both answers come from {first_model}")
    tallies = {field_spec.name: _Tally() for field_spec in FIELDS}
    overall = _Tally()
    disagreements: list[Disagreement] = []
    for case in cases:
        first = decide_case(case.comment, case.first, low=low, high=high)
        second = decide_case(case.comment, case.second, low=low, high=high)
        for field_spec in FIELDS:
            field = field_spec.name
            if field not in first:
                continue
            overall.add(first[field], second[field])
            if tallies[field].add(first[field], second[field]):
                disagreements.append(Disagreement(case.name, field, first[field], second[field]))
    return AgreementReport(
        first_model=first_model,
        second_model=second_model,
        cases=len(cases),
        fields=tuple(tally.agreement(field) for field, tally in tallies.items() if tally.outcomes),
        overall=overall.agreement("all fields"),
        disagreements=tuple(disagreements),
    )


def _label(field: str) -> str:
    return field if field == "all fields" else spec(field).label


def report_text(report: AgreementReport, *, low: Decimal, high: Decimal) -> str:
    """The report as Markdown: a table per field, then every disagreement with both answers."""
    lines = [
        f"Agreement of {report.first_model} and {report.second_model} on {report.cases} "
        f"comments (abstention band {low} to {high})",
        "",
        "| Field | Same outcome | Same value, where both state it |",
        "|---|---|---|",
    ]
    for agreement in (*report.fields, report.overall):
        lines.append(
            f"| {_label(agreement.field)} | {agreement.outcomes.text()} | "
            f"{agreement.values.text()} |"
        )
    lines += ["", f"Disagreements: {len(report.disagreements)}"]
    for item in report.disagreements:
        lines += [
            f"- {item.case}, {_label(item.field)}",
            f"  - {item.first.text()}",
            f"  - {item.second.text()}",
        ]
    return "\n".join(lines) + "\n"
