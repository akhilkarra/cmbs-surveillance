"""The refresh routine's additions to a run: extraction, the roll-forward check, EDGAR's 403.

EDGAR and the model are replayed from the synthetic recordings (`tests/test_routine_world.py`).
The roll-forward check comes from `cmbs.crosscheck` (built in parallel); these tests stand a
module in for it that flags what they say.
"""

import sys
import types
import urllib.error
from typing import Any

import pytest
from sqlalchemy import func, select

from cmbs import clock
from cmbs.checks.service import raise_flag
from cmbs.checks.tables import CheckFlag
from cmbs.distribution.tables import DistributionReport
from cmbs.domain import Period
from cmbs.edgar.replay import Replay
from cmbs.extraction.tables import Extraction, ReviewItem
from cmbs.model.client import ModelClient
from cmbs.model.recordings import DbRecordings
from cmbs.refresh import service
from cmbs.refresh.report import routine_report
from cmbs.refresh.service import (
    CROSSCHECK,
    EDGAR_FORBIDDEN,
    Stage,
    edgar_forbidden,
    not_loaded,
    rollforward_breaks,
)
from cmbs.refresh.sources import FirstRecorded, Sources
from cmbs.tape.tables import LoanPeriod
from cmbs.testing.bdd import in_session
from cmbs.testing.world import FIXTURE_DEAL
from tests.conftest import needs_db
from tests.fixtures.model.synthetic import load as load_recordings
from tests.fixtures.refresh.offline import offline_fixture  # noqa: F401
from tests.test_routine_world import (
    EDGAR,
    LOAD_DAY,
    Archive,
    backfill,
    exhibit_url,
    register,
    sources,
)

AUG, SEP = Period(2026, 8), Period(2026, 9)


def forbidden(url: str = "https://www.sec.gov/x") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, 403, "Forbidden", None, None)  # pyright: ignore[reportArgumentType]


# ---------------------------------------------------------------------------- pure


def test_edgar_forbidden_is_an_http_403_only():
    assert edgar_forbidden(forbidden())
    not_found = urllib.error.HTTPError("u", 404, "Not Found", None, None)  # pyright: ignore[reportArgumentType]
    assert not edgar_forbidden(not_found)
    assert not edgar_forbidden(OSError("403"))


def test_a_403_says_how_to_fix_it_whatever_the_stage():
    for stage in (Stage.FETCH_TAPE, Stage.FETCH_REPORT):
        error = not_loaded(stage, forbidden("https://www.sec.gov/a/b.xml"))
        assert (error.reason, error.detail) == (
            EDGAR_FORBIDDEN,
            "fetching https://www.sec.gov/a/b.xml",
        )
    assert EDGAR_FORBIDDEN == (
        "HTTP 403 from EDGAR: set SEC_USER_AGENT to a string with a contact address"
    )
    other = not_loaded(Stage.FETCH_TAPE, OSError("timed out"))
    assert (other.reason, other.detail) == ("ABS-EE could not be fetched", "timed out")
    assert not_loaded(Stage.EXTRACT, ValueError("x")).reason == "comments could not be extracted"


# ---------------------------------------------------------------------------- the roll-forward


@pytest.fixture
def crosscheck(monkeypatch):
    """A stand-in for cmbs.crosscheck.service whose check flags the loans a test names."""
    module = types.ModuleType(CROSSCHECK)
    calls: list[dict[str, Any]] = []
    breaks: list[tuple[str, Period]] = []

    def rollforward(db, *, deal_id: int, period: Period):
        calls.append({"deal_id": deal_id, "period": period})
        for loan, month in breaks:
            raise_flag(
                db, deal_id=deal_id, loan=loan, check="roll-forward", message="break",
                period=month.first_day,
            )  # fmt: skip
        return []

    module.rollforward = rollforward  # pyright: ignore[reportAttributeAccessIssue]
    monkeypatch.setitem(sys.modules, CROSSCHECK, module)
    return types.SimpleNamespace(calls=calls, breaks=breaks)


def test_without_the_crosscheck_package_the_roll_forward_is_not_checked(monkeypatch):
    monkeypatch.setitem(sys.modules, CROSSCHECK, None)  # imports of it fail

    def missing(name: str):
        raise ModuleNotFoundError(f"No module named {name!r}", name="cmbs.crosscheck")

    monkeypatch.setattr(service.importlib, "import_module", missing)
    assert rollforward_breaks(None, deal_id=1, period=SEP) is None  # pyright: ignore[reportArgumentType]


def test_a_crosscheck_missing_something_else_is_a_bug(monkeypatch):
    def broken(name: str):
        raise ModuleNotFoundError("No module named 'numpy'", name="numpy")

    monkeypatch.setattr(service.importlib, "import_module", broken)
    with pytest.raises(ModuleNotFoundError):
        rollforward_breaks(None, deal_id=1, period=SEP)  # pyright: ignore[reportArgumentType]

    def nameless(name: str):
        raise ModuleNotFoundError("no name")

    monkeypatch.setattr(service.importlib, "import_module", nameless)
    with pytest.raises(ModuleNotFoundError):
        rollforward_breaks(None, deal_id=1, period=SEP)  # pyright: ignore[reportArgumentType]


@needs_db
def test_breaks_are_the_flags_the_check_raised_for_the_deal_and_month(seeded, crosscheck):
    deal_id = in_session(register)
    in_session(
        lambda db: raise_flag(
            db, deal_id=deal_id, loan="1", check="reconciliation", message="x",
            period=SEP.first_day,
        )
    )  # fmt: skip
    crosscheck.breaks += [("7", SEP), ("5", SEP), ("7", AUG)]
    found = in_session(lambda db: rollforward_breaks(db, deal_id=deal_id, period=SEP))
    assert found == 2  # not August's, nor the reconciliation flag raised before
    assert crosscheck.calls == [{"deal_id": deal_id, "period": SEP}]
    crosscheck.breaks.clear()
    assert in_session(lambda db: rollforward_breaks(db, deal_id=deal_id, period=SEP)) == 0


@needs_db
def test_a_break_ends_the_run_with_problems(seeded, crosscheck):
    backfill(in_session, AUG, AUG)
    crosscheck.breaks.append(("7", SEP))
    clock.set_now(LOAD_DAY)
    report = service.refresh_deal("quinn", FIXTURE_DEAL, sources=sources())
    month = report.run.summary["deals"][0]["months"][0]
    assert (month["period"], month["rollforward_breaks"]) == ("September 2026", 1)
    assert report.run.status == "completed with problems"
    assert routine_report(report.run).splitlines()[0] == "1 roll-forward break"
    flags = in_session(lambda db: db.scalars(select(CheckFlag.loan)).all())
    assert flags == ["7"]


# ---------------------------------------------------------------------------- extraction


@needs_db
def test_a_month_extracts_its_comments_under_the_run(seeded):
    report = backfill(in_session, SEP, SEP)
    month = report.run.summary["deals"][0]["months"][0]
    assert (month["comments"], month["review_items"]) == (1, 0)
    runs = in_session(lambda db: db.scalars(select(Extraction.run_id)).all())
    assert runs == [report.run.number]


@needs_db
def test_review_items_are_counted(seeded):
    """A recording that leaves the borrower request unsettled sends that field to review."""
    from tests.fixtures.model import synthetic

    answers = dict(synthetic.LOAN_2_SEPTEMBER)
    answers["borrower_request"] = ("maturity extension", 0.55, 0.94, 3)
    answers["request_status"] = ("pending", 0.50, 0.90, 4)
    unsettled = synthetic.recording(synthetic.loan_2_september_comment(), answers)

    def model(db, user: str) -> ModelClient:
        DbRecordings(db).put(unsettled)
        return ModelClient(DbRecordings(db), replay_only=True, allow_synthetic=True)

    in_session(register)
    clock.set_now(LOAD_DAY)
    report = service.backfill(
        "quinn",
        FIXTURE_DEAL,
        SEP,
        SEP,
        sources=Sources(
            edgar=FirstRecorded(*(Replay(d) for d in EDGAR)), archive=Archive(), model=model
        ),
    )
    month = report.run.summary["deals"][0]["months"][0]
    assert (month["comments"], month["review_items"]) == (1, 2)
    items = in_session(lambda db: sorted(db.scalars(select(ReviewItem.field)).all()))
    assert items == ["borrower_request", "request_status"]


@needs_db
def test_a_comment_that_cannot_be_extracted_keeps_the_month_out(seeded):
    """Replay without the synthetic recordings answers nothing: the month is not loaded."""

    def no_answers(db, user: str) -> ModelClient:
        return ModelClient(DbRecordings(db), replay_only=True)

    in_session(register)
    clock.set_now(LOAD_DAY)
    report = service.backfill(
        "quinn",
        FIXTURE_DEAL,
        SEP,
        SEP,
        sources=Sources(
            edgar=FirstRecorded(*(Replay(d) for d in EDGAR)), archive=Archive(), model=no_answers
        ),
    )
    month = report.run.summary["deals"][0]["months"][0]
    assert (month["outcome"], month["reason"]) == ("not loaded", "comments could not be extracted")
    assert month["detail"].startswith("no recorded answer for this input: loan 2 of "), month
    assert report.run.status == "failed"

    def stored(db):
        return [
            db.scalar(select(func.count()).select_from(table))
            for table in (LoanPeriod, DistributionReport, Extraction)
        ]

    assert in_session(stored) == [0, 0, 0]


@needs_db
def test_a_run_without_a_model_leaves_comments_unread(seeded):
    in_session(register)
    clock.set_now(LOAD_DAY)
    plain = Sources(edgar=FirstRecorded(*(Replay(d) for d in EDGAR)), archive=Archive())
    report = service.backfill("quinn", FIXTURE_DEAL, SEP, SEP, sources=plain)
    month = report.run.summary["deals"][0]["months"][0]
    assert (month["outcome"], month["comments"], month["review_items"]) == ("loaded", None, 0)
    assert in_session(lambda db: db.scalar(select(func.count()).select_from(Extraction))) == 0
    in_session(lambda db: load_recordings(DbRecordings(db)))


# ---------------------------------------------------------------------------- EDGAR's 403


@needs_db
def test_a_403_stops_the_month_and_says_how_to_fix_it(seeded):
    backfill(in_session, AUG, AUG)
    archive = Archive()
    url = exhibit_url(FIXTURE_DEAL, SEP, "ABS-EE")
    archive.forbidden.add(url)
    clock.set_now(LOAD_DAY)
    report = service.refresh_deal("quinn", FIXTURE_DEAL, sources=sources(archive))
    assert report.run.status == "failed"
    assert report.run.stopped == {
        "deal": FIXTURE_DEAL,
        "period": "September 2026",
        "error": EDGAR_FORBIDDEN,
        "detail": f"fetching {url}",
    }


@needs_db
def test_a_403_from_discovery_says_how_to_fix_it(seeded):
    class Refusing:
        def get_json(self, url: str) -> Any:
            raise forbidden(url)

    in_session(register)
    clock.set_now(LOAD_DAY)
    report = service.refresh_deal(
        "quinn", FIXTURE_DEAL, sources=Sources(edgar=Refusing(), archive=Archive())
    )
    deal = report.run.summary["deals"][0]
    assert (deal["outcome"], deal["reason"]) == (
        "failed",
        f"filing discovery failed: {EDGAR_FORBIDDEN}",
    )
