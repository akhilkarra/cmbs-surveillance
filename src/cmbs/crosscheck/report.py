"""Plain-text and JSON views of check results, for the command line and the API. Pure.

Problems come first: flags and breaks, then notes, then the counts. Money in JSON is text with
two decimals; an amount not reported is null, never zero.
"""

from collections import Counter
from collections.abc import Sequence
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from cmbs.crosscheck import rollforward as rf
from cmbs.crosscheck import rules
from cmbs.domain import format_money

if TYPE_CHECKING:
    from cmbs.crosscheck.service import CheckSummary, FlagView, LoanTextVsTape


def _money(amount: Decimal | None) -> str | None:
    return None if amount is None else f"{amount:.2f}"


def kind_of(check: str) -> str:
    """'note' for a check that only notes something, 'flag' for a disagreement or a break."""
    return "note" if check in rules.NOTE_CHECKS else "flag"


def rollforward_line(result: rf.LoanRollForward) -> str:
    """'loan 7: roll-forward break: balance does not roll forward by $10,000.00'."""
    parts: list[str] = []
    roll = result.roll
    if roll.status is rf.Status.BREAK and roll.gap is not None:
        parts.append(f"{rf.ROLL_BREAK} by {format_money(roll.gap)}")
    if roll.status is rf.Status.NOT_CHECKED and roll.reason is not None:
        parts.append(roll.reason)
    if result.continuity.status is rf.Status.BREAK and result.continuity.gap is not None:
        parts.append(f"{rf.CONTINUITY_BREAK} by {format_money(result.continuity.gap)}")
    if roll.not_reported:
        parts.append(f"{', '.join(roll.not_reported)} not reported")
    detail = f": {'; '.join(parts)}" if parts else ""
    return f"loan {result.loan}: {result.label}{detail}"


def rollforward_counts(results: Sequence[rf.LoanRollForward]) -> str:
    """'1 roll-forward break, 1 passed, 1 not checked': breaks first, zero counts left out."""
    counts = Counter(result.status for result in results)
    breaks = counts[rf.Status.BREAK]
    parts = [f"{breaks} roll-forward break{'' if breaks == 1 else 's'}"] if breaks else []
    parts += [
        f"{counts[status]} {status.value}"
        for status in (rf.Status.PASSED, rf.Status.NOT_CHECKED)
        if counts[status]
    ]
    return ", ".join(parts) if parts else "no loans"


def summary_text(summary: "CheckSummary") -> str:
    """A run of the checks as the command line prints it."""
    lines = [f"{summary.deal}, {summary.period.label}, as of {summary.as_of.isoformat()}"]
    new = sum(1 for flag in summary.flags if flag.new)
    lines.append(f"{new} raised, {len(summary.flags) - new} already raised")
    lines += [
        f"  loan {flag.loan}: {kind_of(flag.check)}: {flag.message}" for flag in summary.flags
    ]
    if summary.rollforward:
        lines.append(f"roll-forward: {rollforward_counts(summary.rollforward)}")
    if summary.text_vs_tape:
        compared = sum(1 for loan in summary.text_vs_tape if loan.skipped is None)
        total = len(summary.text_vs_tape)
        lines.append(f"text versus tape: {compared} of {total} comments compared")
        lines += [f"  {loan.skipped}" for loan in summary.text_vs_tape if loan.skipped]
    return "\n".join(lines) + "\n"


def flag_line(view: "FlagView") -> str:
    """'#3 September 2026 loan 2: flag: transfer in text, not in tape'."""
    month = view.period.label if view.period is not None else "-"
    return f"#{view.id} {month} loan {view.loan}: {kind_of(view.check)}: {view.message}"


def rollforward_json(result: rf.LoanRollForward) -> dict[str, Any]:
    balances = result.balances
    return {
        "loan": result.loan,
        "period": result.period.label,
        "accession": result.accession,
        "filed": result.filed.isoformat(),
        "status": result.status.value,
        "label": result.label,
        "roll": {
            "status": result.roll.status.value,
            "beginning_balance": _money(balances.beginning),
            **{name: _money(getattr(balances, name)) for name, _ in rf.DEDUCTIONS},
            "ending_balance": _money(balances.ending.amount),
            "ending_basis": balances.ending.basis,
            "explained_ending_balance": _money(result.roll.expected_ending),
            "gap": _money(result.roll.gap),
            "not_reported": list(result.roll.not_reported),
            "reason": result.roll.reason,
        },
        "continuity": {
            "status": result.continuity.status.value,
            "previous_ending_balance": _money(result.continuity.previous_ending),
            "previous_accession": result.previous_accession,
            "gap": _money(result.continuity.gap),
            "reason": result.continuity.reason,
        },
    }


def finding_json(finding: rules.Finding) -> dict[str, Any]:
    return {
        "check": finding.check,
        "status": finding.status.value,
        "message": finding.message,
        "sides": finding.sides,
    }


def loan_findings_json(loan: "LoanTextVsTape") -> dict[str, Any]:
    return {
        "loan": loan.loan,
        "skipped": loan.skipped,
        "findings": [finding_json(finding) for finding in loan.findings],
    }


def flag_json(view: "FlagView") -> dict[str, Any]:
    return {
        "id": view.id,
        "deal": view.deal,
        "loan": view.loan,
        "period": view.period.label if view.period is not None else None,
        "check": view.check,
        "kind": kind_of(view.check),
        "message": view.message,
        "sides": view.sides,
        "raised_at": view.raised_at.isoformat(),
    }


def summary_json(summary: "CheckSummary") -> dict[str, Any]:
    return {
        "deal": summary.deal,
        "period": summary.period.label,
        "as_of": summary.as_of.isoformat(),
        "flags": [
            {
                "loan": flag.loan,
                "check": flag.check,
                "kind": kind_of(flag.check),
                "message": flag.message,
                "sides": flag.sides,
                "new": flag.new,
            }
            for flag in summary.flags
        ],
        "rollforward": [rollforward_json(result) for result in summary.rollforward],
        "text_vs_tape": [loan_findings_json(loan) for loan in summary.text_vs_tape],
    }
