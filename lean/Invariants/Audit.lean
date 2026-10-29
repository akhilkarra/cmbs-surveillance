import Lean
import Invariants.Rollforward
import Invariants.Delinquency
import Invariants.Wac
import Invariants.Decision
import Invariants.Theorems

/-!
# Every theorem is proved, with nothing assumed

`#audit` fails the build unless the named theorem rests only on Lean's three standard axioms
(`propext`, `Classical.choice`, `Quot.sound`). A proof left unfinished depends on the axiom Lean
inserts for a missing proof, and a proof by compiled evaluation on an axiom trusting the
compiler; either one stops `lake build`. Every theorem in `Invariants` is listed here (and
`tests/test_lean_sources.py` checks none is missing), so CI's build is the audit.
-/

open Lean Elab Command

/-- The axioms a proof may rest on. -/
def allowedAxioms : List Name := [``propext, ``Classical.choice, ``Quot.sound]

elab "#audit " names:ident* : command => do
  for id in names do
    let name ← liftCoreM <| realizeGlobalConstNoOverloadWithInfo id
    let used ← liftCoreM <| collectAxioms name
    let extra := used.toList.filter (fun ax => !allowedAxioms.contains ax)
    unless extra.isEmpty do
      throwErrorAt id m!"{name} rests on {extra}, beyond {allowedAxioms}"

#audit
  Invariants.balance_rollforward
  Invariants.delinquency_partition
  Invariants.wac_bounds
  Invariants.decision_table_total
  Invariants.Rollforward.loss_month_not_checked
  Invariants.Rollforward.passes_iff
  Invariants.Rollforward.gap_of_checked
  Invariants.Rollforward.breaks_iff
  Invariants.Rollforward.fully_reported
  Invariants.Delinquency.exactly_one
  Invariants.Delinquency.all_distinct
  Invariants.Delinquency.split_cons
  Invariants.Delinquency.adds_up
  Invariants.Delinquency.tally_loans
  Invariants.Delinquency.loans_add_up
  Invariants.Delinquency.balances_add_up
  Invariants.Delinquency.unreported_add_up
  Invariants.Wac.weighted_positive
  Invariants.Wac.den_positive
  Invariants.Wac.lo_le
  Invariants.Wac.le_hi
  Invariants.Wac.lo_is_a_rate
  Invariants.Wac.hi_is_a_rate
  Invariants.Wac.lower
  Invariants.Wac.upper
  Invariants.Wac.none_iff
  Invariants.Wac.wac_bounds
  Invariants.Decision.table_total
  Invariants.Decision.decide_follows_table
  Invariants.Decision.one_outcome
  Invariants.Decision.reason_iff_review
  Invariants.Decision.support_low_not_stated
  Invariants.Decision.auto_accept_needs_all_three
