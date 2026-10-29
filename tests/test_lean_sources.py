"""The Lean project, read as text: every feature's theorem exists and every theorem is audited.

These run without Lean. CI's `lean` job builds the project, which is where the proofs are
checked and `Audit.lean` fails the build on an unfinished proof; these tests keep the pieces
connected: a `@theorem:<name>` tag with no theorem, or a theorem left out of the audit, fails here.
"""

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LEAN = ROOT / "lean"
SOURCES = sorted(path for path in LEAN.rglob("*.lean") if ".lake" not in path.parts)
# A theorem or lemma declaration, however it is written: indented, after a docstring or
# attributes on the same line, private or protected.
THEOREM = re.compile(
    r"^[ \t]*(?:/--.*?-/[ \t]*)?(?:@\[[^\]]*\][ \t]*)*"
    r"(?:(?:private|protected|noncomputable|nonrec)[ \t]+)*"
    r"(?:theorem|lemma)[ \t]+([^\s:({\[]+)",
    re.MULTILINE,
)
NAMESPACE = re.compile(r"^namespace\s+(\S+)", re.MULTILINE)
TAG = re.compile(r"@theorem:(\w+)")
# Words that would let a proof through unchecked. `Audit.lean` catches the first three in the
# build. It cannot see the last three: `@[implemented_by]` and `@[extern]` make compiled code
# stand in for a proved definition (a theorem about `f n = n` while the oracle runs `n + 1`), and
# `unsafe` code is outside the logic. Keeping them all out of the source means the oracle runs
# only what is proved.
UNCHECKED = re.compile(r"\b(sorry|admit|native_decide|implemented_by|extern|unsafe)\b")


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
    assert len(found) == 34
    assert found == audited()


def test_declarations_are_found_however_they_are_written():
    text = """
theorem plain : True := trivial
  theorem indented : True := trivial
@[simp] theorem with_attribute : True := trivial
@[simp, grind] private theorem hidden (n : Nat) : n = n := rfl
protected lemma guarded: True := trivial
/-- A docstring. -/ theorem documented : True := trivial
-- theorem in_a_comment is not one
def notATheorem := 1
"""
    assert THEOREM.findall(text) == [
        "plain",
        "indented",
        "with_attribute",
        "hidden",
        "guarded",
        "documented",
    ]


@pytest.mark.parametrize(
    "line",
    [
        "theorem t : 1 = 2 := sorry",
        "  admit",
        "theorem t : f 1 = 1 := by native_decide",
        "@[implemented_by fast] def f (n : Nat) : Nat := n",
        '@[extern "lean_f"] def f (n : Nat) : Nat := n',
        "unsafe def f (n : Nat) : Nat := n + 1",
    ],
)
def test_each_unchecked_word_is_caught(line):
    assert UNCHECKED.search(line)


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
