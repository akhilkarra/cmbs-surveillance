"""Write golden_set.json: 100 synthetic special-servicing comments with synthetic labels.

Run `uv run python tests/fixtures/quality/make_golden_set.py` to rebuild it; the output is
deterministic. Nothing here is a filed comment or a real labeller's work.

The shape the extraction-quality scenarios state:
- 100 comments; every fifth comment and the one after it (G001, G002, G006, G007, ...) states
  a borrower request: 40 in all, with a request status;
- "alex" labels G001 to G060 alone, "morgan" G061 to G080 alone, and both label G081 to G100
  (20 double-labelled comments);
- the two agree on 19 of those: on G082 they differ on the request status only ("granted"
  against "denied"), so the borrower request labels stay undisputed.
"""

import datetime as dt
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
REQUESTS = (
    ("maturity extension", "a 12-month extension of the loan's maturity date"),
    ("forbearance", "a three-month forbearance of debt service payments"),
    ("loan modification", "a modification of the loan's amortization schedule"),
    ("discounted payoff", "a discounted payoff of the loan"),
    ("loan assumption", "consent to a transfer of the property and assumption of the loan"),
    ("collateral release", "the release of an outparcel from the collateral"),
)
STATUSES = (
    ("pending", "The special servicer is reviewing the request."),
    ("granted", "The special servicer approved the request."),
    ("denied", "The special servicer declined the request."),
)
REASONS = ("imminent monetary default", "maturity default", "a cash management trigger")
NO_REQUEST = (
    "The special servicer is dual-tracking foreclosure while it evaluates the property.",
    "A receiver was appointed and is managing the property.",
    "The special servicer has engaged counsel and ordered a new appraisal.",
    "The property is being marketed for sale by the receiver.",
)
DOUBLE = range(81, 101)
DISAGREE = 82


def comment(number: int) -> tuple[str, dict[str, str | None]]:
    transferred = dt.date(2024, 1, 1) + dt.timedelta(days=17 * number)
    opening = (
        f"The loan transferred to special servicing effective {transferred.month}/"
        f"{transferred.day}/{transferred.year} due to {REASONS[number % len(REASONS)]}."
    )
    if number % 5 in (1, 2):
        request, phrase = REQUESTS[number % len(REQUESTS)]
        status, sentence = STATUSES[number % len(STATUSES)]
        text = f"{opening} The borrower has requested {phrase}. {sentence}"
        return text, {"borrower_request": request, "request_status": status}
    text = f"{opening} {NO_REQUEST[number % len(NO_REQUEST)]}"
    return text, {"borrower_request": None, "request_status": None}


def labellers(number: int) -> tuple[str, ...]:
    if number in DOUBLE:
        return ("alex", "morgan")
    return ("alex",) if number <= 60 else ("morgan",)


def build() -> dict[str, object]:
    items = []
    for number in range(1, 101):
        text, labels = comment(number)
        by = {name: dict(labels) for name in labellers(number)}
        if number == DISAGREE:
            by["morgan"]["request_status"] = (
                "denied" if labels["request_status"] != "denied" else "pending"
            )
        items.append({"key": f"G{number:03d}", "comment": text, "labels": by})
    return {
        "description": "Synthetic golden set for the extraction-quality scenarios; not filed "
        "comments, not real labels. Built by make_golden_set.py.",
        "items": items,
    }


if __name__ == "__main__":
    (HERE / "golden_set.json").write_text(json.dumps(build(), indent=1) + "\n")
