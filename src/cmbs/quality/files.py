"""Read a golden-set labels file and a predictions file (JSON). Pure; refusals say what is wrong.

A golden set (tests/fixtures/quality/golden_set.json is the synthetic one):

    {"items": [{"key": "G001", "comment": "...",
                "labels": {"alex": {"borrower_request": "forbearance", "request_status": null}}}]}

A predictions file, one entry per golden comment and measured field:

    {"model": "jev-1.14.0", "prompt_version": 3, "settings_version": 7,
     "predictions": {"G001": {"borrower_request": {"value": "forbearance", "automatic": true}}}}

`settings_version` names the settings in force when the predictions were made (the extraction
records it); an entry may name its own when the predictions came from several.

A value is a field's category, or null for "not stated"; labels and predictions are checked
against the field's options, so a typo cannot be measured as a wrong answer.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TypeGuard

from cmbs.errors import Refused
from cmbs.extraction.fields import BY_NAME, InvalidValue, canonical
from cmbs.quality.measure import GoldenComment, Prediction


class BadFile(Refused):
    """A labels or predictions file this code cannot read faithfully."""


@dataclass(frozen=True)
class LabelledComment:
    key: str
    comment: str
    labels: dict[str, dict[str, str | None]]

    def golden(self) -> GoldenComment:
        return GoldenComment(self.key, self.labels)


@dataclass(frozen=True)
class PredictionSet:
    model: str
    prompt_version: int
    predictions: dict[str, dict[str, Prediction]]

    @property
    def settings_versions(self) -> list[int | None]:
        """Every settings version that produced a prediction; None for a prediction that does
        not say."""
        found = {
            p.settings_version for by_field in self.predictions.values() for p in by_field.values()
        }
        return sorted(found, key=lambda v: (v is None, v or 0))

    @property
    def fields(self) -> list[str]:
        """The fields predicted, in the order the extraction defines them."""
        named = {name for by_field in self.predictions.values() for name in by_field}
        return [name for name in BY_NAME if name in named]

    def as_json(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "prompt_version": self.prompt_version,
            "predictions": {
                key: {
                    name: {
                        "value": p.value,
                        "automatic": p.automatic,
                        "settings_version": p.settings_version,
                    }
                    for name, p in by_field.items()
                }
                for key, by_field in self.predictions.items()
            },
        }


def _object(value: object, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BadFile(f"{where} must be an object")
    return value  # pyright: ignore[reportUnknownVariableType]


def _text(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BadFile(f"{where} must be non-empty text")
    return value.strip()


def value_of(name: str, value: object, where: str) -> str | None:
    """A field's value as stored, or None for "not stated"."""
    if name not in BY_NAME:
        raise BadFile(f"{where}: there is no field {name!r}")
    if value is None:
        return None
    if not isinstance(value, str):
        raise BadFile(f"{where}: {name} must be text or null")
    try:
        return canonical(BY_NAME[name], value)
    except InvalidValue as invalid:
        raise BadFile(f"{where}: {invalid}") from None


def read_golden(data: object) -> list[LabelledComment]:
    items = _object(data, "the labels file").get("items")
    if not isinstance(items, list) or not items:
        raise BadFile("the labels file has no items")
    found: list[LabelledComment] = []
    seen: set[str] = set()
    for number, raw in enumerate(items, start=1):
        entry = _object(raw, f"item {number}")
        key = _text(entry.get("key"), f"item {number}'s key")
        if key in seen:
            raise BadFile(f"golden item {key} appears twice")
        seen.add(key)
        comment = _text(entry.get("comment"), f"{key}'s comment")
        labels = _object(entry.get("labels"), f"{key}'s labels")
        if not labels:
            raise BadFile(f"{key} has no labels")
        found.append(
            LabelledComment(
                key=key,
                comment=comment,
                labels={
                    _text(labeller, f"{key}'s labeller"): {
                        name: value_of(name, value, f"{key}, {labeller}")
                        for name, value in _object(answers, f"{key}, {labeller}").items()
                    }
                    for labeller, answers in labels.items()
                },
            )
        )
    return found


def read_predictions(data: object) -> PredictionSet:
    body = _object(data, "the predictions file")
    model = _text(body.get("model"), "the model")
    version = body.get("prompt_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise BadFile("the prompt version must be a whole number from 1")
    default_version = body.get("settings_version")
    if default_version is not None and not _version(default_version):
        raise BadFile("the settings version must be a whole number from 1")
    raw = _object(body.get("predictions"), "the predictions")
    predictions: dict[str, dict[str, Prediction]] = {}
    for key, by_field in raw.items():
        predictions[key] = {}
        for name, entry in _object(by_field, f"{key}'s predictions").items():
            where = f"{key}, {name}"
            fields = _object(entry, where)
            automatic = fields.get("automatic")
            if not isinstance(automatic, bool):
                raise BadFile(f"{where}: automatic must be true or false")
            produced_by = fields.get("settings_version", default_version)
            if not _version(produced_by):
                raise BadFile(f"{where}: no settings version says what produced it")
            predictions[key][name] = Prediction(
                value_of(name, fields.get("value"), key), automatic, produced_by
            )
    return PredictionSet(model, version, predictions)


def _version(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1
