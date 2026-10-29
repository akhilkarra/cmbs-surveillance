"""The refresh routine's additions to a run: extraction, the roll-forward check, EDGAR's 403.

EDGAR and the model are replayed from the synthetic recordings (`tests/test_routine_world.py`).
The checks come from `cmbs.crosscheck` (built in parallel); these tests stand a
module in for it that flags what they say.
"""

import sys
import types
import urllib.error
from typing import Any

import pytest
from sqlalchemy import func, select

from cmbs import clock
from cmbs.distribution.tables import DistributionReport
from cmbs.domain import Period
from cmbs.edgar.replay import Replay
from cmbs.errors import Refused
from cmbs.extraction.tables import Extraction, ReviewItem
from cmbs.model.client import ModelClient
from cmbs.model.recordings import DbRecordings
from cmbs.refresh import service
from cmbs.refresh.report import routine_report
from cmbs.refresh.service import (
    CROSSCHECK,
    EDGAR_FORBIDDEN,
    Checked,
    MonthNotLoaded,
    Stage,
    crosscheck_month,
    edgar_forbidden,
    not_loaded,
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


# ---------------------------------------------------------------------------- the checks


@pytest.fixture
def crosscheck(monkeypatch):
    """A stand-in for cmbs.crosscheck.service: its run_checks finds what a test sets."""
    module = types.ModuleType(CROSSCHECK)
    calls: list[dict[str, Any]] = []
    found = types.SimpleNamespace(breaks=0, findings=[], refuse=None, calls=calls)

    def run_checks(db, *, user: str, deal: str, period: Period, run_id: int | None):
        calls.append({"user": user, "deal": deal, "period": period, "run_id": run_id})
        if found.refuse is not None:
            raise found.refuse
        loan = types.SimpleNamespace(
            findings=[types.SimpleNamespace(status=status) for status in found.findings]
        )
        return types.SimpleNamespace(rollforward_breaks=found.breaks, text_vs_tape=[loan])

    module.run_checks = run_checks  # pyright: ignore[reportAttributeAccessIssue]
    monkeypatch.setitem(sys.modules, CROSSCHECK, module)
    return found


ACTOR = types.SimpleNamespace(user="quinn")
DEAL = types.SimpleNamespace(name=FIXTURE_DEAL)


def check(db=None):
    return crosscheck_month(db, actor=ACTOR, run_id=7, deal=DEAL, period=SEP)  # pyright: ignore[reportArgumentType]


def test_without_the_crosscheck_package_the_month_is_not_checked(monkeypatch):
    def missing(name: str):
        raise ModuleNotFoundError(f"No module named {name!r}", name="cmbs.crosscheck")

    monkeypatch.setattr(service.importlib, "import_module", missing)
    assert check() is None


def test_a_crosscheck_missing_something_else_is_a_bug(monkeypatch):
    def broken(name: str):
        raise ModuleNotFoundError("No module named 'numpy'", name="numpy")

    monkeypatch.setattr(service.importlib, "import_module", broken)
    with pytest.raises(ModuleNotFoundError):
        check()

    def nameless(name: str):
        raise ModuleNotFoundError("no name")

    monkeypatch.setattr(service.importlib, "import_module", nameless)
    with pytest.raises(ModuleNotFoundError):
        check()


def test_the_checks_count_breaks_and_text_flags_not_notes(crosscheck):
    crosscheck.breaks = 2
    crosscheck.findings = ["flag", "note", "agree", "flag"]
    assert check() == Checked(rollforward_breaks=2, text_flags=2)
    assert crosscheck.calls == [{"user": "quinn", "deal": FIXTURE_DEAL, "period": SEP, "run_id": 7}]
    crosscheck.breaks, crosscheck.findings = 0, []
    assert check() == Checked(rollforward_breaks=0, text_flags=0)


def test_a_refused_check_keeps_the_month_out(crosscheck):
    crosscheck.refuse = Refused("no tape")
    with pytest.raises(MonthNotLoaded) as refused:
        check()
    assert (refused.value.reason, refused.value.detail) == ("checks could not be run", "no tape")


@needs_db
def test_a_break_ends_the_run_with_problems(seeded, crosscheck):
    backfill(in_session, AUG, AUG)
    crosscheck.breaks = 1
    crosscheck.findings = ["flag"]
    clock.set_now(LOAD_DAY)
    report = service.refresh_deal("quinn", FIXTURE_DEAL, sources=sources())
    month = report.run.summary["deals"][0]["months"][0]
    assert (month["period"], month["rollforward_breaks"], month["text_flags"]) == (
        "September 2026",
        1,
        1,
    )
    assert report.run.status == "completed with problems"
    assert routine_report(report.run).splitlines()[0] == (
        "1 roll-forward break, 1 text-versus-tape flag"
    )
    assert crosscheck.calls[-1]["run_id"] == report.run.number


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
