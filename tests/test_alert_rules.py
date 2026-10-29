"""The alert rules and code lists: pure functions, exact findings and messages."""

import datetime as dt
from decimal import Decimal

import pytest

from cmbs.alerts import codes
from cmbs.alerts.rules import (
    SEVERITY_ORDER,
    Comments,
    Context,
    Finding,
    LoanMonth,
    Month,
    Rule,
    Severity,
    added_sentences,
    comment,
    compare,
    count_loans,
    dscr,
    format_dscr,
    format_threshold,
    missing,
    name_loans,
    new_loans,
    paid_through,
    payment_status,
    sentences,
    total_balance,
    transfer,
    unreported_balances,
    workout,
)
from cmbs.domain import Period

SEPTEMBER = Period(2026, 9)
AUGUST = Period(2026, 8)
OLD, NEW = "0009900001-26-000801", "0009900001-26-000901"
REPORT = "0009900001-26-000902"


def row(accession: str = NEW, **values) -> LoanMonth:
    return LoanMonth(loan=values.pop("loan", "7"), accession=accession, **values)


def before(**values) -> LoanMonth:
    return row(OLD, **values)


def after(**values) -> LoanMonth:
    return row(NEW, **values)


def finding(rule: Rule, severity: Severity, title: str, message: str, **rest) -> Finding:
    return Finding(
        loan=rest.pop("loan", "7"),
        rule=rule,
        severity=severity,
        title=title,
        message=message,
        accession=rest.pop("accession", NEW),
        **rest,
    )


# ---------------------------------------------------------------------------- code lists


def test_payment_status_names_and_ranks_follow_schedule_al():
    names = {code: entry.name for code, entry in codes.PAYMENT_STATUS.items()}
    assert names == {
        "0": "current",
        "A": "in grace period",
        "B": "late, under 30 days",
        "1": "30 days",
        "2": "60 days",
        "3": "90+ days",
        "4": "performing matured balloon",
        "5": "non-performing matured balloon",
    }
    ranks = {code: entry.rank for code, entry in codes.PAYMENT_STATUS.items()}
    assert ranks == {"0": 0, "A": 1, "B": 2, "1": 3, "2": 4, "3": 5, "4": 3, "5": 6}


def test_workout_strategy_names_follow_schedule_al():
    names = {code: entry.name for code, entry in codes.WORKOUT_STRATEGY.items()}
    assert names == {
        "1": "modification",
        "2": "foreclosure",
        "3": "bankruptcy",
        "4": "extension",
        "5": "note sale",
        "6": "discounted payoff",
        "7": "REO",
        "8": "resolved",
        "9": "pending return to master servicer",
        "10": "deed in lieu of foreclosure",
        "11": "full payoff",
        "12": "reps and warranties",
        "13": "to be determined",
        "98": "other",
    }


@pytest.mark.parametrize(
    ("code", "name"),
    [
        (None, "not reported"),
        ("0", "current"),
        (" 01 ", "30 days"),
        ("00", "current"),
        ("b", "late, under 30 days"),
        ("9", "code 9"),
        (" X ", "code X"),
    ],
)
def test_codes_are_named_whatever_their_spelling(code, name):
    assert codes.name_of(codes.PAYMENT_STATUS, code) == name


def test_lookup_finds_listed_codes_only():
    assert codes.lookup(codes.WORKOUT_STRATEGY, "013") == codes.Code("to be determined")
    assert codes.lookup(codes.WORKOUT_STRATEGY, "14") is None
    assert codes.lookup(codes.WORKOUT_STRATEGY, None) is None


@pytest.mark.parametrize(
    ("first", "second", "same"),
    [
        (None, None, True),
        (None, "0", False),
        ("0", None, False),
        ("01", "1", True),
        ("b", "B", True),
        ("1", "2", False),
    ],
)
def test_same_code_reads_spellings_alike(first, second, same):
    assert codes.same_code(first, second) is same


# ---------------------------------------------------------------------------- formatting


def test_dscr_is_shown_to_two_places_rounding_half_up():
    assert format_dscr(Decimal("0.955")) == "0.96x"
    assert format_dscr(Decimal("1.1")) == "1.10x"
    assert format_dscr(Decimal("0.954")) == "0.95x"


def test_the_threshold_is_shown_as_the_settings_ledger_shows_it():
    assert format_threshold(Decimal("1.00")) == "1.00x"
    assert format_threshold(Decimal("1.125")) == "1.125x"


def test_severities_order_high_first():
    assert sorted(Severity, key=SEVERITY_ORDER.__getitem__) == [
        Severity.HIGH,
        Severity.MEDIUM,
        Severity.LOW,
    ]


# ---------------------------------------------------------------------------- transfer


def test_a_first_transfer_date_is_a_high_alert():
    found = transfer(before(), after(transfer_date=dt.date(2026, 9, 2)))
    assert found == finding(
        Rule.TRANSFER,
        Severity.HIGH,
        "transferred to special servicing",
        "transferred to special servicing on 2026-09-02",
    )


def test_a_later_transfer_date_is_a_new_transfer():
    found = transfer(
        before(transfer_date=dt.date(2025, 3, 6)), after(transfer_date=dt.date(2026, 9, 2))
    )
    assert found is not None and found.message.endswith("on 2026-09-02")


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (dt.date(2025, 3, 6), dt.date(2025, 3, 6)),
        (dt.date(2025, 3, 6), dt.date(2025, 3, 5)),
        (dt.date(2025, 3, 6), None),
        (None, None),
    ],
)
def test_an_unchanged_earlier_or_missing_transfer_date_is_no_alert(old, new):
    assert transfer(before(transfer_date=old), after(transfer_date=new)) is None


# ---------------------------------------------------------------------------- payment status


def test_a_worse_payment_status_is_a_medium_alert_with_readable_names():
    found = payment_status(before(payment_status="0"), after(payment_status="1"))
    assert found == finding(
        Rule.PAYMENT_WORSENED,
        Severity.MEDIUM,
        "payment status worsened",
        "payment status worsened: current to 30 days",
    )


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("0", "A", "payment status worsened: current to in grace period"),
        ("A", "B", "payment status worsened: in grace period to late, under 30 days"),
        ("B", "1", "payment status worsened: late, under 30 days to 30 days"),
        ("2", "3", "payment status worsened: 60 days to 90+ days"),
        ("0", "4", "payment status worsened: current to performing matured balloon"),
        ("3", "5", "payment status worsened: 90+ days to non-performing matured balloon"),
        ("4", "2", "payment status worsened: performing matured balloon to 60 days"),
    ],
)
def test_each_step_further_behind_is_worse(old, new, message):
    found = payment_status(before(payment_status=old), after(payment_status=new))
    assert found is not None and found.message == message


@pytest.mark.parametrize(
    ("old", "new"),
    [("1", "0"), ("3", "4"), ("5", "3"), ("1", "01"), (None, "1"), ("1", None)],
)
def test_a_better_same_or_unreported_status_is_no_alert(old, new):
    assert payment_status(before(payment_status=old), after(payment_status=new)) is None


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("1", "4", "payment status changed: 30 days to performing matured balloon"),
        ("4", "1", "payment status changed: performing matured balloon to 30 days"),
    ],
)
def test_a_change_between_codes_of_equal_rank_is_still_reported(old, new, message):
    found = payment_status(before(payment_status=old), after(payment_status=new))
    assert found == finding(
        Rule.PAYMENT_CHANGED, Severity.MEDIUM, "payment status changed", message
    )


def test_a_change_to_or_from_an_unlisted_code_is_reported_as_a_change():
    found = payment_status(before(payment_status="0"), after(payment_status="9"))
    assert found == finding(
        Rule.PAYMENT_CHANGED,
        Severity.MEDIUM,
        "payment status changed",
        "payment status changed: current to code 9",
    )
    back = payment_status(before(payment_status="9"), after(payment_status="0"))
    assert back is not None and back.message == "payment status changed: code 9 to current"


# ---------------------------------------------------------------------------- paid-through


def test_a_paid_through_date_that_stayed_behind_is_a_medium_alert():
    day = dt.date(2026, 8, 1)
    found = paid_through(before(paid_through=day), after(paid_through=day), SEPTEMBER)
    assert found == finding(
        Rule.PAID_THROUGH,
        Severity.MEDIUM,
        "paid-through date did not advance",
        "paid-through date did not advance: still 2026-08-01",
    )


def test_a_paid_through_date_that_went_back_says_so():
    found = paid_through(
        before(paid_through=dt.date(2026, 8, 1)),
        after(paid_through=dt.date(2026, 7, 1), ending_balance=Decimal("9000000.00")),
        SEPTEMBER,
    )
    assert found is not None
    assert found.message == "paid-through date did not advance: 2026-08-01 to 2026-07-01"


@pytest.mark.parametrize(
    ("old", "new", "balance"),
    [
        (dt.date(2026, 8, 1), dt.date(2026, 9, 1), None),  # advanced
        (dt.date(2026, 9, 1), dt.date(2026, 9, 1), None),  # stayed, but paid for the month
        (dt.date(2026, 10, 1), dt.date(2026, 10, 1), None),  # paid ahead
        (dt.date(2026, 8, 1), dt.date(2026, 8, 1), Decimal("0.00")),  # paid off
        (None, dt.date(2026, 8, 1), None),
        (dt.date(2026, 8, 1), None, None),
    ],
)
def test_no_paid_through_alert_when_the_loan_is_not_behind(old, new, balance):
    assert (
        paid_through(
            before(paid_through=old), after(paid_through=new, ending_balance=balance), SEPTEMBER
        )
        is None
    )


def test_a_loan_with_a_balance_and_a_stale_paid_through_date_is_behind():
    day = dt.date(2026, 8, 31)
    found = paid_through(
        before(paid_through=day), after(paid_through=day, ending_balance=Decimal("0.01")), SEPTEMBER
    )
    assert found is not None and found.message.endswith("still 2026-08-31")


# ---------------------------------------------------------------------------- DSCR


THRESHOLD = Decimal("1.00")


def test_a_dscr_falling_below_the_threshold_is_a_medium_alert():
    found = dscr(before(dscr=Decimal("1.12")), after(dscr=Decimal("0.96")), THRESHOLD)
    assert found == finding(
        Rule.DSCR,
        Severity.MEDIUM,
        "DSCR fell below 1.00x",
        "DSCR fell below 1.00x: 1.12x to 0.96x",
    )


def test_a_dscr_exactly_at_the_threshold_last_month_counts_as_above_it():
    found = dscr(before(dscr=Decimal("1.00")), after(dscr=Decimal("0.99")), THRESHOLD)
    assert found is not None and found.message == "DSCR fell below 1.00x: 1.00x to 0.99x"


def test_a_dscr_that_rounds_up_to_the_threshold_shows_every_digit():
    found = dscr(before(dscr=Decimal("1.2")), after(dscr=Decimal("0.996")), THRESHOLD)
    assert found is not None and found.message == "DSCR fell below 1.00x: 1.20x to 0.996x"


def test_the_threshold_in_force_is_named():
    found = dscr(before(dscr=Decimal("1.30")), after(dscr=Decimal("1.20")), Decimal("1.25"))
    assert found is not None and found.message == "DSCR fell below 1.25x: 1.30x to 1.20x"


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("0.99", "0.90"),  # already below
        ("1.12", "1.00"),  # at the threshold is not below it
        ("1.12", "1.05"),
        (None, "0.90"),
        ("1.12", None),
    ],
)
def test_no_dscr_alert_unless_it_crosses_below(old, new):
    old_value = Decimal(old) if old else None
    new_value = Decimal(new) if new else None
    assert dscr(before(dscr=old_value), after(dscr=new_value), THRESHOLD) is None


# ---------------------------------------------------------------------------- workout


def test_a_changed_workout_strategy_is_a_medium_alert_with_readable_names():
    found = workout(before(workout_strategy="13"), after(workout_strategy="11"))
    assert found == finding(
        Rule.WORKOUT,
        Severity.MEDIUM,
        "workout strategy changed",
        "workout strategy changed: to be determined to full payoff",
    )


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (None, "2", "workout strategy changed: not reported to foreclosure"),
        ("11", None, "workout strategy changed: full payoff to not reported"),
        ("98", "42", "workout strategy changed: other to code 42"),
    ],
)
def test_a_workout_strategy_starting_ending_or_unlisted_is_a_change(old, new, message):
    found = workout(before(workout_strategy=old), after(workout_strategy=new))
    assert found is not None and found.message == message


@pytest.mark.parametrize(("old", "new"), [("11", "11"), ("011", "11"), (None, None)])
def test_the_same_workout_strategy_is_no_alert(old, new):
    assert workout(before(workout_strategy=old), after(workout_strategy=new)) is None


# ---------------------------------------------------------------------------- comments

COMMENT = (
    "The loan transferred to Special Servicing effective 3/6/2025 due to imminent monetary "
    "default.  As of 12/31/2024, DSCR NOI was 0.71x and occupancy was 61.4%. The borrower "
    "has requested a 12-month maturity extension."
)


def test_sentences_keep_dates_decimals_and_percentages_whole():
    assert sentences(COMMENT) == (
        "The loan transferred to Special Servicing effective 3/6/2025 due to imminent monetary "
        "default.",
        "As of 12/31/2024, DSCR NOI was 0.71x and occupancy was 61.4%.",
        "The borrower has requested a 12-month maturity extension.",
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Wells Fargo, N.A. as trustee. Done.", ("Wells Fargo, N.A. as trustee.", "Done.")),
        ("Paid approx. 5% to No. 3. Done!", ("Paid approx. 5% to No. 3.", "Done!")),
        ("U.S. Bank is trustee. Next?", ("U.S. Bank is trustee.", "Next?")),
        ("Signed by J. Smith. Filed.", ("Signed by J. Smith.", "Filed.")),
        ('It said "pay." Then it paid.', ('It said "pay."', "Then it paid.")),
        ("(Aside.) 2026 budget is due.", ("(Aside.)", "2026 budget is due.")),
        ("Due. lower case follows.", ("Due. lower case follows.",)),
        ("No sentence end", ("No sentence end",)),
        ("   ", ()),
        ("", ()),
    ],
)
def test_sentence_boundaries(text, expected):
    assert sentences(text) == expected


def test_added_sentences_are_the_new_ones_in_order_each_once():
    old = "First. Second."
    new = "First.  Third.\nSecond. Fourth. Third."
    assert added_sentences(old, new) == ("Third.", "Fourth.")
    assert added_sentences(None, "First. Second.") == ("First.", "Second.")
    assert added_sentences("First. Second.", "Second.") == ()


def test_a_comment_with_a_new_sentence_is_a_low_alert_showing_only_it():
    old = Comments("0009900001-26-000802", {"2": "First. Second."})
    new = Comments(REPORT, {"2": "First. Second. The borrower asked for more time."})
    found = comment("2", old, new)
    assert found == finding(
        Rule.COMMENT,
        Severity.LOW,
        "servicer comment changed",
        "servicer comment changed: 1 sentence added",
        loan="2",
        accession=REPORT,
        added=("The borrower asked for more time.",),
    )


def test_a_comment_counts_every_new_sentence_and_a_first_comment_is_all_new():
    old = Comments("x", {})
    new = Comments(REPORT, {"2": "First. Second."})
    found = comment("2", old, new)
    assert found is not None
    assert (found.message, found.added) == (
        "servicer comment changed: 2 sentences added",
        ("First.", "Second."),
    )


def test_no_comment_alert_when_nothing_was_added_or_there_is_no_comment():
    old = Comments("x", {"2": "First. Second."})
    assert comment("2", old, Comments(REPORT, {"2": "Second."})) is None
    assert comment("2", old, Comments(REPORT, {})) is None


# ---------------------------------------------------------------------------- the month


def month(period: Period, accession: str, *rows: LoanMonth, **rest) -> Month:
    return Month(period=period, accession=accession, loans={r.loan: r for r in rows}, **rest)


def test_loan_counts_and_lists_read_as_words():
    assert [count_loans(n) for n in (0, 1, 2, 19)] == ["0 loans", "1 loan", "2 loans", "19 loans"]
    assert name_loans(["20"]) == "loan 20"
    assert name_loans(["20", "26", "29"]) == "loans 20, 26, 29"
    assert total_balance([before(ending_balance=Decimal("1.50")), before()]) == Decimal("1.50")
    assert total_balance([before(), before()]) is None
    assert total_balance([]) is None


def test_loans_dropped_from_the_tape_are_one_medium_alert_for_the_deal():
    old = month(
        AUGUST,
        OLD,
        before(loan="29", ending_balance=Decimal("1000000.00")),
        before(loan="1"),
        before(loan="20", ending_balance=Decimal("250000.50")),
        before(loan="26"),
    )
    new = month(SEPTEMBER, NEW, after(loan="1"))
    assert missing(old, new) == finding(
        Rule.MISSING,
        Severity.MEDIUM,
        "no longer reported",
        "3 loans no longer reported: on the August 2026 tape, not on the September 2026 tape "
        "(loans 20, 26, 29); balance not reported for loan 26",
        loan=None,
        loans=("20", "26", "29"),
        balance=Decimal("1250000.50"),
    )
    assert unreported_balances([before(loan="5"), before(loan="7")]) == (
        "; balance not reported for loans 5, 7"
    )
    assert unreported_balances([before(ending_balance=Decimal("1.00"))]) == ""


def test_one_loan_dropped_is_named_in_the_singular():
    old = month(AUGUST, OLD, before(ending_balance=Decimal("9000000.00")))
    assert missing(old, month(SEPTEMBER, NEW)) == finding(
        Rule.MISSING,
        Severity.MEDIUM,
        "no longer reported",
        "1 loan no longer reported: on the August 2026 tape, not on the September 2026 tape "
        "(loan 7)",
        loan=None,
        loans=("7",),
        balance=Decimal("9000000.00"),
    )


def test_a_quarantined_loan_is_listed_with_why_it_is_missing():
    old = month(
        AUGUST,
        OLD,
        before(loan="7", ending_balance=Decimal("1.00")),
        before(loan="10"),
        before(loan="12"),
    )
    new = month(
        SEPTEMBER,
        NEW,
        quarantined={"7": "ending balance is negative", "12": "rate is out of range"},
    )
    found = missing(old, new)
    assert found is not None
    # The reason, not the filing: the alert's row keeps the accession, and an amendment with
    # the same loans and reasons is the same alert.
    assert found.message == (
        "3 loans no longer reported: on the August 2026 tape, not on the September 2026 tape "
        "(loans 7, 10, 12); loan 7 quarantined: ending balance is negative; "
        "loan 12 quarantined: rate is out of range; balance not reported for loans 10, 12"
    )
    assert (found.loans, found.balance, found.reasons, found.accession) == (
        ("7", "10", "12"),
        Decimal("1.00"),
        (("7", "ending balance is negative"), ("12", "rate is out of range")),
        NEW,
    )


def test_loans_that_had_paid_off_may_leave_the_tape():
    old = month(
        AUGUST,
        OLD,
        before(loan="7", ending_balance=Decimal("0.00")),
        before(loan="8", ending_balance=Decimal("5.00")),
    )
    found = missing(old, month(SEPTEMBER, NEW))
    assert found is not None
    assert (found.loans, found.balance) == (("8",), Decimal("5.00"))
    assert found.message.startswith("1 loan no longer reported")
    paid = month(AUGUST, OLD, before(loan="7", ending_balance=Decimal("0.00")))
    assert missing(paid, month(SEPTEMBER, NEW)) is None


def test_no_missing_or_new_finding_when_the_tapes_list_the_same_loans():
    old = month(AUGUST, OLD, before(loan="7"))
    new = month(SEPTEMBER, NEW, after(loan="7"))
    assert (missing(old, new), new_loans(old, new)) == (None, None)


def test_loans_new_to_the_tape_are_one_low_alert_for_the_deal():
    old = month(AUGUST, OLD, before(loan="1"))
    new = month(
        SEPTEMBER,
        NEW,
        after(loan="1"),
        after(loan="10", ending_balance=Decimal("3.00")),
        after(loan="4", ending_balance=Decimal("2.00")),
    )
    assert new_loans(old, new) == finding(
        Rule.NEW,
        Severity.LOW,
        "newly reported",
        "2 loans newly reported: not on the August 2026 tape (loans 4, 10)",
        loan=None,
        loans=("4", "10"),
        balance=Decimal("5.00"),
    )
    one = new_loans(month(AUGUST, OLD), month(SEPTEMBER, NEW, after()))
    assert one is not None
    assert (one.message, one.balance, one.reasons) == (
        "1 loan newly reported: not on the August 2026 tape (loan 7); "
        "balance not reported for loan 7",
        None,
        (),
    )


def test_compare_runs_every_rule_by_loan_in_number_order():
    old = month(
        AUGUST,
        OLD,
        before(loan="10", payment_status="0"),
        before(loan="2", workout_strategy="13", dscr=Decimal("1.12")),
        before(loan="3", ending_balance=Decimal("5.00")),
        comments=Comments("r8", {"2": "Old."}),
    )
    new = month(
        SEPTEMBER,
        NEW,
        after(loan="10", payment_status="1"),
        after(
            loan="2",
            workout_strategy="11",
            dscr=Decimal("0.96"),
            transfer_date=dt.date(2026, 9, 2),
        ),
        after(loan="4"),
        comments=Comments(REPORT, {"2": "Old. New.", "4": "Fresh."}),
    )
    found = compare(old, new, Context(SEPTEMBER, THRESHOLD))
    assert [(f.loan, f.loans, f.rule) for f in found] == [
        ("2", (), Rule.TRANSFER),
        ("2", (), Rule.DSCR),
        ("2", (), Rule.WORKOUT),
        ("2", (), Rule.COMMENT),
        ("4", (), Rule.COMMENT),
        ("10", (), Rule.PAYMENT_WORSENED),
        (None, ("3",), Rule.MISSING),
        (None, ("4",), Rule.NEW),
    ]


def test_compare_runs_the_paid_through_rule_for_the_month_and_skips_comments_without_reports():
    day = dt.date(2026, 8, 1)
    old = month(AUGUST, OLD, before(paid_through=day), comments=None)
    new = month(SEPTEMBER, NEW, after(paid_through=day), comments=Comments(REPORT, {"7": "A."}))
    found = compare(old, new, Context(SEPTEMBER, THRESHOLD))
    assert [f.rule for f in found] == [Rule.PAID_THROUGH]
    assert compare(old, new, Context(AUGUST, THRESHOLD)) == []


def test_identical_months_have_no_findings():
    rows = (
        before(loan="1", paid_through=dt.date(2026, 9, 1), payment_status="0"),
        before(loan="2", workout_strategy="11", transfer_date=dt.date(2025, 3, 6)),
    )
    report = Comments(REPORT, {"2": COMMENT})
    old = month(AUGUST, OLD, *rows, comments=report)
    new = month(SEPTEMBER, NEW, *rows, comments=report)
    assert compare(old, new, Context(SEPTEMBER, THRESHOLD)) == []
