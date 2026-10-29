"""The extraction decision table as rows, so its totality can be checked against Lean's.

`cmbs.extraction.decision` states its rules twice: as code (`decide`) and as a table in its
docstring. `docstring_rows` reads that table into rows; `lean/Invariants/Decision.lean` states
the same table and proves every combination matches exactly one row and that its `decide` follows
the table. The tests and the decision-table scenario check Lean's rows equal the docstring's and
Python's `decide` equals the table on all 18 combinations.
"""

import itertools
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from cmbs.extraction import decision
from cmbs.extraction.decision import Band, Outcome

EVIDENCE = {"found": (True,), "not found": (False,), "any": (True, False)}
ALL_BANDS = tuple(Band)
_ROW = re.compile(r"^\|(?P<cells>.+)\|\s*$")


@dataclass(frozen=True)
class Row:
    """One row of the table: the values each column accepts, and the result."""

    support: tuple[Band, ...]
    evidence_found: tuple[bool, ...]
    answer: tuple[Band, ...]
    outcome: Outcome
    reason: str | None

    def matches(self, combination: "Combination") -> bool:
        return (
            combination.support in self.support
            and combination.evidence_found in self.evidence_found
            and combination.answer in self.answer
        )


@dataclass(frozen=True)
class Combination:
    support: Band
    evidence_found: bool
    answer: Band

    @property
    def label(self) -> str:
        found = "found" if self.evidence_found else "not found"
        return f"support {self.support}, evidence {found}, answer {self.answer}"


def combinations() -> list[Combination]:
    """All 18: three support bands, evidence found or not, three answer bands."""
    return [
        Combination(support, found, answer)
        for support, found, answer in itertools.product(ALL_BANDS, (True, False), ALL_BANDS)
    ]


def _bands(cell: str) -> tuple[Band, ...]:
    if cell == "any":
        return ALL_BANDS
    return tuple(Band(name.strip()) for name in cell.split(","))


def _result(cell: str) -> tuple[Outcome, str | None]:
    """'review: unsettled answer' -> (review, 'unsettled answer'); 'auto-accept' -> (.., None)."""
    outcome, _, reason = cell.partition(":")
    return Outcome(outcome.strip()), reason.strip() or None


def parse_rows(text: str) -> list[Row]:
    """The rows of the first Markdown table in `text` whose header is support, evidence, answer
    and outcome; the separator line is skipped."""
    rows: list[Row] = []
    header_seen = False
    for line in text.splitlines():
        match = _ROW.match(line.strip())
        if match is None:
            if header_seen:
                break
            continue
        cells = [cell.strip() for cell in match["cells"].split("|")]
        if cells == ["support", "evidence", "answer", "outcome"]:
            header_seen = True
            continue
        if not header_seen or set("".join(cells)) <= set("-"):
            continue
        support, evidence, answer, result = cells
        outcome, reason = _result(result)
        rows.append(Row(_bands(support), EVIDENCE[evidence], _bands(answer), outcome, reason))
    return rows


def docstring_rows() -> list[Row]:
    return parse_rows(decision.__doc__ or "")


def lean_rows(table: Iterable[Mapping[str, Any]]) -> list[Row]:
    """Rows from the oracle's table (`{"kind": "table"}`, or the committed copy of it)."""
    return [
        Row(
            support=tuple(Band(name) for name in row["support"]),
            evidence_found=tuple(row["evidence_found"]),
            answer=tuple(Band(name) for name in row["answer"]),
            outcome=Outcome(row["outcome"]),
            reason=row["reason"],
        )
        for row in table
    ]


def matching(rows: Sequence[Row], combination: Combination) -> list[Row]:
    return [row for row in rows if row.matches(combination)]


def python_decision(combination: Combination) -> tuple[Outcome, str | None]:
    found = decision.decide(
        answer=combination.answer,
        support=combination.support,
        evidence_found=combination.evidence_found,
    )
    return found.outcome, found.reason
