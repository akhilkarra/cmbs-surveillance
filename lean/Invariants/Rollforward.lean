/-!
# The balance roll-forward

A loan's month, as `cmbs.crosscheck.rollforward.roll` checks it (grill log Q81). Money is in
integer cents; `none` is "not reported", never zero.

    beginning scheduled balance - scheduled principal - unscheduled principal
        - other principal adjustments = ending scheduled balance

A month that reports a nonzero realized loss is not checked (the loss is more than principal),
and neither is a month without a beginning or an ending scheduled balance. A missing deduction
is left out of the sum and listed, never taken as zero: the identity is evaluated on the amounts
reported. The gap is the balance the components explain minus the balance reported.

The theorems say, for every possible month:

* `passes_iff`: the check passes exactly when the month is checked and the reported amounts roll
  to the ending balance;
* `fully_reported`: with every amount reported and no loss, it passes exactly when
  `beginning - scheduled - unscheduled - other = ending`, and the gap is the difference;
* `gap_of_checked` and `breaks_iff`: a checked month reports the gap, and breaks exactly when
  the gap is not zero;
* `loss_month_not_checked`: a realized-loss month is never a break.
-/

namespace Invariants.Rollforward

/-- One loan's month as its tape reports it, in cents. -/
structure Month where
  beginning : Option Int
  scheduled : Option Int
  unscheduled : Option Int
  other : Option Int
  realizedLoss : Option Int
  ending : Option Int
deriving Repr

inductive Status where
  | passed
  | broken
  | notChecked
deriving DecidableEq, Repr

/-- Why a month is not checked. -/
inductive Reason where
  | realizedLoss
  /-- Which of the beginning and the ending scheduled balance are not reported. -/
  | notReported (beginning ending : Bool)
deriving DecidableEq, Repr

/-- The principal deductions, in the order Schedule AL lists them. -/
inductive Deduction where
  | scheduled
  | unscheduled
  | other
deriving DecidableEq, Repr

structure Roll where
  status : Status
  expected : Option Int := none
  gap : Option Int := none
  reason : Option Reason := none
  /-- The deductions the month does not report, in Schedule AL order. -/
  missing : List Deduction := []
deriving Repr

/-- Take a deduction off when it is reported. -/
def less (balance : Int) : Option Int → Int
  | some amount => balance - amount
  | none => balance

/-- The ending balance the reported components explain. -/
def explained (beginning : Int) (m : Month) : Int :=
  less (less (less beginning m.scheduled) m.unscheduled) m.other

def lossMonth (m : Month) : Bool :=
  match m.realizedLoss with
  | some loss => loss != 0
  | none => false

def missing (m : Month) : List Deduction :=
  (if m.scheduled.isNone then [Deduction.scheduled] else []) ++
  (if m.unscheduled.isNone then [Deduction.unscheduled] else []) ++
  (if m.other.isNone then [Deduction.other] else [])

def roll (m : Month) : Roll :=
  if lossMonth m then
    { status := .notChecked, reason := some .realizedLoss }
  else
    match m.beginning, m.ending with
    | some b, some e =>
      let x := explained b m
      { status := if x - e = 0 then .passed else .broken
        expected := some x
        gap := some (x - e)
        missing := missing m }
    | b, e =>
      { status := .notChecked, reason := some (.notReported b.isNone e.isNone) }

theorem loss_month_not_checked (m : Month) (h : lossMonth m = true) :
    (roll m).status = .notChecked := by
  simp [roll, h]

theorem passes_iff (m : Month) :
    (roll m).status = .passed ↔
      lossMonth m = false ∧
        ∃ b e, m.beginning = some b ∧ m.ending = some e ∧ explained b m = e := by
  unfold roll
  cases hl : lossMonth m <;> cases hb : m.beginning <;> cases he : m.ending <;>
    simp
  case false.some.some b e =>
    constructor
    · intro h
      by_cases hx : explained b m - e = 0
      · omega
      · simp [hx] at h
    · intro h
      simp [show explained b m - e = 0 by omega]

theorem gap_of_checked (m : Month) (h : (roll m).status ≠ .notChecked) :
    ∃ b e, m.beginning = some b ∧ m.ending = some e ∧
      (roll m).expected = some (explained b m) ∧ (roll m).gap = some (explained b m - e) := by
  unfold roll at *
  cases hl : lossMonth m <;> cases hb : m.beginning <;> cases he : m.ending <;>
    simp_all

theorem breaks_iff (m : Month) :
    (roll m).status = .broken ↔ ∃ g, (roll m).gap = some g ∧ g ≠ 0 := by
  unfold roll
  cases hl : lossMonth m <;> cases hb : m.beginning <;> cases he : m.ending <;>
    simp

theorem fully_reported (b s u o e : Int) (loss : Option Int) (hloss : loss = none ∨ loss = some 0) :
    let m : Month := ⟨some b, some s, some u, some o, loss, some e⟩
    ((roll m).status = .passed ↔ b - s - u - o = e) ∧ (roll m).gap = some (b - s - u - o - e) ∧
      (roll m).missing = [] := by
  intro m
  have hl : lossMonth m = false := by
    rcases hloss with h | h <;> simp [m, lossMonth, h]
  simp only [roll, hl, m, explained, less, missing]
  by_cases hx : b - s - u - o - e = 0 <;> simp [hx] <;> omega

/-!
## Continuity across months

This month's beginning scheduled balance must equal last month's ending scheduled balance
(Q81). `previous` is last month's tape row: `none` when last month's tape does not show the loan,
`some none` when it shows the loan without an ending scheduled balance. The gap is last month's
ending balance minus this month's beginning balance.
-/

/-- Why continuity is not checked, in the order `cmbs.crosscheck.rollforward.continuity` asks. -/
inductive ContinuityReason where
  | noPreviousRow
  | beginningNotReported
  | previousEndingNotReported
deriving DecidableEq, Repr

structure Continuity where
  status : Status
  previousEnding : Option Int := none
  gap : Option Int := none
  reason : Option ContinuityReason := none
deriving Repr

def continuity (beginning : Option Int) (previous : Option (Option Int)) : Continuity :=
  match previous, beginning with
  | none, _ => { status := .notChecked, reason := some .noPreviousRow }
  | some _, none => { status := .notChecked, reason := some .beginningNotReported }
  | some none, some _ => { status := .notChecked, reason := some .previousEndingNotReported }
  | some (some e), some b =>
    { status := if e - b = 0 then .passed else .broken, previousEnding := some e,
      gap := some (e - b) }

theorem continuity_passes_iff (beginning : Option Int) (previous : Option (Option Int)) :
    (continuity beginning previous).status = .passed ↔
      ∃ b, beginning = some b ∧ previous = some (some b) := by
  unfold continuity
  split <;> simp
  rename_i e b
  constructor
  · intro h
    by_cases hx : e - b = 0
    · omega
    · simp [hx] at h
  · intro h
    simp [show e - b = 0 by omega]

theorem continuity_breaks_iff (beginning : Option Int) (previous : Option (Option Int)) :
    (continuity beginning previous).status = .broken ↔
      ∃ b e, beginning = some b ∧ previous = some (some e) ∧ b ≠ e ∧
        (continuity beginning previous).gap = some (e - b) := by
  unfold continuity
  split <;> simp
  rename_i e b
  by_cases hx : e - b = 0
  · simp [hx]
    omega
  · simp [hx]
    omega

end Invariants.Rollforward
