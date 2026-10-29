"""Wilson intervals, rounding and Cohen's kappa, with exact values."""

from decimal import Decimal

import pytest

from cmbs.quality.stats import Z95, Proportion, cohen_kappa, rounded, wilson


@pytest.mark.parametrize(
    ("successes", "trials", "shown"),
    [
        # The feature's numbers.
        (38, 42, ("0.905", "0.779", "0.962")),
        (38, 40, ("0.950", "0.835", "0.986")),
        (58, 60, ("0.967", "0.886", "0.991")),
        (32, 40, ("0.800", "0.652", "0.895")),
        (19, 20, ("0.950", "0.764", "0.991")),
        # The ends: never outside 0 to 1, and not degenerate at 0 or n.
        (0, 10, ("0.000", "0.000", "0.278")),
        (10, 10, ("1.000", "0.722", "1.000")),
        (1, 2, ("0.500", "0.095", "0.905")),
    ],
)
def test_wilson_intervals(successes, trials, shown):
    assert Proportion(successes, trials).shown() == shown


def test_wilson_is_symmetric_about_a_half():
    low, high = wilson(3, 10)
    other_low, other_high = wilson(7, 10)
    assert rounded(low) == rounded(1 - other_high)
    assert rounded(high) == rounded(1 - other_low)


def test_wilson_with_a_larger_z_is_wider():
    narrow = wilson(38, 42)
    wide = wilson(38, 42, z=Decimal("2.575829"))
    assert wide[0] < narrow[0] and wide[1] > narrow[1]
    assert Decimal("1.959") < Z95 < Decimal("1.960")


@pytest.mark.parametrize(("successes", "trials"), [(0, 0), (-1, 4), (5, 4), (1, -1)])
def test_wilson_refuses_what_is_not_a_proportion(successes, trials):
    with pytest.raises(ValueError, match=f"^not a proportion: {successes} of {trials}$"):
        wilson(successes, trials)


def test_rounding_is_half_up_to_three_places():
    assert rounded(Decimal("0.9045")) == Decimal("0.905")
    assert rounded(Decimal("0.9044999")) == Decimal("0.904")
    assert rounded(Decimal("0.8")) == Decimal("0.800")


def test_a_proportion_without_trials_is_not_measured():
    empty = Proportion(0, 0)
    assert (empty.measured, empty.rate, empty.interval, empty.shown()) == (False, None, None, None)
    assert empty.text() == "not measured (0 of 0)"
    assert Proportion(1, 1).measured


def test_proportion_text():
    assert Proportion(38, 42).text() == "0.905 (95% interval 0.779 to 0.962; 38 of 42)"
    assert Proportion(38, 42).rate == Decimal(38) / Decimal(42)


def test_kappa_of_perfect_agreement_is_one():
    pairs = [("forbearance", "forbearance"), (None, None), (None, None)]
    assert cohen_kappa(pairs) == 1


def test_kappa_corrects_for_chance():
    # 10 items: both say None on 8, both say "a" on 1, one disagreement (a vs None).
    pairs = [(None, None)] * 8 + [("a", "a"), ("a", None)]
    # observed 0.9; chance = 0.8*0.9 + 0.2*0.1 = 0.74; kappa = 0.16 / 0.26
    assert rounded(cohen_kappa(pairs) or Decimal(-9)) == Decimal("0.615")


def test_kappa_below_chance_is_negative():
    pairs = [("a", "b"), ("b", "a")]
    assert cohen_kappa(pairs) == -1


def test_kappa_is_undefined_without_pairs_or_variation():
    assert cohen_kappa([]) is None
    assert cohen_kappa([(None, None), (None, None)]) is None
