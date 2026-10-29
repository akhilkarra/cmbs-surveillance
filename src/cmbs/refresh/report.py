"""The refresh routine's report: problems first, then what each month loaded, with links.

Read from a run's stored summary (`cmbs.refresh.plan.summary`), so any run can be reported again
later exactly as it was. Pure functions, no database.

The report opens with what went wrong, if anything: where the run stopped, then one line of
counts ("1 loan quarantined, 1 roll-forward break"). Then the run and its outcome, which is
never "succeeded" when a check found fault (`cmbs.refresh.plan.run_status`). Then each deal:
"up to date through September 2026" when it has no month left to load, and for each month the
run tried, its counts (loans loaded, comments extracted, items sent to review, alerts raised, and
what the checks found), each followed by the filings it came from and their EDGAR links.
"""

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from cmbs.refresh.plan import DealOutcome, MonthOutcome
from cmbs.runs.service import RunView

TAPE_FORM = "ABS-EE"
REPORT_FORM = "10-D"


def counted(number: int, singular: str, plural: str | None = None) -> str:
    """'1 loan', '2 loans', '0 items'."""
    return f"{number} {singular if number == 1 else (plural or singular + 's')}"


def _months(summary: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [month for deal in summary.get("deals", []) for month in deal.get("months", [])]


def problem_counts(summary: Mapping[str, Any]) -> list[str]:
    """What went wrong in the run, as counts, in the order a reader should act on them."""
    months = _months(summary)
    loaded = [m for m in months if m["outcome"] == MonthOutcome.LOADED.value]
    deals = summary.get("deals", [])
    quarantined = sum(m.get("quarantined", 0) for m in loaded)
    breaks = sum(m.get("rollforward_breaks") or 0 for m in loaded)
    flags = sum(m.get("flags", 0) for m in loaded)
    text = sum(m.get("text_flags") or 0 for m in loaded)
    not_loaded = sum(m["outcome"] == MonthOutcome.NOT_LOADED.value for m in months)
    failed = sum(d["outcome"] == DealOutcome.FAILED.value and not d.get("months") for d in deals)
    held = sum(d["outcome"] == DealOutcome.SKIPPED.value and d.get("problem") for d in deals)
    found = [
        (quarantined, counted(quarantined, "loan") + " quarantined"),
        (breaks, counted(breaks, "roll-forward break")),
        (flags, counted(flags, "reconciliation flag")),
        (text, counted(text, "text-versus-tape flag")),
        (not_loaded, counted(not_loaded, "month") + " not loaded"),
        (failed, counted(failed, "deal") + " not refreshed"),
        (held, counted(held, "deal") + " held by another run"),
    ]
    return [text for number, text in found if number]


def stopped_line(stopped: Mapping[str, Any] | None) -> str | None:
    """'stopped at Fixture Trust 2026-F1, September 2026: <error> (<detail>)'."""
    if not stopped:
        return None
    where = ", ".join(str(stopped[key]) for key in ("deal", "period") if stopped.get(key))
    error = stopped.get("error")
    line = f"stopped at {where}: {error}" if where else f"stopped: {error}"
    detail = stopped.get("detail")
    return f"{line} ({detail})" if detail else line


def cite(filings: Iterable[Mapping[str, str]], forms: Sequence[str]) -> str:
    """'ABS-EE 0009900001-26-000419 <url>; 10-D ...', the month's filings of those forms."""
    chosen = [f for form in forms for f in filings if f["form"] == form]
    return "; ".join(f"{f['form']} {f['accession']} {f['url']}" for f in chosen) or "no filing"


def month_report(month: Mapping[str, Any]) -> list[str]:
    """A month's outcome, then each count with the filings it came from."""
    filings = month.get("filings", [])
    if month["outcome"] != MonthOutcome.LOADED.value:
        head = f"{month['period']}: not loaded, {month['reason']}"
        if month.get("detail"):
            head += f" ({month['detail']})"
        tried = cite(filings, (TAPE_FORM, REPORT_FORM))
        return [head, *([f"  tried {tried}"] if filings else [])]
    tape, report = cite(filings, (TAPE_FORM,)), cite(filings, (REPORT_FORM,))
    both = cite(filings, (TAPE_FORM, REPORT_FORM))
    lines = [f"{month['period']}: loaded"]
    lines.append(f"  {counted(month.get('loan_rows', 0), 'loan')} loaded: {tape}")
    comments = month.get("comments")
    if comments is None:
        lines.append(f"  comments not extracted (the run had no model to ask): {report}")
    else:
        lines.append(f"  {counted(comments, 'comment')} extracted: {report}")
        items = month.get("review_items", 0)
        lines.append(f"  {counted(items, 'item')} sent to review: {report}")
    lines.append(f"  {counted(month.get('alerts', 0), 'alert')} raised: {both}")
    quarantined = month.get("quarantined", 0)
    if quarantined:
        lines.append(f"  {counted(quarantined, 'loan')} quarantined: {tape}")
    breaks = month.get("rollforward_breaks")
    if breaks is None:
        lines.append(f"  roll-forward not checked: {tape}")
    elif breaks:
        lines.append(f"  {counted(breaks, 'roll-forward break')}: {tape}")
    else:
        lines.append(f"  roll-forward passed: {tape}")
    flags = month.get("flags", 0)
    if flags:
        lines.append(f"  {counted(flags, 'reconciliation flag')}: {both}")
    else:
        lines.append(f"  reconciliation passed: {both}")
    text = month.get("text_flags")
    if text is None:
        lines.append(f"  text versus tape not checked: {both}")
    elif text:
        lines.append(f"  {counted(text, 'text-versus-tape flag')}: {both}")
    else:
        lines.append(f"  text versus tape agreed: {both}")
    lines += [f"  {note}" for note in month.get("notes", [])]
    return lines


def deal_head(deal: Mapping[str, Any]) -> str:
    """'Fixture Trust 2026-F1: up to date through September 2026', or why it is not."""
    name, outcome = deal["deal"], deal["outcome"]
    if outcome == DealOutcome.SKIPPED.value or (
        outcome == DealOutcome.FAILED.value and not deal.get("months")
    ):
        return f"{name}: {outcome}, {deal.get('reason')}"
    through = deal.get("loaded_through")
    loaded = f"loaded through {through}" if through else "nothing loaded yet"
    left = sum(m["outcome"] == MonthOutcome.NOT_LOADED.value for m in deal.get("months", []))
    if left:
        return f"{name}: {loaded}, {counted(left, 'month')} not loaded"
    return f"{name}: up to date through {through}" if through else f"{name}: {loaded}"


def routine_report(run: RunView) -> str:
    """The whole report: problems, the run's outcome, then each deal and month."""
    summary = run.summary
    lines: list[str] = []
    stopped = stopped_line(run.stopped)
    if stopped:
        lines.append(stopped)
    problems = problem_counts(summary)
    if problems:
        lines.append(", ".join(problems))
    lines.append(f"run {run.number} ({run.kind}, started by {run.started_by}): {run.status}")
    for deal in summary.get("deals", []):
        lines.append(deal_head(deal))
        for month in deal.get("months", []):
            lines += [f"  {line}" for line in month_report(month)]
        lines += [f"  {note}" for note in deal.get("notes", [])]
    return "\n".join(lines)
