"""Quality reports and label candidates as plain text, from what is stored. Pure.

The text opens with what a risk officer must not miss: the flags against the previous report
and whether the abstention band holds, then each field's measures, then labeller agreement.
"""

from typing import TYPE_CHECKING, Any

from cmbs.quality.measure import NOT_MEASURED

if TYPE_CHECKING:
    from cmbs.quality.service import CandidateView, ReportView


def measure_text(measured: dict[str, Any]) -> str:
    """'0.905 (95% interval 0.779 to 0.962; 38 of 42)' from a stored measure."""
    if measured["rate"] is None:
        return f"not measured ({measured['successes']} of {measured['trials']})"
    return (
        f"{measured['rate']} (95% interval {measured['low']} to {measured['high']}; "
        f"{measured['successes']} of {measured['trials']})"
    )


def versions_text(versions: list[int]) -> str:
    """[7] -> 'settings version 7'; [6, 7] -> 'settings versions 6 and 7'."""
    if len(versions) == 1:
        return f"settings version {versions[0]}"
    named = [str(version) for version in versions]
    return f"settings versions {', '.join(named[:-1])} and {named[-1]}"


def report_text(report: "ReportView") -> str:
    results = report.results
    lines = [
        f"quality report {report.id}: model {report.model}, prompt version "
        f"{report.prompt_version}, {versions_text(report.settings_versions)}; "
        f"{report.items} golden comments; built {report.built_at:%Y-%m-%d} by {report.built_by}"
    ]
    lines += [f"flag: {flag}" for flag in report.flags]
    if report.previous_id is not None and not report.flags:
        lines.append(f"no measure fell since quality report {report.previous_id}")
    lines += [results["bands"][name] for name in results["fields"]]
    for measured in results["fields"].values():
        disputed = (
            f"; {measured['disputed']} disputed labels left out" if measured["disputed"] else ""
        )
        lines.append(
            f"{measured['label']}: precision {measure_text(measured['precision'])}; recall "
            f"{measure_text(measured['recall'])}; auto-accepted accuracy "
            f"{measure_text(measured['automatic'])}{disputed}"
        )
    agreement = results["agreement"]
    if agreement["double_labelled"]:
        kappa = ", ".join(
            f"{name.replace('_', ' ')} {value if value is not None else 'undefined'}"
            for name, value in agreement["kappa"].items()
        )
        lines.append(
            f"labeller agreement: {agreement['agreed']} of {agreement['double_labelled']} "
            f"double-labelled comments ({agreement['rate']}, 95% interval {agreement['low']} "
            f"to {agreement['high']}); Cohen's kappa: {kappa}"
        )
    else:
        lines.append(f"labeller agreement: {NOT_MEASURED} (no double-labelled comments)")
    return "\n".join(lines) + "\n"


def candidate_text(candidate: "CandidateView") -> str:
    """'#101 proposed: Fixture Trust 2026-F1 loan 2, September 2026: borrower request
    forbearance (sentence 3) by alex'."""
    labels = "; ".join(
        f"{name.replace('_', ' ')} {value if value is not None else 'not stated'}"
        + (f" (sentence {candidate.sentences[name]})" if candidate.sentences.get(name) else "")
        + f" by {labeller}"
        for labeller, answers in candidate.labels.items()
        for name, value in answers.items()
    )
    return (
        f"#{candidate.id} {candidate.status}: {candidate.deal} loan {candidate.loan}, "
        f"{candidate.period.label}: {labels}"
    )
