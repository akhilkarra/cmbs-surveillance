"""The real EX-102 files load end to end: every asset stored, every loan typed, none quarantined."""

import datetime as dt
import gzip
from collections import Counter
from decimal import Decimal

import pytest
from sqlalchemy import select

from cmbs.absee.schedule_al import AssetKind, parse_assets
from cmbs.analysis.staleness import file_staleness
from cmbs.tape.service import load_tape, plan, stored_assets
from cmbs.tape.tables import LoanPeriod
from tests.conftest import needs_db
from tests.fixtures.tape import tapes
from tests.fixtures.tape.tapes import REAL_FILES, real_bytes

BNK13_SEPTEMBER = "bank-2018-bnk13_2026-09_ex102.xml.gz"
BNK13_AUGUST = "bank-2018-bnk13_2026-08_ex102.xml.gz"
NEW_ISSUE = "bank5-2026-5yr23_2026-09_ex102.xml.gz"


@pytest.mark.parametrize(
    ("name", "kinds"),
    [
        (BNK13_AUGUST, {"loan": 62, "portfolio property": 21, "companion note": 4}),
        (BNK13_SEPTEMBER, {"loan": 42, "portfolio property": 21, "companion note": 4}),
        (NEW_ISSUE, {"loan": 33}),
    ],
)
def test_every_real_loan_passes_the_contract(name, kinds):
    assets = parse_assets(real_bytes(name))
    assert Counter(asset.kind.value for asset in assets) == kinds
    work = plan(assets)
    assert work.quarantined == []
    assert len(work.loaded) == kinds["loan"]


def test_the_new_issue_nests_portfolio_properties_in_one_asset():
    assets = {asset.asset_number: asset for asset in parse_assets(real_bytes(NEW_ISSUE))}
    assert len(assets["1"].properties) == 90
    assert assets["1"].kind is AssetKind.LOAN
    assert assets["1"].tag("scheduledPrincipalAmount") == ".00000000"


def test_the_september_bnk13_staleness_matches_the_research_notes(tmp_path):
    path = tmp_path / "exh_102.xml"
    path.write_bytes(real_bytes(BNK13_SEPTEMBER))
    result = file_staleness(path, dt.date(2026, 9, 29))
    assert (result.active_loans, result.loans_with_financials, result.over_six_months) == (
        34,
        34,
        3,
    )


def test_a_damaged_fixture_is_caught(tmp_path, monkeypatch):

    monkeypatch.setattr(tapes, "HERE", tmp_path)
    (tmp_path / NEW_ISSUE).write_bytes(gzip.compress(b"<assetData/>"))
    with pytest.raises(AssertionError, match="is not the file filed on EDGAR"):
        real_bytes(NEW_ISSUE)


@needs_db
def test_the_september_bnk13_tape_loads_end_to_end(db):
    real = REAL_FILES[BNK13_SEPTEMBER]
    summary = load_tape(
        db,
        deal="BANK 2018-BNK13",
        content=real_bytes(BNK13_SEPTEMBER),
        file_name="exh_102.xml",
        accession=real.accession,
        filing_date=dt.date(2026, 9, 29),
        cik=real.cik,
    )
    assert (summary.assets, summary.loans_loaded, summary.quarantined) == (67, 42, [])
    assert summary.file_sha256 == real.sha256
    assert len(stored_assets(db, real.accession)) == 67
    ditson = db.scalars(select(LoanPeriod).where(LoanPeriod.loan == "11")).one()
    assert (
        ditson.ending_balance,
        ditson.paid_through_date,
        ditson.payment_status,
        ditson.special_servicer_transfer_date,
        ditson.workout_strategy,
        ditson.financials_end,
        ditson.dscr,
        ditson.occupancy,
    ) == (
        Decimal("37500000.00"),
        dt.date(2026, 3, 1),
        "3",
        dt.date(2026, 6, 4),
        "2",
        dt.date(2026, 3, 31),
        Decimal("0.0348"),
        Decimal("0.28"),
    )
