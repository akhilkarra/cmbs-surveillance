"""Steps for features/data/filing_discovery.feature.

EDGAR answers are replayed from tests/fixtures/edgar (recorded from the live hosts); scenarios
never call the network. Checks read the registries, so they prove what discovery stored.
"""

import datetime as dt
from pathlib import Path

from behave import given, then, when  # pyright: ignore[reportAttributeAccessIssue]
from sqlalchemy import select

from cmbs import clock
from cmbs.coverage.rules import ASSET_LEVEL_COMPLIANCE
from cmbs.coverage.service import classify_deal
from cmbs.domain import Period
from cmbs.edgar.replay import Replay
from cmbs.edgar.service import deal_named, discover_deal, filings_of
from cmbs.filings.tables import Filing
from cmbs.testing.bdd import attempt, in_session

RECORDINGS = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "edgar"
ANALYST = "alex"


def _deal(db, name):
    deal = deal_named(db, name)
    assert deal is not None, f"no deal named {name!r} was registered"
    return deal


@given('the analyst looks up the deal "{deal}"')
def step_analyst_looks_up(context, deal):
    context.deal = deal
    context.edgar = Replay(RECORDINGS)


@when("filing discovery runs on {day}")
def step_discovery_runs(context, day):
    on = dt.date.fromisoformat(day)
    clock.set_now(on)  # discovery "runs on" this day: it is today
    context.discovery_on = on
    context.discovery = attempt(
        context, lambda: discover_deal(context.edgar, context.deal, on, user=ANALYST)
    )
    assert context.refusal is None, f"discovery was refused: {context.refusal}"


@then("the deal resolves to issuing entity CIK {cik:d}")
def step_resolves_to_cik(context, cik):
    assert context.discovery.match.cik == cik, context.discovery.match
    stored = in_session(lambda db: _deal(db, context.deal).cik)
    assert stored == cik, f"the deal registry holds CIK {stored}"


def _has_filing(context, period, form, accession, day):
    wanted = Period.parse(period).first_day

    def read(db):
        deal = _deal(db, context.deal)
        return db.scalars(
            select(Filing).where(
                Filing.deal_id == deal.id, Filing.form == form, Filing.period == wanted
            )
        ).all()

    found = [(f.accession, f.filing_date.isoformat()) for f in in_session(read)]
    assert (accession, day) in found, f"{period} {form} filings registered: {found}"


@then("{period} has a {form} with accession {accession} filed on {day}")
def step_has_a_filing(context, period, form, accession, day):
    _has_filing(context, period, form, accession, day)


@then("{period} has an {form} with accession {accession} filed on {day}")
def step_has_an_filing(context, period, form, accession, day):
    _has_filing(context, period, form, accession, day)


def _coverage(context):
    return in_session(lambda db: classify_deal(db, _deal(db, context.deal), context.discovery_on))


def _reason_from_filings(context) -> str:
    """The reason the stored filings give for the deal's class, worked out independently."""

    def read(db):
        deal = _deal(db, context.deal)
        return deal.cik, list(filings_of(db, deal.id, context.discovery_on))

    cik, filings = in_session(read)
    forms = {f.form for f in filings}
    if cik is None:
        dates = sorted(f.filing_date.isoformat() for f in filings if f.form == "ABS-15G")
        assert len(dates) == 1, f"depositor filings: {dates}"
        return f"only the depositor's ABS-15G of {dates[0]}"
    if "10-D" in forms and "ABS-EE" not in forms:
        offered = min(f.filing_date for f in filings if f.form.startswith("424"))
        assert offered < ASSET_LEVEL_COMPLIANCE, f"offered {offered}"
        return "issued before November 2016, no loan tape"
    assert {"10-D", "ABS-EE"} <= forms, forms
    return "loan tape and distribution reports monthly"


@then('the deal is marked "{coverage}"')
def step_deal_marked(context, coverage):
    classified = _coverage(context)
    assert classified.coverage.value == coverage, classified
    assert classified.detail == _reason_from_filings(context), classified.detail


@then('the deal is marked "{coverage}" because it filed a Form {form}')
def step_deal_marked_because(context, coverage, form):
    classified = _coverage(context)
    assert classified.coverage.value == coverage, classified
    assert classified.detail.startswith(f"Form {form},"), classified.detail


@then("no ABS-EE periods are listed")
def step_no_abs_ee(context):
    def read(db):
        return [
            f.accession
            for f in filings_of(db, _deal(db, context.deal).id, context.discovery_on)
            if f.form.startswith("ABS-EE")
        ]

    found = in_session(read)
    assert not found, f"ABS-EE filings registered: {found}"
    assert not context.discovery.of_form("ABS-EE")


@then('the match notes that EDGAR names the trust "{edgar_name}"')
def step_match_notes(context, edgar_name):
    note = context.discovery.match.note
    assert note is not None and f'EDGAR names the trust "{edgar_name}"' in note, note
    stored = in_session(lambda db: _deal(db, context.deal).edgar_name)
    assert stored is not None and edgar_name in stored, f"stored EDGAR name {stored!r}"


@then("its last 10-D was filed in {period}")
def step_last_ten_d(context, period):
    def read(db):
        return [
            f.filing_date
            for f in filings_of(db, _deal(db, context.deal).id, context.discovery_on)
            if f.form == "10-D"
        ]

    dates = in_session(read)
    assert dates, "no 10-D registered"
    assert Period.of(max(dates)) == Period.parse(period), f"last 10-D filed {max(dates)}"


@then("no issuing entity CIK is found")
def step_no_cik(context):
    assert context.discovery.match.cik is None, context.discovery.match
    stored = in_session(lambda db: _deal(db, context.deal).cik)
    assert stored is None, f"the deal registry holds CIK {stored}"


@then("the only filing listed is the depositor's {form} filed on {day}")
def step_only_depositor_filing(context, form, day):
    def read(db):
        deal = _deal(db, context.deal)
        return [
            (f.form, f.filing_date.isoformat(), f.cik) for f in filings_of(db, deal.id, dt.date.max)
        ]

    found = in_session(read)
    assert len(found) == 1, f"filings registered: {found}"
    (registered_form, filed, filer) = found[0]
    assert (registered_form, filed) == (form, day), found
    # The depositor files it under its own CIK; the deal has none.
    assert filer is not None and context.discovery.match.cik is None
