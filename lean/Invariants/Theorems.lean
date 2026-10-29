import Invariants.Rollforward
import Invariants.Delinquency
import Invariants.Wac
import Invariants.Decision

/-!
# The theorems the feature files name

Each `@theorem:<name>` tag in `features/` names one theorem here, stated for every possible
input; the module each one comes from explains the model and the representation.
`tests/test_lean_sources.py` checks that every tag has its theorem and that `Audit.lean` audits
every theorem. Each headline states only what the model could get wrong: nothing here holds for
an arbitrary function in place of the one Python implements.
-/

namespace Invariants

open Rollforward in
/-- `@theorem:balance_rollforward` (grill log Q81):
* a fully reported month without a loss passes exactly when beginning − scheduled − unscheduled
  − other adjustments = ending, its gap is the difference, and it lists nothing as missing;
* in general a month passes exactly when it has no loss, reports both balances and its reported
  amounts roll, and breaks exactly when the reported gap is not zero;
* a realized-loss month is never a break;
* across months, the check passes exactly when this month's beginning scheduled balance equals
  last month's ending one, and breaks exactly when both are reported and differ, with the gap
  last month's ending minus this month's beginning. -/
theorem balance_rollforward :
    (∀ (b s u o e : Int) (loss : Option Int), (loss = none ∨ loss = some 0) →
      let m : Month := ⟨some b, some s, some u, some o, loss, some e⟩
      ((roll m).status = .passed ↔ b - s - u - o = e) ∧
        (roll m).gap = some (b - s - u - o - e) ∧ (roll m).missing = []) ∧
    (∀ m : Month, (roll m).status = .passed ↔
      lossMonth m = false ∧ ∃ b e, m.beginning = some b ∧ m.ending = some e ∧ explained b m = e) ∧
    (∀ m : Month, (roll m).status = .broken ↔ ∃ g, (roll m).gap = some g ∧ g ≠ 0) ∧
    (∀ m : Month, lossMonth m = true → (roll m).status = .notChecked) ∧
    (∀ (beginning : Option Int) (previous : Option (Option Int)),
      ((continuity beginning previous).status = .passed ↔
        ∃ b, beginning = some b ∧ previous = some (some b)) ∧
      ((continuity beginning previous).status = .broken ↔
        ∃ b e, beginning = some b ∧ previous = some (some e) ∧ b ≠ e ∧
          (continuity beginning previous).gap = some (e - b))) :=
  ⟨fun b s u o e loss h => fully_reported b s u o e loss h, passes_iff, breaks_iff,
    loss_month_not_checked,
    fun beginning previous =>
      ⟨continuity_passes_iff beginning previous, continuity_breaks_iff beginning previous⟩⟩

open Delinquency in
/-- `@theorem:delinquency_partition`: each loan's bucket is the one the Schedule AL code table
gives it (codes 0, A, B, 1 to 5; any other code "status not on the code list"; no status with a
balance "status not reported"; neither "not outstanding"), and the buckets' loans, balances and
loans without a balance add up to the pool's. -/
theorem delinquency_partition (pool : List Loan) :
    (∀ balance : Option Int,
      bucket ⟨some "0", balance⟩ = .current ∧
      bucket ⟨some "A", balance⟩ = .graceOrLate ∧
      bucket ⟨some "B", balance⟩ = .graceOrLate ∧
      bucket ⟨some "1", balance⟩ = .days30 ∧
      bucket ⟨some "2", balance⟩ = .days60 ∧
      bucket ⟨some "3", balance⟩ = .days90Plus ∧
      bucket ⟨some "4", balance⟩ = .performingMatured ∧
      bucket ⟨some "5", balance⟩ = .nonPerformingMatured ∧
      (∀ code, code ∉ codes → bucket ⟨some code, balance⟩ = .unrecognized) ∧
      (∀ amount, bucket ⟨none, some amount⟩ = .notReported) ∧
      bucket ⟨none, none⟩ = .notOutstanding) ∧
    sumLoans (split pool) = pool.length ∧
    sumBalance (split pool) = (tally pool).balance ∧
    sumUnreported (split pool) = (tally pool).unreported :=
  ⟨bucket_by_code, loans_add_up pool, balances_add_up pool, unreported_add_up pool⟩

open Wac in
/-- `@theorem:wac_bounds`: the loans averaged are exactly those with a positive balance and a
rate from 0 to the cap; when there are any, the WAC `num / den` has `den > 0` and lies between
the lowest and the highest rate of the loans it averages, both of which are rates of averaged
loans and bound every one of them; when there are none, there is no WAC. -/
theorem wac_bounds (cap : Int) (pool : List Loan) :
    (∀ p, p ∈ weighted cap pool ↔ ∃ l ∈ pool, l.balance = some p.1 ∧ l.rate = some p.2 ∧
      0 < p.1 ∧ 0 ≤ p.2 ∧ p.2 ≤ cap) ∧
    (∀ r, wac cap pool = some r →
      0 < r.den ∧ r.lo * r.den ≤ r.num ∧ r.num ≤ r.hi * r.den ∧
      (∃ p ∈ weighted cap pool, p.2 = r.lo) ∧ (∃ p ∈ weighted cap pool, p.2 = r.hi) ∧
      (∀ p ∈ weighted cap pool, r.lo ≤ p.2 ∧ p.2 ≤ r.hi)) ∧
    (wac cap pool = none ↔ weighted cap pool = []) := by
  refine ⟨mem_weighted cap pool, fun r h => ?_, none_iff cap pool⟩
  obtain ⟨hden, hlo, hhi, hl, hh⟩ := Wac.wac_bounds cap pool r h
  exact ⟨hden, hlo, hhi, lo_is_a_rate _ _ hl, hi_is_a_rate _ _ hh,
    fun p hp => ⟨lo_le _ _ hl p hp, le_hi _ _ hh p hp⟩⟩

open Decision in
/-- `@theorem:decision_table_total`: every combination of answer band, support band and evidence
matches exactly one row of decision.py's docstring table, and `decide` gives that row's outcome
and reason. Without a supporting sentence the field is not stated (Q87), and nothing is
auto-accepted unless support and answer are high and the evidence was found. -/
theorem decision_table_total (answer support : Band) (evidenceFound : Bool) :
    (table.filter (·.matches answer support evidenceFound)).length = 1 ∧
      (∀ row ∈ table, row.matches answer support evidenceFound = true →
        Decision.decide answer support evidenceFound = row.decision) ∧
      Decision.decide answer .low evidenceFound = ⟨.notStated, none⟩ ∧
      ((Decision.decide answer support evidenceFound).outcome = .autoAccept →
        answer = .high ∧ support = .high ∧ evidenceFound = true) :=
  ⟨table_total answer support evidenceFound, decide_follows_table answer support evidenceFound,
    support_low_not_stated answer evidenceFound,
    auto_accept_needs_all_three answer support evidenceFound⟩

end Invariants
