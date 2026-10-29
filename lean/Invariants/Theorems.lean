import Invariants.Rollforward
import Invariants.Delinquency
import Invariants.Wac
import Invariants.Decision

/-!
# The theorems the feature files name

Each `@theorem:<name>` tag in `features/` names one theorem here, stated for every possible
input; the module each one comes from explains the model and the representation.
`tests/test_lean_sources.py` checks that every tag has its theorem and that `Audit.lean` audits
every theorem.
-/

namespace Invariants

open Rollforward in
/-- `@theorem:balance_rollforward`: a fully reported month without a loss passes exactly when
beginning − scheduled − unscheduled − other adjustments = ending, and the gap is the difference;
in general, a month passes exactly when it is checked and its reported amounts roll, and breaks
exactly when the reported gap is not zero. -/
theorem balance_rollforward :
    (∀ (b s u o e : Int) (loss : Option Int), (loss = none ∨ loss = some 0) →
      let m : Month := ⟨some b, some s, some u, some o, loss, some e⟩
      ((roll m).status = .passed ↔ b - s - u - o = e) ∧
        (roll m).gap = some (b - s - u - o - e)) ∧
    (∀ m : Month, (roll m).status = .passed ↔
      lossMonth m = false ∧ ∃ b e, m.beginning = some b ∧ m.ending = some e ∧ explained b m = e) ∧
    (∀ m : Month, (roll m).status = .broken ↔ ∃ g, (roll m).gap = some g ∧ g ≠ 0) ∧
    (∀ m : Month, lossMonth m = true → (roll m).status = .notChecked) :=
  ⟨fun b s u o e loss h => ⟨(fully_reported b s u o e loss h).1, (fully_reported b s u o e loss h).2.1⟩,
    passes_iff, breaks_iff, loss_month_not_checked⟩

open Delinquency in
/-- `@theorem:delinquency_partition`: every loan lands in exactly one bucket, and the buckets'
loans, balances and loans without a balance add up to the pool's. -/
theorem delinquency_partition (pool : List Loan) :
    (∀ l ∈ pool, (Bucket.all.filter (fun b => decide (bucket l.status = b))).length = 1) ∧
      sumLoans (split pool) = pool.length ∧
      sumBalance (split pool) = (tally pool).balance ∧
      sumUnreported (split pool) = (tally pool).unreported :=
  ⟨fun l _ => exactly_one l, loans_add_up pool, balances_add_up pool, unreported_add_up pool⟩

open Wac in
/-- `@theorem:wac_bounds`: when some loan has a positive balance and a rate, the WAC `num / den`
has `den > 0` and lies between the lowest and the highest of those rates; otherwise there is
no WAC. -/
theorem wac_bounds (pool : List Loan) :
    (∀ r, wac pool = some r → 0 < r.den ∧ r.lo * r.den ≤ r.num ∧ r.num ≤ r.hi * r.den) ∧
      (wac pool = none ↔ weighted pool = []) :=
  ⟨fun r h => Wac.wac_bounds pool r h, none_iff pool⟩

open Decision in
/-- `@theorem:decision_table_total`: every combination of answer band, support band and evidence
matches exactly one row of the docstring's table, `decide` gives that row's outcome, and that
is exactly one of auto-accept, review or not stated. -/
theorem decision_table_total (answer support : Band) (evidenceFound : Bool) :
    (table.filter (·.matches answer support evidenceFound)).length = 1 ∧
      (∀ row ∈ table, row.matches answer support evidenceFound = true →
        Decision.decide answer support evidenceFound = row.decision) ∧
      (Outcome.all.filter (· = (Decision.decide answer support evidenceFound).outcome)).length = 1 :=
  ⟨table_total answer support evidenceFound, decide_follows_table answer support evidenceFound,
    one_outcome answer support evidenceFound⟩

end Invariants
