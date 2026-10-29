"""The Lean project, read as text: every feature's theorem exists and every theorem is audited.

These run without Lean. CI's `lean` job builds the project, which is where the proofs are
checked and `Audit.lean` fails the build on an unfinished proof; these tests keep the pieces
connected: a `@theorem:<name>` tag with no theorem, or a theorem left out of the audit, fails here.
"""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEAN = ROOT / "lean"
SOURCES = sorted([*LEAN.glob("*.lean"), *(LEAN / "Invariants").glob("*.lean")])
THEOREM = re.compile(r"^theorem\s+(\S+)", re.MULTILINE)
NAMESPACE = re.compile(r"^namespace\s+(\S+)", re.MULTILINE)
TAG = re.compile(r"@theorem:(\w+)")
# Words that would let a proof through unchecked. `Audit.lean` catches them in the build; this
# keeps them out of the source too.
UNCHECKED = re.compile(r"\b(sorry|admit|native_decide)\b")


def theorems() -> set[str]:
    """Every theorem's full name: its namespace (one per file) and its own name."""
    found: set[str] = set()
    for source in SOURCES:
        text = source.read_text()
        spaces = NAMESPACE.findall(text)
        prefix = f"{spaces[0]}." if spaces else ""
        found |= {prefix + name for name in THEOREM.findall(text)}
    return found


def audited() -> set[str]:
    text = (LEAN / "Invariants" / "Audit.lean").read_text()
    block = text[text.index("#audit\n") :]
    return {line.strip() for line in block.splitlines()[1:] if line.strip()}


def test_the_project_is_lean_core_on_a_pinned_toolchain():
    assert (LEAN / "lean-toolchain").read_text() == "leanprover/lean4:v4.35.0\n"
    config = tomllib.loads((LEAN / "lakefile.toml").read_text())
    assert "require" not in config, "Lean core only: no Mathlib or other packages"
    assert config["defaultTargets"] == ["Invariants", "oracle"]


def test_every_theorem_is_audited():
    found = theorems()
    assert len(found) == 33
    assert found == audited()


def test_every_feature_theorem_tag_names_a_theorem():
    tags = {
        tag
        for feature in (ROOT / "features").rglob("*.feature")
        for tag in TAG.findall(feature.read_text())
    }
    assert tags == {
        "balance_rollforward",
        "delinquency_partition",
        "wac_bounds",
        "decision_table_total",
    }
    assert {f"Invariants.{tag}" for tag in tags} <= theorems()


def test_no_proof_is_left_unchecked():
    for source in SOURCES:
        assert not UNCHECKED.search(source.read_text()), source.name
