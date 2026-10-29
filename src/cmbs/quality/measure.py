"""Measure extraction against the golden set, field by field. Pure.

**Reference labels.** Each golden comment has one or two labellers' answers per field; "not
stated" (None) is an answer. When everyone who labelled a field agrees, that is the reference.
When two labellers disagree the comment is left out of that field's measures until someone
adjudicates, and the report counts it; a disputed label is never picked for them.

**What is measured, per field,** from one prediction per comment: the value the pipeline found
(None when it found none) and whether the decision rules settled it without a person.

- Precision: of the comments where a value was found, how many found the reference value.
- Recall: of the comments whose reference states a value, how many found that value. A value
  found but of the wrong category counts against both.
- Auto-accepted accuracy: of the fields settled without review (a value auto-accepted, or
  "not stated" decided by the rules), how many match the reference. A wrong "not stated" is a
  missed value nobody saw, so it counts here too. This is what tests the abstention band: the
  band is "holding" when the lower end of the interval is above 0.85 (AUTO_ACCEPT_FLOOR).

**Against the previous report**, any measure whose three-place rate fell is flagged:
"borrower request recall fell from 0.950 to 0.800". A fall within the noise is still flagged;
the intervals beside it let the reader judge it.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from cmbs.extraction.fields import BY_NAME, spec
from cmbs.quality.stats import Proportion, cohen_kappa, rounded

AUTO_ACCEPT_FLOOR = Decimal("0.85")
HOLDING = "holding"
NOT_HOLDING = "not holding"
NOT_MEASURED = "not measured"
# The measures compared with the previous report, by the name the flags use.
MEASURES = (
    ("precision", "precision"),
    ("recall", "recall"),
    ("automatic", "auto-accepted accuracy"),
)


@dataclass(frozen=True)
class GoldenComment:
    """A golden comment's labels: labeller -> field -> value (None for "not stated").

    `independent` is False when the labels are not independent judgments: a comment proposed
    from a panel decision and confirmed by a second analyst, who saw the first label. Such a
    comment counts as a reference but not toward labeller agreement.
    """

    key: str
    labels: Mapping[str, Mapping[str, str | None]]
    independent: bool = True


@dataclass(frozen=True)
class Prediction:
    value: str | None
    automatic: bool
    # The settings version in force when the prediction was made (its abstention band decided
    # `automatic`).
    settings_version: int | None = None


class Unlabelled:
    """The reference for a comment no one labelled for the field."""


class Disputed:
    """The reference for a comment whose labellers disagree on the field."""


UNLABELLED = Unlabelled()
DISPUTED = Disputed()


def reference(item: GoldenComment, name: str) -> str | None | Unlabelled | Disputed:
    answers = [labels[name] for labels in item.labels.values() if name in labels]
    if not answers:
        return UNLABELLED
    if any(answer != answers[0] for answer in answers):
        return DISPUTED
    return answers[0]


@dataclass(frozen=True)
class FieldQuality:
    field: str
    compared: int
    disputed: int
    precision: Proportion
    recall: Proportion
    automatic: Proportion

    @property
    def label(self) -> str:
        return spec(self.field).label

    @property
    def band(self) -> str:
        interval = self.automatic.interval
        if interval is None:
            return NOT_MEASURED
        return HOLDING if interval[0] > AUTO_ACCEPT_FLOOR else NOT_HOLDING


def measure_field(
    items: Sequence[GoldenComment], predictions: Mapping[str, Mapping[str, Prediction]], name: str
) -> FieldQuality:
    found = right = stated = recalled = settled = settled_right = compared = disputed = 0
    for item in items:
        expected = reference(item, name)
        if expected is UNLABELLED:
            continue
        if expected is DISPUTED:
            disputed += 1
            continue
        predicted = predictions[item.key][name]
        compared += 1
        correct = predicted.value == expected
        if predicted.value is not None:
            found += 1
            right += correct
        if expected is not None:
            stated += 1
            recalled += correct
        if predicted.automatic:
            settled += 1
            settled_right += correct
    return FieldQuality(
        field=name,
        compared=compared,
        disputed=disputed,
        precision=Proportion(right, found),
        recall=Proportion(recalled, stated),
        automatic=Proportion(settled_right, settled),
    )


@dataclass(frozen=True)
class Agreement:
    """Labeller agreement on the comments two people labelled."""

    labellers: tuple[str, ...]
    overall: Proportion
    kappa: dict[str, Decimal | None] = field(default_factory=dict)


def agreement(items: Sequence[GoldenComment], fields: Sequence[str]) -> Agreement:
    """A double-labelled comment agrees when both labellers gave the same answer on every field
    they both labelled. Only independently labelled comments count."""
    doubled = [item for item in items if item.independent and len(item.labels) >= 2]
    labellers = sorted({name for item in doubled for name in item.labels})
    agreed = 0
    pairs: dict[str, list[tuple[str | None, str | None]]] = {name: [] for name in fields}
    for item in doubled:
        first, second = (item.labels[name] for name in sorted(item.labels)[:2])
        shared = [name for name in fields if name in first and name in second]
        agreed += all(first[name] == second[name] for name in shared)
        for name in shared:
            pairs[name].append((first[name], second[name]))
    return Agreement(
        labellers=tuple(labellers),
        overall=Proportion(agreed, len(doubled)),
        kappa={name: cohen_kappa(found) for name, found in pairs.items()},
    )


def labelled_fields(items: Sequence[GoldenComment]) -> list[str]:
    """Every field some labeller answered, in the order the extraction defines fields."""
    named = {name for item in items for answers in item.labels.values() for name in answers}
    return [name for name in BY_NAME if name in named]


def missing_predictions(
    items: Sequence[GoldenComment], predictions: Mapping[str, Mapping[str, Prediction]], fields
) -> list[str]:
    """Golden comments with no prediction for a measured field, as 'G007 borrower request'."""
    return [
        f"{item.key} {spec(name).label}"
        for item in items
        for name in fields
        if name not in predictions.get(item.key, {})
    ]


def band_text(quality: FieldQuality) -> str:
    """Why the abstention band holds or not, from the field's auto-accepted accuracy."""
    shown = quality.automatic.shown()
    if shown is None:
        return f'the abstention band is "{NOT_MEASURED}": no {quality.label} was auto-accepted'
    rate, low, _ = shown
    relation = "is above" if quality.band == HOLDING else "is not above"
    return (
        f'the abstention band is "{quality.band}": auto-accepted {quality.label} accuracy '
        f"{rate}, lower bound {low} {relation} {AUTO_ACCEPT_FLOOR}"
    )


def as_json(quality: FieldQuality) -> dict[str, Any]:
    def proportion(value: Proportion) -> dict[str, Any]:
        shown = value.shown()
        return {
            "successes": value.successes,
            "trials": value.trials,
            "rate": shown[0] if shown else None,
            "low": shown[1] if shown else None,
            "high": shown[2] if shown else None,
        }

    return {
        "field": quality.field,
        "label": quality.label,
        "compared": quality.compared,
        "disputed": quality.disputed,
        "precision": proportion(quality.precision),
        "recall": proportion(quality.recall),
        "automatic": proportion(quality.automatic),
        "band": quality.band,
    }


def worse(previous: Mapping[str, Any], current: Sequence[FieldQuality]) -> list[str]:
    """Measures that fell since the previous report (its stored results), in field order."""
    flags: list[str] = []
    for quality in current:
        before = previous.get(quality.field)
        if before is None:
            continue
        for key, name in MEASURES:
            then = before[key]["rate"]
            now = getattr(quality, key).rate
            if then is None or now is None:
                continue
            if rounded(now) < Decimal(then):
                flags.append(f"{quality.label} {name} fell from {then} to {rounded(now)}")
    return flags
