"""Text and JSON views of check results: pure, exact."""

import datetime as dt

from cmbs.crosscheck import report, rules
from cmbs.crosscheck import rollforward as rf
from cmbs.crosscheck.service import CheckSummary, FlagView, LoanTextVsTape, Raised
from cmbs.domain import Period, money

SEPTEMBER = Period(2026, 9)


def loan(
    number: str, roll: rf.Roll, continuity: rf.Continuity, *, not_reported=False
) -> rf.LoanRollForward:
    return rf.LoanRollForward(
        loan=number,
        period=SEPTEMBER,
        accession="0009900101-26-000901",
        filed=dt.date(2026, 9, 29),
        balances=rf.Balances(
            beginning=money("9,050,000.00"),
            scheduled_principal=money("40,000.00"),
            unscheduled_principal=None if not_reported else money("0.00"),
            other_principal_adjustments=money("0.00"),
            realized_loss=money("0.00"),
            ending=rf.Ending(money("9,000,000.00"), rf.ACTUAL),
        ),
        roll=roll,
        continuity=continuity,
        previous_accession=None,
    )


BREAK = loan(
    "7",
    rf.Roll(
        rf.Status.BREAK,
        expected_ending=money("9,010,000.00"),
        gap=money("10,000.00"),
        not_reported=("unscheduled principal",),
    ),
    rf.Continuity(rf.Status.BREAK, previous_ending=money("9,060,000.00"), gap=money("10,000.00")),
    not_reported=True,
)
PASSED = loan(
    "1",
    rf.Roll(rf.Status.PASSED, expected_ending=money("9,000,000.00"), gap=money("0.00")),
    rf.Continuity(rf.Status.NOT_CHECKED, reason="no August 2026 tape row for the loan"),
)
UNCHECKED = loan(
    "9",
    rf.Roll(rf.Status.NOT_CHECKED, reason="beginning balance not reported"),
    rf.Continuity(rf.Status.NOT_CHECKED, reason="beginning balance not reported"),
)


def test_rollforward_lines():
    assert report.rollforward_line(BREAK) == (
        "loan 7: roll-forward break: balance does not roll forward by $10,000.00; beginning "
        "balance does not match last month's ending balance by $10,000.00; unscheduled "
        "principal not reported"
    )
    assert report.rollforward_line(PASSED) == "loan 1: roll-forward passed"
    assert report.rollforward_line(UNCHECKED) == (
        "loan 9: roll-forward not checked: beginning balance not reported"
    )


def test_rollforward_counts_put_breaks_first_and_leave_out_zeros():
    assert report.rollforward_counts([PASSED, BREAK, UNCHECKED]) == (
        "1 roll-forward break, 1 passed, 1 not checked"
    )
    assert report.rollforward_counts([BREAK, BREAK]) == "2 roll-forward breaks"
    assert report.rollforward_counts([PASSED]) == "1 passed"
    assert report.rollforward_counts([]) == "no loans"


def test_kind_of_tells_notes_from_flags():
    assert report.kind_of(rules.NEWER_FINANCIALS) == "note"
    assert report.kind_of(rules.TRANSFER_MISSING) == "flag"
    assert report.kind_of(rf.ROLLFORWARD) == "flag"


def summary() -> CheckSummary:
    return CheckSummary(
        deal="Checks Trust 2026-C1",
        period=SEPTEMBER,
        as_of=dt.date(2026, 10, 1),
        text_vs_tape=[
            LoanTextVsTape("2", []),
            LoanTextVsTape("4", [], "loan 4 is not on the September 2026 tape as of 2026-10-01"),
        ],
        rollforward=[PASSED, BREAK],
        flags=[
            Raised("7", rf.ROLLFORWARD, rf.ROLL_BREAK, {"gap": "$10,000.00"}, True),
            Raised("2", rules.NEWER_FINANCIALS, rules.NEWER_THAN_TAPE, {}, False),
        ],
    )


def test_summary_text_opens_with_the_flags():
    assert report.summary_text(summary()) == (
        "Checks Trust 2026-C1, September 2026, as of 2026-10-01\n"
        "1 raised, 1 already raised\n"
        "  loan 7: flag: balance does not roll forward\n"
        "  loan 2: note: text has newer financials than tape\n"
        "roll-forward: 1 roll-forward break, 1 passed\n"
        "text versus tape: 1 of 2 comments compared\n"
        "  loan 4 is not on the September 2026 tape as of 2026-10-01\n"
    )


def test_summary_text_without_reads_has_only_the_header():
    empty = CheckSummary("D", SEPTEMBER, dt.date(2026, 10, 1))
    assert (
        report.summary_text(empty)
        == "D, September 2026, as of 2026-10-01\n0 raised, 0 already raised\n"
    )


def test_rollforward_json_keeps_missing_amounts_null():
    assert report.rollforward_json(BREAK) == {
        "loan": "7",
        "period": "September 2026",
        "accession": "0009900101-26-000901",
        "filed": "2026-09-29",
        "status": "break",
        "label": "roll-forward break",
        "roll": {
            "status": "break",
            "beginning_balance": "9050000.00",
            "scheduled_principal": "40000.00",
            "unscheduled_principal": None,
            "other_principal_adjustments": "0.00",
            "realized_loss": "0.00",
            "ending_balance": "9000000.00",
            "ending_basis": "actual",
            "explained_ending_balance": "9010000.00",
            "gap": "10000.00",
            "not_reported": ["unscheduled principal"],
            "reason": None,
        },
        "continuity": {
            "status": "break",
            "previous_ending_balance": "9060000.00",
            "previous_accession": None,
            "gap": "10000.00",
            "reason": None,
        },
    }


def test_summary_and_flag_json():
    finding = rules.Finding(rules.TRANSFER_MISSING, rules.Status.FLAG, rules.NOT_IN_TAPE, {"a": 1})
    assert report.loan_findings_json(LoanTextVsTape("5", [finding])) == {
        "loan": "5",
        "skipped": None,
        "findings": [
            {
                "check": rules.TRANSFER_MISSING,
                "status": "flag",
                "message": "transfer in text, not in tape",
                "sides": {"a": 1},
            }
        ],
    }
    body = report.summary_json(summary())
    assert (body["deal"], body["period"], body["as_of"]) == (
        "Checks Trust 2026-C1",
        "September 2026",
        "2026-10-01",
    )
    assert body["flags"] == [
        {
            "loan": "7",
            "check": rf.ROLLFORWARD,
            "kind": "flag",
            "message": rf.ROLL_BREAK,
            "sides": {"gap": "$10,000.00"},
            "new": True,
        },
        {
            "loan": "2",
            "check": rules.NEWER_FINANCIALS,
            "kind": "note",
            "message": rules.NEWER_THAN_TAPE,
            "sides": {},
            "new": False,
        },
    ]
    assert [item["loan"] for item in body["rollforward"]] == ["1", "7"]
    assert [item["skipped"] for item in body["text_vs_tape"]] == [
        None,
        "loan 4 is not on the September 2026 tape as of 2026-10-01",
    ]
    view = FlagView(
        id=3,
        deal="D",
        loan="2",
        period=SEPTEMBER,
        check=rules.NEWER_FINANCIALS,
        message=rules.NEWER_THAN_TAPE,
        sides={},
        raised_at=dt.datetime(2026, 10, 1, 15, tzinfo=dt.UTC),
    )
    assert (
        report.flag_line(view)
        == "#3 September 2026 loan 2: note: text has newer financials than tape"
    )
    assert report.flag_json(view) == {
        "id": 3,
        "deal": "D",
        "loan": "2",
        "period": "September 2026",
        "check": rules.NEWER_FINANCIALS,
        "kind": "note",
        "message": "text has newer financials than tape",
        "sides": {},
        "raised_at": "2026-10-01T15:00:00+00:00",
    }
    undated = FlagView(4, "D", "2", None, rf.ROLLFORWARD, rf.ROLL_BREAK, {}, view.raised_at)
    assert report.flag_line(undated) == "#4 - loan 2: flag: balance does not roll forward"
    assert report.flag_json(undated)["period"] is None
