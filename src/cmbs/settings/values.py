"""The settings themselves, as a typed, immutable record, and the pure rules for changing them.

Nothing here touches the database: applying a proposed change, checking it, describing it and
building each setting's history are plain functions over `Settings` values.
"""

import datetime as dt
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from cmbs.errors import Refused


class Settings(BaseModel):
    """Every number that changes what the panel says. Stored as JSON, one version per row."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dscr_alert_threshold: Decimal = Decimal("1.00")
    abstention_low: Decimal = Decimal("0.30")
    abstention_high: Decimal = Decimal("0.70")
    transfer_date_tolerance_periods: int = 2
    model: str = "jev-1.14.0"
    prompt_version: int = 3
    zero_data_retention: bool = False

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.dscr_alert_threshold <= 0:
            raise ValueError("the DSCR alert threshold must be above 0")
        if not 0 <= self.abstention_low < self.abstention_high <= 1:
            raise ValueError("the abstention band must rise from low to high within 0 to 1")
        if self.transfer_date_tolerance_periods < 0:
            raise ValueError("the transfer-date tolerance cannot be negative")
        if not self.model.strip():
            raise ValueError("the model must be named")
        if self.prompt_version < 1:
            raise ValueError("the prompt version must be 1 or more")
        return self


FIELDS: tuple[str, ...] = tuple(Settings.model_fields)

LABELS: dict[str, str] = {
    "dscr_alert_threshold": "DSCR alert threshold",
    "abstention_low": "low end of the abstention band",
    "abstention_high": "high end of the abstention band",
    "transfer_date_tolerance_periods": "transfer-date tolerance",
    "model": "model",
    "prompt_version": "prompt version",
    "zero_data_retention": "zero data retention",
}

# A change to what the model is asked, or which model answers, must cite a quality report
# measured on the golden set (grill log Q39, Q46).
QUALITY_REPORT_FIELDS = frozenset({"model", "prompt_version"})


class InvalidSetting(Refused):
    """A proposed change names no setting, changes nothing, or gives an impossible value."""


def _ratio(value: Decimal) -> str:
    """Two decimals unless the value has more: 1 -> '1.00', 1.125 -> '1.125'."""
    exponent = value.as_tuple().exponent
    return f"{value:.2f}" if isinstance(exponent, int) and exponent >= -2 else str(value)


def format_field(field: str, value: Any) -> str:
    """One field's value as the panel shows it: '1.05x', '0.30', '2 periods', 'on'."""
    if field == "dscr_alert_threshold":
        return f"{_ratio(Decimal(value))}x"
    if field in ("abstention_low", "abstention_high"):
        return _ratio(Decimal(value))
    if field == "transfer_date_tolerance_periods":
        return f"{value} period" if int(value) == 1 else f"{value} periods"
    if field == "zero_data_retention":
        return "on" if value else "off"
    return str(value)


def _message(error: ValidationError) -> str:
    first = error.errors()[0]
    location = first["loc"]
    if location and location[0] in LABELS:
        return f"the {LABELS[str(location[0])]} cannot be {first['input']!r}"
    return str(first["msg"]).removeprefix("Value error, ")


def apply_changes(current: Settings, changes: Mapping[str, object]) -> Settings:
    """`current` with `changes` applied and checked; InvalidSetting if they cannot apply."""
    unknown = [field for field in changes if field not in LABELS]
    if unknown:
        raise InvalidSetting(f"there is no setting named {unknown[0]!r}")
    try:
        changed = Settings.model_validate({**current.model_dump(), **changes})
    except ValidationError as error:
        raise InvalidSetting(_message(error)) from None
    if changed == current:
        raise InvalidSetting("the proposal changes nothing")
    return changed


def changed_fields(before: Settings, after: Settings) -> tuple[str, ...]:
    """The fields whose values differ, in declaration order."""
    return tuple(field for field in FIELDS if getattr(before, field) != getattr(after, field))


def as_json(values: Settings, fields: Sequence[str]) -> dict[str, Any]:
    """The named fields as JSON-safe values (decimals as strings), for storage."""
    dumped = values.model_dump(mode="json")
    return {field: dumped[field] for field in fields}


def needs_quality_report(fields: Sequence[str]) -> bool:
    return not QUALITY_REPORT_FIELDS.isdisjoint(fields)


def describe_changes(before: Mapping[str, Any], after: Mapping[str, Any]) -> str:
    """'DSCR alert threshold 1.00x to 1.05x; prompt version 3 to 4'."""
    return "; ".join(
        f"{LABELS[field]} {format_field(field, before[field])} to {format_field(field, value)}"
        for field, value in after.items()
    )


def stale_fields(proposed_from: Mapping[str, Any], latest: Settings) -> list[tuple[str, Any, Any]]:
    """Fields that have changed since the proposal was made: (field, then, now).

    A proposal records the value each field had when it was made. If another approval has
    changed one of those fields since, approving would silently overwrite that decision.
    """
    now = latest.model_dump(mode="json")
    return [
        (field, then, now[field])
        for field, then in proposed_from.items()
        if not _same(field, then, now[field])
    ]


def _same(field: str, first: Any, second: Any) -> bool:
    """Equal as values of the field's type: '1.0' and '1.00' are the same ratio."""
    if Settings.model_fields[field].annotation is Decimal:
        return Decimal(str(first)) == Decimal(str(second))
    return first == second


@dataclass(frozen=True)
class Approved:
    """One approved version, as the settings history needs it."""

    version: int
    values: Settings
    proposed_by: str
    approved_by: str
    approved_at: dt.datetime


@dataclass(frozen=True)
class Shown:
    """One line of the settings as the panel lists them; a line may span several fields."""

    label: str
    fields: tuple[str, ...]
    text: Callable[[Settings], str]


SHOWN: tuple[Shown, ...] = (
    Shown(
        "DSCR alert threshold",
        ("dscr_alert_threshold",),
        lambda s: format_field("dscr_alert_threshold", s.dscr_alert_threshold),
    ),
    Shown(
        "abstention band",
        ("abstention_low", "abstention_high"),
        lambda s: f"{_ratio(s.abstention_low)} to {_ratio(s.abstention_high)}",
    ),
    Shown(
        "transfer-date tolerance",
        ("transfer_date_tolerance_periods",),
        lambda s: format_field(
            "transfer_date_tolerance_periods", s.transfer_date_tolerance_periods
        ),
    ),
    Shown("model", ("model",), lambda s: s.model),
    Shown("prompt version", ("prompt_version",), lambda s: str(s.prompt_version)),
    Shown(
        "zero data retention",
        ("zero_data_retention",),
        lambda s: format_field("zero_data_retention", s.zero_data_retention),
    ),
)


@dataclass(frozen=True)
class SettingChange:
    """A version that set a line to a new value."""

    version: int
    value: str
    proposed_by: str
    approved_by: str
    approved_at: dt.datetime


@dataclass(frozen=True)
class SettingLine:
    """A setting in force: its value, the change that set it, and every earlier change."""

    label: str
    value: str
    since: SettingChange
    history: tuple[SettingChange, ...]  # newest first; history[0] is `since`


def setting_lines(versions: Sequence[Approved]) -> list[SettingLine]:
    """Each setting's value in the last of `versions`, with the versions that changed it.

    `versions` are in approval order, oldest first, and must not be empty.
    """
    if not versions:
        raise ValueError("no approved versions")
    lines = []
    for shown in SHOWN:
        changes: list[SettingChange] = []
        for approved in versions:
            value = shown.text(approved.values)
            if not changes or changes[-1].value != value:
                changes.append(
                    SettingChange(
                        version=approved.version,
                        value=value,
                        proposed_by=approved.proposed_by,
                        approved_by=approved.approved_by,
                        approved_at=approved.approved_at,
                    )
                )
        history = tuple(reversed(changes))
        since = history[0]
        lines.append(
            SettingLine(label=shown.label, value=since.value, since=since, history=history)
        )
    return lines
