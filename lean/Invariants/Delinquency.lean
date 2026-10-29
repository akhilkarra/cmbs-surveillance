/-!
# The delinquency partition

`cmbs.invariants.partition` splits a pool into buckets by each loan's Schedule AL payment status
(`paymentStatusLoanCode`), as Python normalizes it ("01" is "1", "b" is "B"):

| code                     | bucket                                  |
|--------------------------|-----------------------------------------|
| 0                        | current                                 |
| A, B                     | grace or late                           |
| 1                        | 30 days                                 |
| 2                        | 60 days                                 |
| 3                        | 90 days or more                         |
| 4                        | performing matured                      |
| 5                        | non-performing matured                  |
| any other                | status not on the code list             |
| none, balance reported   | status not reported                     |
| none, balance none       | not outstanding (no status, no balance) |

A performing matured balloon (4) has its own bucket: the loan is past its maturity date, so it
is not current, but it keeps paying, so it is not delinquent by any number of days either. A loan
with neither a status nor an ending balance is one the tape keeps after it paid off (BANK
2018-BNK13 keeps six, M-026), so it is not counted among outstanding loans missing a status.

Balances are in integer cents; `none` is "not reported" and is counted apart, never as zero.

The theorems say, for every possible pool:

* `bucket_by_code`: each loan's bucket is the one the table above gives it;
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
  | notOutstanding
deriving DecidableEq, Repr

/-- Every bucket, in the order a report lists them. -/
def Bucket.all : List Bucket :=
  [.current, .graceOrLate, .days30, .days60, .days90Plus, .performingMatured,
    .nonPerformingMatured, .unrecognized, .notReported, .notOutstanding]

/-- The Schedule AL payment status codes. -/
def codes : List String := ["0", "A", "B", "1", "2", "3", "4", "5"]

/-- The bucket of a reported, normalized payment status code. -/
def codeBucket : String → Bucket
  | "0" => .current
  | "A" => .graceOrLate
  | "B" => .graceOrLate
  | "1" => .days30
  | "2" => .days60
  | "3" => .days90Plus
  | "4" => .performingMatured
  | "5" => .nonPerformingMatured
  | _ => .unrecognized

structure Loan where
  status : Option String
  balance : Option Int
deriving Repr

def bucket (l : Loan) : Bucket :=
  match l.status, l.balance with
  | some code, _ => codeBucket code
  | none, some _ => .notReported
  | none, none => .notOutstanding

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
  pool.filter (fun l => decide (bucket l = b))

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

/-- Each loan's bucket is the one the Schedule AL table gives it, whatever its balance. -/
theorem bucket_by_code (balance : Option Int) :
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
    bucket ⟨none, none⟩ = .notOutstanding := by
  refine ⟨rfl, rfl, rfl, rfl, rfl, rfl, rfl, rfl, ?_, fun _ => rfl, rfl⟩
  intro code h
  simp only [codes, List.mem_cons, List.not_mem_nil, or_false, not_or] at h
  obtain ⟨h0, hA, hB, h1, h2, h3, h4, h5⟩ := h
  -- codeBucket's last equation needs code to differ from each code: the hypotheses above.
  simp only [bucket, codeBucket]

/-- Adding one loan to a pool adds it to its own bucket's tally and leaves the others alone. -/
theorem split_cons (l : Loan) (pool : List Loan) (b : Bucket) :
    tally (inBucket b (l :: pool)) =
      if bucket l = b then
        let t := tally (inBucket b pool)
        ⟨t.loans + 1, t.balance + l.balance.getD 0,
          t.unreported + (if l.balance.isNone then 1 else 0)⟩
      else tally (inBucket b pool) := by
  by_cases h : bucket l = b <;> simp [inBucket, h, tally]

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
    generalize hk : bucket l = k
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
