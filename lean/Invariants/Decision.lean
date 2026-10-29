/-!
# The extraction decision table is total

`cmbs.extraction.decision.decide` turns a field's three judgments into one outcome: the answer's
band, the support's band (each low, unsettled or high against the abstention band) and whether
the cited sentence was found word for word in the comment. Its docstring states the rules as a
table whose rows use "any" and lists of bands:

| support    | evidence  | answer         | outcome                                      |
|------------|-----------|----------------|----------------------------------------------|
| low        | any       | any            | not stated                                   |
| unsettled  | not found | any            | review: evidence not found in source         |
| high       | not found | any            | review: evidence not found in source         |
| unsettled  | found     | any            | review: unsettled support                    |
| high       | found     | low, unsettled | review: unsettled answer                     |
| high       | found     | high           | auto-accept                                  |

`table` is that table as data, and `decide` is the rules as Python writes them. The theorems say,
for all 18 combinations (3 support bands × 2 evidence results × 3 answer bands):

* `table_total`: exactly one row of the table matches;
* `decide_follows_table`: `decide` gives that row's outcome and reason;
* `one_outcome`: every combination leads to exactly one of auto-accept, review or not stated;
* `support_low_not_stated`: without a supporting sentence the field is not stated, whatever the
  answer's probability (grill log Q87);
* `auto_accept_needs_all_three`: nothing is auto-accepted unless support and answer are high and
  the evidence was found.

Python's `decide` is compared with this one on every combination, and the table with the
docstring's (`tests/test_lean_oracle.py`).
-/

namespace Invariants.Decision

inductive Band where
  | low
  | unsettled
  | high
deriving DecidableEq, Repr

def Band.all : List Band := [.low, .unsettled, .high]

inductive Outcome where
  | autoAccept
  | review
  | notStated
deriving DecidableEq, Repr

def Outcome.all : List Outcome := [.autoAccept, .review, .notStated]

inductive Reason where
  | evidenceNotFound
  | unsettledSupport
  | unsettledAnswer
deriving DecidableEq, Repr

structure Decision where
  outcome : Outcome
  reason : Option Reason
deriving DecidableEq, Repr

/-- The rules, in the order `cmbs.extraction.decision.decide` tests them. -/
def decide (answer support : Band) (evidenceFound : Bool) : Decision :=
  match support with
  | .low => ⟨.notStated, none⟩
  | _ =>
    if !evidenceFound then ⟨.review, some .evidenceNotFound⟩
    else if support = .unsettled then ⟨.review, some .unsettledSupport⟩
    else if answer ≠ .high then ⟨.review, some .unsettledAnswer⟩
    else ⟨.autoAccept, none⟩

/-- One row of the docstring's table: the values each column accepts, and the result. -/
structure Row where
  support : List Band
  evidence : List Bool
  answer : List Band
  decision : Decision
deriving DecidableEq, Repr

def Row.matches (row : Row) (answer support : Band) (evidenceFound : Bool) : Bool :=
  row.support.contains support && row.evidence.contains evidenceFound &&
    row.answer.contains answer

def table : List Row :=
  [ ⟨[.low], [true, false], Band.all, ⟨.notStated, none⟩⟩,
    ⟨[.unsettled], [false], Band.all, ⟨.review, some .evidenceNotFound⟩⟩,
    ⟨[.high], [false], Band.all, ⟨.review, some .evidenceNotFound⟩⟩,
    ⟨[.unsettled], [true], Band.all, ⟨.review, some .unsettledSupport⟩⟩,
    ⟨[.high], [true], [.low, .unsettled], ⟨.review, some .unsettledAnswer⟩⟩,
    ⟨[.high], [true], [.high], ⟨.autoAccept, none⟩⟩ ]

theorem table_total (answer support : Band) (evidenceFound : Bool) :
    (table.filter (·.matches answer support evidenceFound)).length = 1 := by
  cases answer <;> cases support <;> cases evidenceFound <;> rfl

theorem decide_follows_table (answer support : Band) (evidenceFound : Bool) :
    ∀ row ∈ table, row.matches answer support evidenceFound = true →
      decide answer support evidenceFound = row.decision := by
  cases answer <;> cases support <;> cases evidenceFound <;> simp [table, Row.matches, decide]

theorem one_outcome (answer support : Band) (evidenceFound : Bool) :
    (Outcome.all.filter (· = (decide answer support evidenceFound).outcome)).length = 1 := by
  cases answer <;> cases support <;> cases evidenceFound <;> rfl

/-- A review always says why; auto-accept and not stated never carry a reason. -/
theorem reason_iff_review (answer support : Band) (evidenceFound : Bool) :
    (decide answer support evidenceFound).reason.isSome = true ↔
      (decide answer support evidenceFound).outcome = .review := by
  cases answer <;> cases support <;> cases evidenceFound <;> simp [decide]

theorem support_low_not_stated (answer : Band) (evidenceFound : Bool) :
    decide answer .low evidenceFound = ⟨.notStated, none⟩ := by
  rfl

theorem auto_accept_needs_all_three (answer support : Band) (evidenceFound : Bool)
    (h : (decide answer support evidenceFound).outcome = .autoAccept) :
    answer = .high ∧ support = .high ∧ evidenceFound = true := by
  revert h
  cases answer <;> cases support <;> cases evidenceFound <;> simp [decide]

end Invariants.Decision
