"""Turning Python's values into the Lean oracle's cases and back, without Lean installed.

The oracle process itself is stood in for by a small Python program speaking the same protocol;
tests/test_lean_oracle.py runs the real one.
"""

import subprocess
import sys
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest

from cmbs.crosscheck.rollforward import Balances, Ending
from cmbs.extraction.decision import Band
from cmbs.invariants import oracle
from cmbs.invariants.partition import PoolLoan
from cmbs.invariants.wac import RatedLoan

D = Decimal


def test_cents_are_exact():
    assert oracle.cents(D("50000000.00")) == 5_000_000_000
    assert oracle.cents(D("-1.25")) == -125
    assert oracle.cents(D("7")) == 700
    assert oracle.cents(None) is None
    with pytest.raises(ValueError, match="0.005 is not a whole number of cents"):
        oracle.cents(D("0.005"))


def test_rates_share_the_scale_of_the_longest():
    assert oracle.rate_scale([D("0.045"), None, D(".07747000"), D("0.03768")]) == 8
    assert oracle.rate_scale([D("5"), D("4.5E+1")]) == 0
    assert oracle.rate_scale([None]) == 0
    assert oracle.scaled(D("0.045"), 5) == 4500
    assert oracle.scaled(None, 5) is None
    with pytest.raises(ValueError, match="0.03768 has more than 4 decimal places"):
        oracle.scaled(D("0.03768"), 4)


def test_a_rollforward_case():
    balances = Balances(
        beginning=D("9050000.00"),
        scheduled_principal=D("40000.00"),
        unscheduled_principal=None,
        other_principal_adjustments=D("0.00"),
        realized_loss=D("0.00"),
        ending=Ending(D("9000000.00")),
    )
    assert oracle.rollforward_case(balances) == {
        "kind": "rollforward",
        "beginning": 905000000,
        "scheduled": 4000000,
        "unscheduled": None,
        "other": 0,
        "realized_loss": 0,
        "ending": 900000000,
    }
    unreadable = Balances(None, None, None, None, None, Ending(None, "not a number"))
    with pytest.raises(ValueError, match="could not be read: not a number"):
        oracle.rollforward_case(unreadable)


def test_a_partition_case_reads_codes_as_python_does():
    loans = [PoolLoan("1", "01", D("1.00")), PoolLoan("2", None, None), PoolLoan("3", " x ", None)]
    assert oracle.partition_case(loans) == {
        "kind": "partition",
        "loans": [
            {"status": "1", "balance": 100},
            {"status": None, "balance": None},
            {"status": "x", "balance": None},
        ],
    }


def test_a_wac_case_and_its_answer():
    loans = [RatedLoan("1", D("10.00"), D("0.045")), RatedLoan("2", None, D("0.0525"))]
    case, scale = oracle.wac_case(loans)
    assert scale == 4
    assert case == {
        "kind": "wac",
        "loans": [{"balance": 1000, "rate": 450}, {"balance": None, "rate": 525}],
    }
    answer = {"num": 450_000, "den": 1000, "lo": 450, "hi": 450}
    assert oracle.lean_wac(answer, scale) == oracle.LeanWac(
        rate=Fraction(45, 1000), lowest=Fraction(45, 1000), highest=Fraction(45, 1000)
    )
    assert oracle.lean_wac(None, scale) is None


def test_a_decision_case():
    assert oracle.decision_case(answer=Band.HIGH, support=Band.LOW, evidence_found=False) == {
        "kind": "decision",
        "answer": "high",
        "support": "low",
        "evidence_found": False,
    }


# A stand-in oracle: answers {"echo": <case>}, an error for kind "bad", and stops on kind "stop".
STAND_IN = """
import json, sys
for line in sys.stdin:
    case = json.loads(line)
    if case["kind"] == "stop":
        break
    answer = {"error": "no"} if case["kind"] == "bad" else {"echo": case}
    print(json.dumps(answer), flush=True)
"""


def test_the_oracle_process_answers_line_by_line():
    with oracle.Oracle([sys.executable, "-c", STAND_IN]) as running:
        assert running.ask({"kind": "table"}) == {"echo": {"kind": "table"}}
        assert running.ask({"kind": "x", "n": 1}) == {"echo": {"kind": "x", "n": 1}}
        with pytest.raises(oracle.OracleError, match="the oracle refused .*bad.*: no"):
            running.ask({"kind": "bad"})
        with pytest.raises(oracle.OracleError, match="the oracle stopped without answering"):
            running.ask({"kind": "stop"})


def test_build_says_why_without_lean(monkeypatch):
    monkeypatch.setattr(oracle.shutil, "which", lambda name: None)
    executable, why = oracle.build()
    assert executable is None
    assert why == "Lean is not installed: no `lake` on PATH (install elan, see CLAUDE.md)"


def test_build_runs_lake_in_the_project_and_reports_a_failure(monkeypatch, tmp_path: Path):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs["cwd"]))
        failed = kwargs["cwd"].name == "broken"
        return subprocess.CompletedProcess(command, 1 if failed else 0, "out\n", "err\n")

    monkeypatch.setattr(oracle.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(oracle.subprocess, "run", run)
    assert oracle.build(tmp_path) == (tmp_path / ".lake" / "build" / "bin" / "oracle", "")
    assert calls == [(["/bin/lake", "build", "--wfail"], tmp_path)]
    broken = tmp_path / "broken"
    with pytest.raises(oracle.OracleError, match="lake build failed:\nout\nerr\n"):
        oracle.build(broken)
