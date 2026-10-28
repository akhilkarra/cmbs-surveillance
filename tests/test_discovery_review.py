"""Discovery defects found in review: names, depositor filings, transactions, future dates."""

import datetime as dt
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.pool import NullPool

from cmbs import clock
from cmbs.access.roles import Role
from cmbs.access.service import Actor, PermissionRefused
from cmbs.book.service import (
    InvalidPositions,
    PositionLine,
    book_as_of,
    load_positions,
    store_positions,
)
from cmbs.coverage.service import configure_vendor_source, vendor_sources_of
from cmbs.db import engine
from cmbs.deals.service import ConflictingDeal, get_or_create
from cmbs.deals.tables import Deal
from cmbs.edgar import search
from cmbs.edgar.replay import Replay
from cmbs.edgar.service import (
    DiscoveryInFuture,
    SeparateDeals,
    deal_for,
    deal_named,
    discover_deal,
    filings_of,
    latest_discovery,
)
from cmbs.edgar.tables import DealAlias
from cmbs.filings.tables import Filing
from tests.conftest import needs_db

pytestmark = needs_db

RECORDINGS = Path(__file__).parent / "fixtures" / "edgar"
ON = dt.date(2026, 10, 27)
COMM_CIK = 1610486


def comm_deals(db) -> list[Deal]:
    return list(db.scalars(select(Deal).where(Deal.cik == COMM_CIK)))


@pytest.mark.parametrize(
    "names", [("COMM 2014-CR18", "COMM 2014-CCRE18"), ("COMM 2014-CCRE18", "COMM 2014-CR18")]
)
def test_both_names_of_one_trust_reach_one_deal(seeded, names):
    first, second = names
    discover_deal(Replay(RECORDINGS), first, ON, user="alex")
    discover_deal(Replay(RECORDINGS), second, ON, user="alex")
    with engine.session() as db:
        (deal,) = comm_deals(db)
        assert deal.name == first
        assert deal_named(db, second) == deal
        assert deal_named(db, first) == deal
        aliases = db.scalars(select(DealAlias)).all()
        assert [(a.name, a.deal_id) for a in aliases] == [(second, deal.id)]
        suspension = db.get_one(Filing, "0001888524-24-014734")
        assert suspension.deal_id == deal.id
        assert latest_discovery(db, deal.id) is not None


class TwoPrivateDeals:
    """Two privately placed deals named in the same depositor ABS-15G."""

    def get_json(self, url: str) -> Any:
        if "keysTyped" in url:
            return {"hits": {"total": {"value": 0}, "hits": []}}
        assert url in (
            search.full_text_url('"AAA 2024-ONE"', "ABS-15G"),
            search.full_text_url('"BBB 2024-TWO"', "ABS-15G"),
        ), url
        hit = {
            "_id": "0000000001-24-000001:exh1.htm",
            "_source": {
                "adsh": "0000000001-24-000001",
                "form": "ABS-15G",
                "file_date": "2024-08-06",
                "ciks": ["0000850779"],
            },
        }
        return {"hits": {"total": {"value": 1}, "hits": [hit]}}


def test_one_depositor_filing_is_linked_to_every_deal_it_names(seeded):
    discover_deal(TwoPrivateDeals(), "AAA 2024-ONE", ON, user="alex")
    discover_deal(TwoPrivateDeals(), "BBB 2024-TWO", ON, user="alex")
    with engine.session() as db:
        filing = db.get_one(Filing, "0000000001-24-000001")
        assert filing.deal_id is None
        for name in ("AAA 2024-ONE", "BBB 2024-TWO"):
            deal = deal_named(db, name)
            assert deal is not None and deal.cik is None
            assert [f.accession for f in filings_of(db, deal.id, ON)] == ["0000000001-24-000001"]


class AssertNoTransaction:
    """A source that checks, on every request, that no database transaction is open."""

    def __init__(self, upstream: Any, url: str) -> None:
        self.upstream = upstream
        self.checker = create_engine(url, poolclass=NullPool)
        self.requests = 0

    def get_json(self, url: str) -> Any:
        with self.checker.connect() as conn:
            open_transactions = conn.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                    "AND pid <> pg_backend_pid() AND xact_start IS NOT NULL"
                )
            ).scalar_one()
        assert open_transactions == 0, f"{open_transactions} transactions open while fetching"
        self.requests += 1
        return self.upstream.get_json(url)


def test_no_transaction_is_open_while_edgar_is_fetched(seeded, db_url):
    source = AssertNoTransaction(Replay(RECORDINGS), db_url)
    discover_deal(source, "COMM 2014-CR18", ON, user="alex")
    assert source.requests == 3
    source.checker.dispose()


def test_roles_are_checked_again_after_the_fetch(seeded):
    class RevokeDuringFetch:
        def get_json(self, url: str) -> Any:
            with engine.session() as db:
                db.execute(text("DELETE FROM app.user_role WHERE user_name = 'alex'"))
            return Replay(RECORDINGS).get_json(url)

    with pytest.raises(PermissionRefused, match="^a user without roles may not read the panel$"):
        discover_deal(RevokeDuringFetch(), "BANK 2018-BNK13", ON, user="alex")
    with engine.session() as db:
        assert deal_named(db, "BANK 2018-BNK13") is None


def test_a_discovery_dated_after_today_is_refused(seeded):
    clock.set_now(dt.datetime(2026, 10, 28, 9, tzinfo=dt.UTC))
    with pytest.raises(DiscoveryInFuture) as refused:
        discover_deal(Replay(RECORDINGS), "BANK 2018-BNK13", dt.date(2026, 10, 29), user="alex")
    assert str(refused.value) == (
        "cannot look up filings as of 2026-10-29, which is after today (2026-10-28)"
    )
    discover_deal(Replay(RECORDINGS), "BANK 2018-BNK13", dt.date(2026, 10, 28), user="alex")


def test_the_latest_discovery_is_by_date_not_by_when_it_was_stored(seeded):
    discover_deal(Replay(RECORDINGS), "WFRBS 2014-C25", ON, user="alex")
    discover_deal(Replay(RECORDINGS), "WFRBS 2014-C25", dt.date(2026, 9, 1), user="alex")
    with engine.session() as db:
        deal = deal_named(db, "WFRBS 2014-C25")
        assert deal is not None
        latest = latest_discovery(db, deal.id)
        assert latest is not None and latest.as_of == ON
        bounded = latest_discovery(db, deal.id, dt.date(2026, 10, 1))
        assert bounded is not None and bounded.as_of == dt.date(2026, 9, 1)


def test_a_name_already_registered_apart_from_its_trust_is_refused(seeded):
    get_or_create(seeded, "COMM 2014-CCRE18")
    seeded.commit()
    discover_deal(Replay(RECORDINGS), "COMM 2014-CR18", ON, user="alex")
    with pytest.raises(SeparateDeals) as refused:
        discover_deal(Replay(RECORDINGS), "COMM 2014-CCRE18", ON, user="alex")
    assert str(refused.value) == (
        '"COMM 2014-CCRE18" is registered apart from "COMM 2014-CR18", which already has '
        "CIK 1610486; they are the same trust"
    )


def test_positions_and_vendor_sources_under_an_alias_reach_the_trust(seeded):
    discover_deal(Replay(RECORDINGS), "COMM 2014-CR18", ON, user="alex")
    discover_deal(Replay(RECORDINGS), "COMM 2014-CCRE18", ON, user="alex")
    lines = [PositionLine("COMM 2014-CCRE18", "D", Decimal("2000000.00"))]
    load_positions(seeded, user="olly", as_of=dt.date(2026, 9, 30), lines=lines)
    configure_vendor_source(seeded, user="olly", deal=" COMM  2014-CCRE18", source="feed")
    book = book_as_of(seeded, ON)
    assert book is not None
    assert [(h.deal, h.tranche) for h in book.positions] == [("COMM 2014-CR18", "D")]
    deal = deal_named(seeded, "COMM 2014-CR18")
    assert deal is not None
    assert vendor_sources_of(seeded, deal.id, dt.date(2100, 1, 1)) == ["feed"]


def test_one_class_listed_under_two_names_of_a_trust_is_refused(seeded):
    discover_deal(Replay(RECORDINGS), "COMM 2014-CR18", ON, user="alex")
    discover_deal(Replay(RECORDINGS), "COMM 2014-CCRE18", ON, user="alex")
    lines = [
        PositionLine("COMM 2014-CCRE18", "D", Decimal("1.00")),
        PositionLine("COMM 2014-CR18", "D", Decimal("1.00")),
    ]
    with pytest.raises(InvalidPositions) as refused:
        load_positions(seeded, user="olly", as_of=dt.date(2026, 9, 30), lines=lines)
    assert str(refused.value) == (
        "Class D of COMM 2014-CCRE18 is listed twice, also as COMM 2014-CR18"
    )


def test_an_alias_learns_or_refuses_a_cik(seeded):
    known = get_or_create(seeded, "TRUST A 2026-1", cik=5)
    blank = get_or_create(seeded, "TRUST B 2026-1")
    seeded.add_all([DealAlias(name="A 2026-1", deal_id=known.id)])
    seeded.add_all([DealAlias(name="B 2026-1", deal_id=blank.id)])
    seeded.flush()
    assert deal_for(seeded, "A 2026-1", cik=5) == known
    with pytest.raises(ConflictingDeal, match="^TRUST A 2026-1 already has CIK 5, not 6$"):
        deal_for(seeded, "A 2026-1", cik=6)
    assert deal_for(seeded, " B  2026-1", cik=7).cik == 7
    assert deal_for(seeded, "B 2026-1") == blank


def test_storing_positions_needs_an_actor_who_may_load_them(seeded):
    with pytest.raises(ValueError, match="^an actor acting as pm cannot load positions$"):
        store_positions(seeded, actor=Actor("pat", Role.PM), as_of=dt.date(2026, 9, 30), lines=[])
