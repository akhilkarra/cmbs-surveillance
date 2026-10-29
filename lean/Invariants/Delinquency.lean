/-!
# The delinquency partition

`cmbs.invariants.partition` splits a pool into buckets by each loan's Schedule AL payment status
(`paymentStatusLoanCode`), as Python normalizes it ("01" is "1", "b" is "B"):

| code        | bucket                      |
|-------------|-----------------------------|
| 0           | current                     |
| A, B        | grace or late               |
| 1           | 30 days                     |
| 2           | 60 days                     |
| 3           | 90 days or more             |
| 4           | performing matured          |
| 5           | non-performing matured      |
| any other   | status not on the code list |
| none        | status not reported         |

A performing matured balloon (4) has its own bucket: the loan is past its maturity date, so it
is not current, but it keeps paying, so it is not delinquent by any number of days either.

Balances are in integer cents; `none` is "not reported" and is counted apart, never as zero.

The theorems say, for every possible pool:

* `exactly_one`: every loan lands in exactly one bucket of `Bucket.all`;
* `loans_add_up`, `balances_add_up`, `unreported_add_up`: the buckets' loan counts, balances
  and counts of loans without a balance add up to the pool's.
-/

namespace Invariants.Delinquency

inductive Bucket where
  | current
  | graceOrLate
  | days30
  | days60
  | days90Plus
  | performingMatured
  | nonPerformingMatured
  | unrecognized
  | notReported
deriving DecidableEq, Repr

/-- Every bucket, in the order a report lists them. -/
def Bucket.all : List Bucket :=
  [.current, .graceOrLate, .days30, .days60, .days90Plus, .performingMatured,
    .nonPerformingMatured, .unrecognized, .notReported]

/-- The bucket of a normalized payment status code; `none` is "not reported". -/
def bucket : Option String → Bucket
  | none => .notReported
  | some "0" => .current
  | some "A" => .graceOrLate
  | some "B" => .graceOrLate
  | some "1" => .days30
  | some "2" => .days60
  | some "3" => .days90Plus
  | some "4" => .performingMatured
  | some "5" => .nonPerformingMatured
  | some _ => .unrecognized

structure Loan where
  status : Option String
  balance : Option Int
deriving Repr

/-- A bucket's (or the pool's) loans, reported balance and loans without a balance. -/
structure Tally where
  loans : Nat
  balance : Int
  unreported : Nat
deriving DecidableEq, Repr

def tally : List Loan → Tally
  | [] => ⟨0, 0, 0⟩
  | l :: rest =>
    let t := tally rest
    ⟨t.loans + 1, t.balance + l.balance.getD 0, t.unreported + (if l.balance.isNone then 1 else 0)⟩

def inBucket (b : Bucket) (pool : List Loan) : List Loan :=
  pool.filter (fun l => decide (bucket l.status = b))

/-- The partition: each bucket with its tally, in `Bucket.all` order. -/
def split (pool : List Loan) : List (Bucket × Tally) :=
  Bucket.all.map (fun b => (b, tally (inBucket b pool)))

def sumLoans : List (Bucket × Tally) → Nat
  | [] => 0
  | (_, t) :: rest => t.loans + sumLoans rest

def sumBalance : List (Bucket × Tally) → Int
  | [] => 0
  | (_, t) :: rest => t.balance + sumBalance rest

def sumUnreported : List (Bucket × Tally) → Nat
  | [] => 0
  | (_, t) :: rest => t.unreported + sumUnreported rest

theorem exactly_one (l : Loan) :
    (Bucket.all.filter (fun b => decide (bucket l.status = b))).length = 1 := by
  generalize bucket l.status = b
  cases b <;> decide

theorem all_distinct : Bucket.all.Nodup := by
  decide

/-- Adding one loan to a pool adds it to its own bucket's tally and leaves the others alone. -/
theorem split_cons (l : Loan) (pool : List Loan) (b : Bucket) :
    tally (inBucket b (l :: pool)) =
      if bucket l.status = b then
        let t := tally (inBucket b pool)
        ⟨t.loans + 1, t.balance + l.balance.getD 0,
          t.unreported + (if l.balance.isNone then 1 else 0)⟩
      else tally (inBucket b pool) := by
  by_cases h : bucket l.status = b <;> simp [inBucket, h, tally]

theorem adds_up (pool : List Loan) :
    sumLoans (split pool) = (tally pool).loans ∧
      sumBalance (split pool) = (tally pool).balance ∧
      sumUnreported (split pool) = (tally pool).unreported := by
  induction pool with
  | nil => decide
  | cons l rest ih =>
    obtain ⟨hl, hb, hu⟩ := ih
    simp only [split, Bucket.all, List.map, sumLoans, sumBalance, sumUnreported] at hl hb hu ⊢
    simp only [split_cons]
    generalize hk : bucket l.status = k
    cases k <;> simp [tally] <;> omega

theorem tally_loans (pool : List Loan) : (tally pool).loans = pool.length := by
  induction pool with
  | nil => rfl
  | cons l rest ih => simp [tally, ih]

theorem loans_add_up (pool : List Loan) : sumLoans (split pool) = pool.length :=
  (adds_up pool).1.trans (tally_loans pool)

theorem balances_add_up (pool : List Loan) : sumBalance (split pool) = (tally pool).balance :=
  (adds_up pool).2.1

theorem unreported_add_up (pool : List Loan) :
    sumUnreported (split pool) = (tally pool).unreported :=
  (adds_up pool).2.2

end Invariants.Delinquency
