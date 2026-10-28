"""Every domain refusal follows one contract: steps capture it, and the API returns it readably."""

import pytest

from cmbs.access.service import PermissionRefused, UnknownUser
from cmbs.deals.service import ConflictingDeal
from cmbs.errors import Refused
from cmbs.filings.service import ConflictingFiling
from cmbs.runs.service import RunAlreadyFinished
from cmbs.settings.service import OutOfOrderVersion
from cmbs.testing.bdd import attempt

REFUSALS = [
    PermissionRefused,
    UnknownUser,
    ConflictingDeal,
    ConflictingFiling,
    RunAlreadyFinished,
    OutOfOrderVersion,
]


@pytest.mark.parametrize("refusal", REFUSALS)
def test_domain_refusals_derive_from_refused(refusal):
    assert issubclass(refusal, Refused)


@pytest.mark.parametrize("refusal", REFUSALS)
def test_scenario_steps_capture_every_refusal(refusal):
    class Context:
        refusal: Exception | None = None

    def refuse():
        raise refusal("not allowed")

    context = Context()
    assert attempt(context, refuse) is None
    assert isinstance(context.refusal, refusal) and str(context.refusal) == "not allowed"


def test_programming_errors_are_not_captured_as_refusals():
    class Context:
        refusal: Exception | None = None

    def bug():
        raise ValueError("bad argument")

    with pytest.raises(ValueError, match="bad argument"):
        attempt(Context(), bug)
