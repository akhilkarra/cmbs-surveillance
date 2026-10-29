"""Whole-loan links and holdings by deal: pure checks, then linking against the database."""

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import select

from cmbs import clock
from cmbs.access.service import PermissionRefused, UnknownUser, ensure_user
from cmbs.audit.tables import AuditEntry
from cmbs.book.service import (
    NOTHING_HELD,
    Book,
    Held,
    Holding,
    InvalidLink,
    NoteRef,
    clean_notes,
    holding,
    link_problem,
    link_whole_loan,
    whole_loans_of,
)
from cmbs.book.tables import WholeLoan
from cmbs.deals.service import by_name, get_or_create
from tests.conftest import needs_db

F1, F2, F3 = "Fixture Trust 2026-F1", "Fixture Trust 2026-F2", "Fixture Trust 2026-F3"


# ---------------------------------------------------------------------------- holdings


def test_a_book_holding_sums_a_deals_classes_and_lists_them_in_order():
    book = Book(
        file_id=1,
        as_of=dt.date(2026, 9, 30),
        positions=(
            Held(1, F1, "F", Decimal("5000000.00")),
            Held(2, F2, "D", Decimal("40000000.00")),
            Held(1, F1, "E", Decimal("20000000.00")),
        ),
    )
    assert book.holding(1) == Holding(Decimal("25000000.00"), ("E", "F"))
    assert holding(book, 2) == Holding(Decimal("40000000.00"), ("D",))
    assert holding(book, 3) == Holding(Decimal("0.00"), ())
    assert str(holding(book, 3).face) == "0.00"


def test_no_book_holds_nothing():
    assert holding(None, 1) is NOTHING_HELD
    assert Holding(Decimal("0.00"), ()) == NOTHING_HELD


# ---------------------------------------------------------------------------- pure checks


def test_notes_are_cleaned_of_extra_whitespace():
    assert clean_notes([NoteRef(" Fixture  Trust 2026-F1 ", " 1 "), NoteRef(F2, "3")]) == [
        NoteRef(F1, "1"),
        NoteRef(F2, "3"),
    ]


@pytest.mark.parametrize(
    ("notes", "message"),
    [
        ([NoteRef(F1, "1")], "a whole loan links loans of at least two deals"),
        ([], "a whole loan links loans of at least two deals"),
        (
            [NoteRef(F1, "1"), NoteRef(F2, " ")],
            "every note of a whole loan names a deal and a loan",
        ),
        (
            [NoteRef(" ", "1"), NoteRef(F2, "3")],
            "every note of a whole loan names a deal and a loan",
        ),
        (
            [NoteRef(F1, "1"), NoteRef(" Fixture Trust  2026-F1", "2")],
            f"a whole loan has one note in each deal; {F1} is named twice",
        ),
    ],
)
def test_invalid_notes_are_refused(notes, message):
    with pytest.raises(InvalidLink) as refused:
        clean_notes(notes)
    assert str(refused.value) == message


NOTES = [NoteRef(F1, "1"), NoteRef(F2, "3"), NoteRef(F3, "5")]


def _problem(linked, occupied=()) -> str | None:
    found = link_problem(NOTES, linked, occupied)
    return None if found is None else str(found)


def test_unlinked_notes_make_a_new_whole_loan():
    assert _problem([None, None, None]) is None


def test_unlinked_notes_may_join_one_existing_whole_loan():
    assert _problem([4, None, None], occupied={F1}) is None


def test_two_whole_loans_are_never_merged():
    assert _problem([4, None, 7]) == (
        f"loan 1 of {F1} is in whole loan 4 and loan 5 of {F3} in whole loan 7; "
        "whole loans are never merged"
    )
    assert _problem([7, 4, None]).startswith(f"loan 3 of {F2} is in whole loan 4 and loan 1")  # pyright: ignore[reportOptionalMemberAccess]


def test_notes_already_linked_together_are_refused():
    assert _problem([4, 4, 4]) == "these loans are already linked as whole loan 4"


def test_a_whole_loan_never_gets_a_second_note_in_one_deal():
    assert _problem([4, None, None], occupied={F1, F3}) == (
        f"whole loan 4 already has a note in {F3}"
    )


# ---------------------------------------------------------------------------- linking


@needs_db
def test_a_quant_links_a_whole_loan_and_it_is_audited(seeded):
    clock.set_now(dt.datetime(2026, 9, 30, 12, tzinfo=dt.UTC))
    whole = link_whole_loan(
        seeded, user="quinn", notes=[NoteRef(F1, "1"), NoteRef(" Fixture Trust 2026-F2", "3")]
    )
    seeded.commit()
    stored = seeded.get_one(WholeLoan, whole)
    assert (stored.linked_by, stored.linked_at) == (
        "quinn",
        dt.datetime(2026, 9, 30, 12, tzinfo=dt.UTC),
    )
    f1, f2 = by_name(seeded, F1), by_name(seeded, F2)
    assert f1 is not None and f2 is not None
    assert whole_loans_of(seeded, f1.id) == {"1": whole}
    assert whole_loans_of(seeded, f2.id) == {"3": whole}
    entry = seeded.scalars(select(AuditEntry)).one()
    assert (entry.user_name, entry.role, entry.action, entry.subject, entry.subject_key) == (
        "quinn",
        "quant",
        "link whole loan",
        "whole loan",
        str(whole),
    )
    assert entry.after == f"loan 1 of {F1}; loan 3 of {F2}"
    assert entry.detail == {"notes": [{"deal": F1, "loan": "1"}, {"deal": F2, "loan": "3"}]}


@needs_db
def test_a_third_deal_joins_an_existing_whole_loan(seeded):
    whole = link_whole_loan(seeded, user="quinn", notes=[NoteRef(F1, "1"), NoteRef(F2, "3")])
    joined = link_whole_loan(seeded, user="morgan", notes=[NoteRef(F2, "3"), NoteRef(F3, "5")])
    assert joined == whole
    f3 = by_name(seeded, F3)
    assert f3 is not None and whole_loans_of(seeded, f3.id) == {"5": whole}
    assert seeded.scalars(select(WholeLoan)).all() == [seeded.get(WholeLoan, whole)]


@needs_db
def test_only_a_quant_links_whole_loans_and_refusals_are_audited(seeded):
    notes = [NoteRef(F1, "1"), NoteRef(F2, "3")]
    with pytest.raises(PermissionRefused, match="^pm may not link whole loans$"):
        link_whole_loan(seeded, user="pat", notes=notes)
    with pytest.raises(PermissionRefused, match="^operator may not link whole loans$"):
        link_whole_loan(seeded, user="olly", notes=notes)
    with pytest.raises(UnknownUser, match="^no user named 'bob'$"):
        link_whole_loan(seeded, user="bob", notes=notes)
    seeded.rollback()
    refusals = seeded.scalars(select(AuditEntry).order_by(AuditEntry.id)).all()
    assert [(e.user_name, e.action, e.outcome, e.reason) for e in refusals] == [
        ("pat", "link whole loan", "refused", "pm may not link whole loans"),
        ("olly", "link whole loan", "refused", "operator may not link whole loans"),
        ("bob", "link whole loan", "refused", "no user named 'bob'"),
    ]
    assert seeded.scalars(select(WholeLoan)).all() == []


@needs_db
def test_a_user_without_roles_may_not_link(seeded):
    ensure_user(seeded, "nobody", set())
    with pytest.raises(PermissionRefused, match="^a user without roles may not link whole loans$"):
        link_whole_loan(seeded, user="nobody", notes=[NoteRef(F1, "1"), NoteRef(F2, "3")])
    with pytest.raises(PermissionRefused, match="^analyst may not link whole loans$"):
        link_whole_loan(seeded, user="alex", notes=[NoteRef(F1, "1"), NoteRef(F2, "3")])


@needs_db
def test_an_impossible_link_is_refused_audited_and_changes_nothing(seeded):
    get_or_create(seeded, F1)
    first = link_whole_loan(seeded, user="quinn", notes=[NoteRef(F1, "1"), NoteRef(F2, "3")])
    second = link_whole_loan(seeded, user="quinn", notes=[NoteRef(F1, "2"), NoteRef(F3, "5")])
    seeded.commit()
    with pytest.raises(InvalidLink, match="whole loans are never merged$"):
        link_whole_loan(seeded, user="quinn", notes=[NoteRef(F1, "1"), NoteRef(F3, "5")])
    seeded.rollback()
    with pytest.raises(InvalidLink, match="^a whole loan links loans of at least two deals$"):
        link_whole_loan(seeded, user="quinn", notes=[NoteRef(F1, "1")])
    seeded.rollback()
    refused = seeded.scalars(
        select(AuditEntry).where(AuditEntry.outcome == "refused").order_by(AuditEntry.id)
    ).all()
    assert [(e.user_name, e.role, e.action, e.subject, e.reason) for e in refused] == [
        (
            "quinn",
            "quant",
            "link whole loan",
            "whole loan",
            f"loan 1 of {F1} is in whole loan {first} and loan 5 of {F3} in whole loan "
            f"{second}; whole loans are never merged",
        ),
        (
            "quinn",
            "quant",
            "link whole loan",
            "whole loan",
            "a whole loan links loans of at least two deals",
        ),
    ]
    f1 = by_name(seeded, F1)
    assert f1 is not None and whole_loans_of(seeded, f1.id) == {"1": first, "2": second}


@needs_db
def test_a_deal_may_not_hold_two_notes_of_one_whole_loan(seeded):
    whole = link_whole_loan(seeded, user="quinn", notes=[NoteRef(F1, "1"), NoteRef(F2, "3")])
    with pytest.raises(InvalidLink, match=f"^whole loan {whole} already has a note in {F1}$"):
        link_whole_loan(seeded, user="quinn", notes=[NoteRef(F2, "3"), NoteRef(F1, "9")])
