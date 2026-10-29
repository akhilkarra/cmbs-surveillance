"""Proportions with confidence intervals, and labeller agreement. Pure, exact Decimal arithmetic.

**Intervals are Wilson score intervals at 95%.** The golden set is small (around 100 comments,
40 positives for a field) and the rates measured sit near 1, where the textbook normal
("Wald") interval runs past 1 and is far too narrow: 58 of 60 would read 0.921 to 1.012. The
Wilson interval stays inside 0 to 1, behaves well for small counts and is the usual choice for
binomial proportions; Clopper-Pearson would be more conservative than a quality gate needs.

**Agreement is reported two ways.** The headline is simple agreement, "19 of 20
double-labelled comments", with its Wilson interval: it is what a reviewer can check by hand,
and it is the number the risk officer asks for. Cohen's kappa is given beside it, per field,
because simple agreement flatters fields where one answer dominates (most comments state no
borrower request, so two labellers agree by chance alone); kappa corrects for that. Kappa is
not the headline because on 20 items with a dominant category it swings widely with one label
(the "kappa paradox"), so it reads as a warning sign, not a score.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

# The 97.5th percentile of the standard normal distribution: a two-sided 95% interval.
Z95 = Decimal("1.959963984540054")
PLACES = Decimal("0.001")


def rounded(value: Decimal) -> Decimal:
    """To three places, half up, as reports show rates: 0.9047... -> 0.905."""
    return value.quantize(PLACES, rounding=ROUND_HALF_UP)


def wilson(successes: int, trials: int, z: Decimal = Z95) -> tuple[Decimal, Decimal]:
    """The Wilson score interval for `successes` out of `trials` (trials > 0)."""
    if trials <= 0 or not 0 <= successes <= trials:
        raise ValueError(f"not a proportion: {successes} of {trials}")
    n = Decimal(trials)
    p = Decimal(successes) / n
    z2 = z * z
    denominator = 1 + z2 / n
    centre = (p + z2 / (2 * n)) / denominator
    margin = z * (p * (1 - p) / n + z2 / (4 * n * n)).sqrt() / denominator
    return max(Decimal(0), centre - margin), min(Decimal(1), centre + margin)


@dataclass(frozen=True)
class Proportion:
    """`successes` of `trials`, with its rate and 95% Wilson interval; no rate when no trials."""

    successes: int
    trials: int

    @property
    def measured(self) -> bool:
        return self.trials > 0

    @property
    def rate(self) -> Decimal | None:
        return Decimal(self.successes) / Decimal(self.trials) if self.trials else None

    @property
    def interval(self) -> tuple[Decimal, Decimal] | None:
        return wilson(self.successes, self.trials) if self.trials else None

    def shown(self) -> tuple[str, str, str] | None:
        """(rate, low, high) as three-place text: ('0.905', '0.779', '0.962')."""
        rate, interval = self.rate, self.interval
        if rate is None or interval is None:
            return None
        return str(rounded(rate)), str(rounded(interval[0])), str(rounded(interval[1]))

    def text(self) -> str:
        """'0.905 (95% interval 0.779 to 0.962; 38 of 42)', or 'not measured (0 of 0)'."""
        shown = self.shown()
        if shown is None:
            return "not measured (0 of 0)"
        rate, low, high = shown
        return f"{rate} (95% interval {low} to {high}; {self.successes} of {self.trials})"


def cohen_kappa(pairs: Sequence[tuple[str | None, str | None]]) -> Decimal | None:
    """Cohen's kappa for two labellers' answers on the same items ("not stated" is None, a
    category of its own). None when there are no pairs or chance agreement is certain (both
    labellers gave one and the same answer throughout), where kappa is undefined."""
    if not pairs:
        return None
    n = Decimal(len(pairs))
    observed = Decimal(sum(1 for first, second in pairs if first == second)) / n
    firsts = Counter(first for first, _ in pairs)
    seconds = Counter(second for _, second in pairs)
    chance = sum((Decimal(firsts[label]) / n) * (Decimal(seconds[label]) / n) for label in firsts)
    chance = Decimal(chance)
    if chance == 1:
        return None
    return (observed - chance) / (1 - chance)
