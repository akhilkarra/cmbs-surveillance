"""The Lean oracle: the Lean definitions the theorems are about, run on Python's cases.

`lean/Oracle.lean` compiles to `lean/.lake/build/bin/oracle`, which reads one JSON case per line
and answers one JSON line each (grill log Q4). This module turns Python's values into those cases
and back, exactly: money in integer cents, rates as integers over a power of ten shared by the
pool, "not reported" as null. The differential tests and the decision-table scenario use it; the
running pipeline never does.

`build()` runs `lake build` in `lean/` and returns the executable, or None with the reason when
Lean is not installed, so tests that need the oracle skip with that reason.
"""

import json
import shutil
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import IO, Any

from cmbs.crosscheck.rollforward import Balances, Ending
from cmbs.extraction.decision import Band
from cmbs.invariants.partition import PoolLoan, code_of
from cmbs.invariants.wac import RatedLoan
from cmbs.tape.convert import RATE_LIMIT

LEAN = Path(__file__).resolve().parents[3] / "lean"
# Lean's decision table as the oracle printed it (`{"kind": "table"}`), committed so the scenario
# runs where Lean is not installed; the tests check it against the oracle wherever Lean is.
COMMITTED_TABLE = LEAN / "decision_table.json"


class OracleError(RuntimeError):
    """The oracle could not answer a case."""


def cents(amount: Decimal | None) -> int | None:
    """Dollars to integer cents, exactly; a fraction of a cent is an error, never rounded."""
    if amount is None:
        return None
    scaled = amount * 100
    if scaled != scaled.to_integral_value():
        raise ValueError(f"{amount} is not a whole number of cents")
    return int(scaled)


def rate_scale(rates: Iterable[Decimal | None]) -> int:
    """The fewest decimal places that write every rate as a whole number: 0.03768 needs 5."""
    places = [-int(rate.as_tuple().exponent) for rate in rates if rate is not None]
    return max([0, *places])


def scaled(rate: Decimal | None, scale: int) -> int | None:
    if rate is None:
        return None
    value = rate.scaleb(scale)
    if value != value.to_integral_value():
        raise ValueError(f"{rate} has more than {scale} decimal places")
    return int(value)


def continuity_case(beginning: Decimal | None, previous: Ending | None) -> dict[str, Any]:
    """This month's beginning balance and last month's ending one (`previous` None when last
    month's tape does not show the loan)."""
    if previous is not None and previous.problem is not None:
        raise ValueError(f"last month's ending balance could not be read: {previous.problem}")
    return {
        "kind": "continuity",
        "beginning": cents(beginning),
        "previous_row": previous is not None,
        "previous_ending": None if previous is None else cents(previous.amount),
    }


def rollforward_case(balances: Balances) -> dict[str, Any]:
    """A month of the roll-forward. A month whose ending balance could not be read is not a case:
    the oracle models amounts as read."""
    if balances.ending.problem is not None:
        raise ValueError(f"the ending balance could not be read: {balances.ending.problem}")
    return {
        "kind": "rollforward",
        "beginning": cents(balances.beginning),
        "scheduled": cents(balances.scheduled_principal),
        "unscheduled": cents(balances.unscheduled_principal),
        "other": cents(balances.other_principal_adjustments),
        "realized_loss": cents(balances.realized_loss),
        "ending": cents(balances.ending.amount),
    }


def partition_case(loans: Sequence[PoolLoan]) -> dict[str, Any]:
    """A pool for the partition, each status as Python reads it (`code_of`)."""
    return {
        "kind": "partition",
        "loans": [
            {"status": code_of(loan.status), "balance": cents(loan.balance)} for loan in loans
        ],
    }


def wac_case(loans: Sequence[RatedLoan]) -> tuple[dict[str, Any], int]:
    """A pool for the WAC, and the scale (a power of ten) its rates and the rate limit were
    written over."""
    scale = rate_scale([RATE_LIMIT, *(loan.rate for loan in loans)])
    case = {
        "kind": "wac",
        "cap": scaled(RATE_LIMIT, scale),
        "loans": [
            {"balance": cents(loan.balance), "rate": scaled(loan.rate, scale)} for loan in loans
        ],
    }
    return case, scale


@dataclass(frozen=True)
class LeanWac:
    rate: Fraction
    lowest: Fraction
    highest: Fraction


def lean_wac(answer: dict[str, int] | None, scale: int) -> LeanWac | None:
    """The oracle's WAC `num / den` and its bounds, back over the rates' scale."""
    if answer is None:
        return None
    unit = 10**scale
    return LeanWac(
        rate=Fraction(answer["num"], answer["den"] * unit),
        lowest=Fraction(answer["lo"], unit),
        highest=Fraction(answer["hi"], unit),
    )


def decision_case(*, answer: Band, support: Band, evidence_found: bool) -> dict[str, Any]:
    return {
        "kind": "decision",
        "answer": answer.value,
        "support": support.value,
        "evidence_found": evidence_found,
    }


TABLE_CASE: dict[str, Any] = {"kind": "table"}


def committed_table() -> list[dict[str, Any]]:
    return json.loads(COMMITTED_TABLE.read_text())


class Oracle:
    """One running oracle process; `ask` sends a case and returns its answer."""

    def __init__(self, command: Sequence[str]) -> None:
        self._process = subprocess.Popen(
            list(command),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )

    def _pipes(self) -> tuple[IO[str], IO[str]]:
        stdin, stdout = self._process.stdin, self._process.stdout
        assert stdin is not None and stdout is not None
        return stdin, stdout

    def ask(self, case: dict[str, Any]) -> Any:
        stdin, stdout = self._pipes()
        stdin.write(json.dumps(case) + "\n")
        stdin.flush()
        line = stdout.readline()
        if not line:
            raise OracleError(f"the oracle stopped without answering {case}")
        answer = json.loads(line)
        if isinstance(answer, dict) and set(answer) == {"error"}:
            raise OracleError(f"the oracle refused {case}: {answer['error']}")
        return answer

    def close(self) -> None:
        stdin, _ = self._pipes()
        stdin.close()
        self._process.wait(timeout=10)

    def __enter__(self) -> "Oracle":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def build(lean: Path = LEAN) -> tuple[Path | None, str]:
    """Build the Lean project; the oracle executable and "", or None and why it is missing."""
    lake = shutil.which("lake")
    if lake is None:
        return None, "Lean is not installed: no `lake` on PATH (install elan, see CLAUDE.md)"
    done = subprocess.run(
        [lake, "build", "--wfail"], cwd=lean, capture_output=True, text=True, check=False
    )
    if done.returncode != 0:
        raise OracleError(f"lake build failed:\n{done.stdout}{done.stderr}")
    return lean / ".lake" / "build" / "bin" / "oracle", ""
