"""Discovery registers deals, filings and a log entry. Replays tests/fixtures/edgar."""

import datetime as dt
from pathlib import Path

import pytest
from sqlalchemy import select

from cmbs import clock
from cmbs.access.service import PermissionRefused
from cmbs.audit.tables import AuditEntry
from cmbs.deals.service import by_name, get_or_create
from cmbs.edgar.replay import MissingRecording, Replay
from cmbs.edgar.service import discover_deal, filings_of, latest_discovery
from cmbs.edgar.tables import FilingDiscovery
from cmbs.filings.tables import Filing
from tests.conftest import needs_db

pytestmark = needs_db

RECORDINGS = Path(__file__).parent / "fixtures" / "edgar"
ON = dt.date(2026, 10, 27)


@pytest.fixture
def edgar() -> Replay:
    return Replay(RECORDINGS)


def test_a_conduit_is_registered_with_its_cik_filings_and_periods(seeded, edgar):
    clock.set_now(dt.datetime(2026, 10, 28, 8, tzinfo=dt.UTC))
    discover_deal(edgar, "BANK 2018-BNK13", ON, user="alex")
    deal = by_name(seeded, "BANK 2018-BNK13")
    assert deal is not None
    assert (deal.cik, deal.edgar_name) == (1744707, "BANK 2018-BNK13")
    tape = seeded.get_one(Filing, "0001888524-26-017705")
    assert (
        tape.cik,
        tape.deal_id,
        tape.form,
        tape.filing_date,
        tape.report_date,
        tape.period,
        tape.primary_document,
    ) == (
        1744707,
        deal.id,
        "ABS-EE",
        dt.date(2026, 9, 29),
        dt.date(2026, 9, 17),
        dt.date(2026, 9, 1),
        "wcm18b13_absee-202609.htm",
    )
    prospectus = seeded.get_one(Filing, "0001539497-18-001270")
    assert (prospectus.form, prospectus.report_date, prospectus.period) == ("424B2", None, None)
    assert len(filings_of(seeded, deal.id, ON)) == 231
    log = seeded.scalars(select(FilingDiscovery)).one()
    assert (log.deal_id, log.as_of, log.checked_by, log.cik, log.filings, log.note) == (
        deal.id,
        ON,
        "alex",
        1744707,
        231,
        None,
    )
    assert log.checked_at == dt.datetime(2026, 10, 28, 8, tzinfo=dt.UTC)
    entry = seeded.scalars(select(AuditEntry)).one()
    assert (entry.user_name, entry.role, entry.action, entry.subject_key, entry.after) == (
        "alex",
        "analyst",
        "discover filings",
        "BANK 2018-BNK13",
        "CIK 1744707, 231 filings",
    )
    assert entry.detail == {
        "on": "2026-10-27",
        "name": "BANK 2018-BNK13",
        "cik": 1744707,
        "edgar_name": "BANK 2018-BNK13",
        "note": None,
        "filings": 231,
    }


def test_filings_after_the_discovery_date_are_not_registered(seeded, edgar):
    discover_deal(edgar, "BANK 2018-BNK13", dt.date(2026, 10, 26), user="alex")
    assert seeded.get(Filing, "0001888524-26-019175") is None
    assert seeded.get(Filing, "0001888524-26-017705") is not None


def test_a_renamed_trust_keeps_its_edgar_name_and_note(seeded, edgar):
    found = discover_deal(edgar, "COMM 2014-CR18", ON, user="pat")
    deal = by_name(seeded, "COMM 2014-CR18")
    assert deal is not None
    assert (deal.cik, deal.edgar_name) == (1610486, "COMM 2014-CCRE18 Mortgage Trust")
    log = latest_discovery(seeded, deal.id, ON)
    assert log is not None and log.note == found.match.note
    assert log.note is not None and log.note.startswith('EDGAR names the trust "COMM 2014-CCRE18"')


def test_a_private_deal_is_registered_without_a_cik_and_with_the_depositor_filing(seeded, edgar):
    discover_deal(edgar, "HYT 2024-RGCY", ON, user="alex")
    deal = by_name(seeded, "HYT 2024-RGCY")
    assert deal is not None and deal.cik is None and deal.edgar_name is None
    (filing,) = filings_of(seeded, deal.id, ON)
    assert (filing.accession, filing.cik, filing.form, filing.filing_date) == (
        "0001539497-24-001576",
        850779,
        "ABS-15G",
        dt.date(2024, 8, 6),
    )
    entry = seeded.scalars(select(AuditEntry)).one()
    assert entry.after == "no issuing entity, 1 filings"


def test_discovering_again_is_idempotent_and_logged_again(seeded, edgar):
    discover_deal(edgar, "WFRBS 2014-C25", dt.date(2026, 9, 1), user="alex")
    discover_deal(edgar, "WFRBS 2014-C25", ON, user="quinn")
    deal = by_name(seeded, "WFRBS 2014-C25")
    assert deal is not None
    assert len(filings_of(seeded, deal.id, ON)) == 171
    assert len(filings_of(seeded, deal.id, dt.date(2026, 9, 1))) == 170
    logs = seeded.scalars(select(FilingDiscovery).order_by(FilingDiscovery.id)).all()
    assert [(log.as_of, log.filings) for log in logs] == [(dt.date(2026, 9, 1), 170), (ON, 171)]
    first = latest_discovery(seeded, deal.id, dt.date(2026, 10, 1))
    assert first is not None and first.as_of == dt.date(2026, 9, 1)
    latest = latest_discovery(seeded, deal.id, dt.date(2026, 12, 1))
    assert latest is not None and latest.id == logs[1].id
    assert latest_discovery(seeded, deal.id, dt.date(2026, 8, 31)) is None


def test_of_two_discoveries_on_one_date_the_later_is_latest(seeded, edgar):
    discover_deal(edgar, "WFRBS 2014-C25", ON, user="alex")
    discover_deal(edgar, "WFRBS 2014-C25", ON, user="quinn")
    deal = by_name(seeded, "WFRBS 2014-C25")
    assert deal is not None
    latest = latest_discovery(seeded, deal.id, ON)
    assert latest is not None and latest.checked_by == "quinn"


def test_a_deal_known_before_learns_its_cik(seeded, edgar):
    get_or_create(seeded, "BANK 2018-BNK13")
    seeded.commit()
    discover_deal(edgar, "BANK 2018-BNK13", ON, user="alex")
    deal = by_name(seeded, "BANK 2018-BNK13")
    assert deal is not None and deal.cik == 1744707


def test_users_who_cannot_read_the_panel_cannot_look_deals_up(seeded, edgar):
    with pytest.raises(PermissionRefused, match="^operator may not read the panel$"):
        discover_deal(edgar, "BANK 2018-BNK13", ON, user="olly")


def test_a_failed_lookup_writes_nothing(seeded):
    with pytest.raises(MissingRecording):
        discover_deal(Replay(RECORDINGS), "KSL 2026-HT3", ON, user="alex")
    assert by_name(seeded, "KSL 2026-HT3") is None
    assert seeded.scalars(select(FilingDiscovery)).all() == []
