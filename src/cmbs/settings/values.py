"""The settings themselves, as a typed, immutable record."""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict


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
