/-!
# The weighted-average coupon lies between the lowest and highest loan rates

`cmbs.invariants.wac` computes

    WAC = Σ (balance × rate) / Σ balance

over the loans that report a positive ending balance and an interest rate. A loan whose balance
or rate is not reported is left out (and listed by Python), never taken as zero; a reported
rate of zero counts as zero.

**Representation.** Lean core has no rationals we rely on, so everything is an integer:
balances in cents, and rates as integers over a common scale chosen by the caller (Python
scales every rate of a pool by the same power of ten, so 0.03768 at scale 10^5 is 3768). The
WAC is the fraction `num / den`, never divided out. `wac` is `none` exactly when no loan has a
positive balance and a rate; otherwise `den > 0`, so the bound

    lo ≤ num / den ≤ hi    is the same statement as    lo × den ≤ num ≤ hi × den,

which is what `wac_bounds` proves, for every pool. `lo` and `hi` are rates of loans in the
average (`lo_is_a_rate`, `hi_is_a_rate`) and bound every one of them.
-/

namespace Invariants.Wac

structure Loan where
  balance : Option Int
  rate : Option Int
deriving Repr

/-- A loan's (balance, rate) when it counts in the average. -/
def weight (l : Loan) : Option (Int × Int) :=
  match l.balance, l.rate with
  | some b, some r => if 0 < b then some (b, r) else none
  | _, _ => none

def weighted (pool : List Loan) : List (Int × Int) :=
  pool.filterMap weight

def num : List (Int × Int) → Int
  | [] => 0
  | (b, r) :: rest => b * r + num rest

def den : List (Int × Int) → Int
  | [] => 0
  | (b, _) :: rest => b + den rest

def lo : List (Int × Int) → Option Int
  | [] => none
  | (_, r) :: rest => some (match lo rest with | none => r | some m => min r m)

def hi : List (Int × Int) → Option Int
  | [] => none
  | (_, r) :: rest => some (match hi rest with | none => r | some m => max r m)

structure Result where
  num : Int
  den : Int
  lo : Int
  hi : Int
deriving DecidableEq, Repr

def wac (pool : List Loan) : Option Result :=
  let w := weighted pool
  match lo w, hi w with
  | some a, some z => some ⟨num w, den w, a, z⟩
  | _, _ => none

theorem weighted_positive (pool : List Loan) : ∀ p ∈ weighted pool, 0 < p.1 := by
  intro p hp
  simp only [weighted, List.mem_filterMap] at hp
  obtain ⟨l, _, hl⟩ := hp
  unfold weight at hl
  split at hl
  · split at hl
    · cases hl
      assumption
    · cases hl
  · cases hl

theorem den_positive : ∀ (w : List (Int × Int)), w ≠ [] → (∀ p ∈ w, 0 < p.1) → 0 < den w
  | [], h, _ => absurd rfl h
  | (b, r) :: rest, _, hpos => by
    have hb : 0 < b := hpos (b, r) (List.mem_cons_self ..)
    have hrest : 0 ≤ den rest := by
      cases rest with
      | nil => simp [den]
      | cons q qs =>
        exact Int.le_of_lt
          (den_positive (q :: qs) (by simp) (fun p hp => hpos p (List.mem_cons_of_mem _ hp)))
    simp [den]
    omega

theorem lo_le : ∀ (w : List (Int × Int)) (m : Int), lo w = some m → ∀ p ∈ w, m ≤ p.2
  | [], _, h, _, _ => by simp [lo] at h
  | (b, r) :: rest, m, h, p, hp => by
    simp only [lo, Option.some.injEq] at h
    cases hrest : lo rest with
    | none =>
      simp only [hrest] at h
      subst h
      cases hp with
      | head => exact Int.le_refl _
      | tail _ hq =>
        cases rest with
        | nil => cases hq
        | cons q qs => simp [lo] at hrest
    | some m' =>
      simp only [hrest] at h
      subst h
      cases hp with
      | head => exact Int.min_le_left _ _
      | tail _ hq => exact Int.le_trans (Int.min_le_right _ _) (lo_le rest m' hrest p hq)

theorem le_hi : ∀ (w : List (Int × Int)) (m : Int), hi w = some m → ∀ p ∈ w, p.2 ≤ m
  | [], _, h, _, _ => by simp [hi] at h
  | (b, r) :: rest, m, h, p, hp => by
    simp only [hi, Option.some.injEq] at h
    cases hrest : hi rest with
    | none =>
      simp only [hrest] at h
      subst h
      cases hp with
      | head => exact Int.le_refl _
      | tail _ hq =>
        cases rest with
        | nil => cases hq
        | cons q qs => simp [hi] at hrest
    | some m' =>
      simp only [hrest] at h
      subst h
      cases hp with
      | head => exact Int.le_max_left _ _
      | tail _ hq => exact Int.le_trans (le_hi rest m' hrest p hq) (Int.le_max_right _ _)

theorem lo_is_a_rate : ∀ (w : List (Int × Int)) (m : Int), lo w = some m → ∃ p ∈ w, p.2 = m
  | [], _, h => by simp [lo] at h
  | (b, r) :: rest, m, h => by
    simp only [lo, Option.some.injEq] at h
    cases hrest : lo rest with
    | none =>
      simp only [hrest] at h
      exact ⟨(b, r), List.mem_cons_self .., h⟩
    | some m' =>
      simp only [hrest] at h
      by_cases hr : r ≤ m'
      · exact ⟨(b, r), List.mem_cons_self .., by rw [← h, Int.min_eq_left hr]⟩
      · obtain ⟨p, hp, hpm⟩ := lo_is_a_rate rest m' hrest
        refine ⟨p, List.mem_cons_of_mem _ hp, ?_⟩
        rw [← h, Int.min_eq_right (by omega), hpm]

theorem hi_is_a_rate : ∀ (w : List (Int × Int)) (m : Int), hi w = some m → ∃ p ∈ w, p.2 = m
  | [], _, h => by simp [hi] at h
  | (b, r) :: rest, m, h => by
    simp only [hi, Option.some.injEq] at h
    cases hrest : hi rest with
    | none =>
      simp only [hrest] at h
      exact ⟨(b, r), List.mem_cons_self .., h⟩
    | some m' =>
      simp only [hrest] at h
      by_cases hr : m' ≤ r
      · exact ⟨(b, r), List.mem_cons_self .., by rw [← h, Int.max_eq_left hr]⟩
      · obtain ⟨p, hp, hpm⟩ := hi_is_a_rate rest m' hrest
        refine ⟨p, List.mem_cons_of_mem _ hp, ?_⟩
        rw [← h, Int.max_eq_right (by omega), hpm]

/-- A lower bound on every rate, weighted by positive balances, bounds the weighted sum. -/
theorem lower : ∀ (w : List (Int × Int)) (m : Int),
    (∀ p ∈ w, 0 < p.1 ∧ m ≤ p.2) → m * den w ≤ num w
  | [], _, _ => by simp [den, num]
  | (b, r) :: rest, m, h => by
    have ⟨hb, hr⟩ := h (b, r) (List.mem_cons_self ..)
    have hrest := lower rest m (fun p hp => h p (List.mem_cons_of_mem _ hp))
    have hbr : m * b ≤ b * r := by
      rw [Int.mul_comm m b]
      exact Int.mul_le_mul_of_nonneg_left hr (Int.le_of_lt hb)
    simp only [den, num, Int.mul_add]
    omega

theorem upper : ∀ (w : List (Int × Int)) (m : Int),
    (∀ p ∈ w, 0 < p.1 ∧ p.2 ≤ m) → num w ≤ m * den w
  | [], _, _ => by simp [den, num]
  | (b, r) :: rest, m, h => by
    have ⟨hb, hr⟩ := h (b, r) (List.mem_cons_self ..)
    have hrest := upper rest m (fun p hp => h p (List.mem_cons_of_mem _ hp))
    have hbr : b * r ≤ m * b := by
      rw [Int.mul_comm m b]
      exact Int.mul_le_mul_of_nonneg_left hr (Int.le_of_lt hb)
    simp only [den, num, Int.mul_add]
    omega

theorem none_iff (pool : List Loan) : wac pool = none ↔ weighted pool = [] := by
  unfold wac
  cases hw : weighted pool with
  | nil => simp [lo]
  | cons p rest => simp [lo, hi]

theorem wac_bounds (pool : List Loan) (r : Result) (h : wac pool = some r) :
    0 < r.den ∧ r.lo * r.den ≤ r.num ∧ r.num ≤ r.hi * r.den := by
  unfold wac at h
  have hpos := weighted_positive pool
  cases hlo : lo (weighted pool) with
  | none => simp [hlo] at h
  | some a =>
    cases hhi : hi (weighted pool) with
    | none => simp [hlo, hhi] at h
    | some z =>
      simp only [hlo, hhi, Option.some.injEq] at h
      subst h
      have hne : weighted pool ≠ [] := by
        intro hnil
        rw [hnil] at hlo
        simp [lo] at hlo
      refine ⟨den_positive _ hne hpos, ?_, ?_⟩
      · exact lower _ a (fun p hp => ⟨hpos p hp, lo_le _ a hlo p hp⟩)
      · exact upper _ z (fun p hp => ⟨hpos p hp, le_hi _ z hhi p hp⟩)

end Invariants.Wac
