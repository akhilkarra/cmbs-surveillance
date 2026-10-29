import Lean.Data.Json
import Invariants

/-!
# The oracle

Runs the Lean definitions the theorems are about on cases from Python, so the Python
implementations can be compared with them (`tests/test_lean_oracle.py`, grill log Q4). It reads
one JSON object per line on stdin and writes one JSON value per line on stdout, flushing after
each, until stdin ends. Money is in integer cents, rates are integers over the caller's scale,
and `null` is "not reported".

    {"kind": "rollforward", "beginning": 5010000000, "scheduled": 10000000, "unscheduled": 0,
     "other": 0, "realized_loss": 0, "ending": 5000000000}
    {"kind": "partition", "loans": [{"status": "0", "balance": 5000000000}]}
    {"kind": "wac", "loans": [{"balance": 5000000000, "rate": 450}]}
    {"kind": "decision", "answer": "high", "support": "high", "evidence_found": true}
    {"kind": "table"}

A line that cannot be read gets `{"error": "..."}` and the oracle carries on.
-/

open Lean (Json)

namespace Oracle

def optInt (j : Json) (key : String) : Except String (Option Int) :=
  match j.getObjVal? key with
  | .error _ => .ok none
  | .ok Json.null => .ok none
  | .ok v => some <$> v.getInt?

def optStr (j : Json) (key : String) : Except String (Option String) :=
  match j.getObjVal? key with
  | .error _ => .ok none
  | .ok Json.null => .ok none
  | .ok v => some <$> v.getStr?

def intJson : Option Int → Json
  | some n => Json.num (Lean.JsonNumber.fromInt n)
  | none => Json.null

def strJson : Option String → Json
  | some s => Json.str s
  | none => Json.null

section Rollforward
open Invariants.Rollforward

def statusName : Status → String
  | .passed => "passed"
  | .broken => "break"
  | .notChecked => "not checked"

def rollReason : Reason → String
  | .realizedLoss => "realized loss"
  | .notReported b e =>
    "not reported: " ++ ", ".intercalate
      ((if b then ["beginning"] else []) ++ (if e then ["ending"] else []))

def deductionName : Deduction → String
  | .scheduled => "scheduled principal"
  | .unscheduled => "unscheduled principal"
  | .other => "other principal adjustments"

def runRollforward (j : Json) : Except String Json := do
  let m : Month := {
    beginning := ← optInt j "beginning"
    scheduled := ← optInt j "scheduled"
    unscheduled := ← optInt j "unscheduled"
    other := ← optInt j "other"
    realizedLoss := ← optInt j "realized_loss"
    ending := ← optInt j "ending" }
  let r := roll m
  return Json.mkObj [
    ("status", Json.str (statusName r.status)),
    ("expected", intJson r.expected),
    ("gap", intJson r.gap),
    ("reason", strJson (r.reason.map rollReason)),
    ("missing", Json.arr (r.missing.map (Json.str ∘ deductionName)).toArray)]

end Rollforward

section Delinquency
open Invariants.Delinquency

def bucketName : Bucket → String
  | .current => "current"
  | .graceOrLate => "grace or late"
  | .days30 => "30 days"
  | .days60 => "60 days"
  | .days90Plus => "90 days or more"
  | .performingMatured => "performing matured"
  | .nonPerformingMatured => "non-performing matured"
  | .unrecognized => "status not on the code list"
  | .notReported => "status not reported"

def tallyJson (t : Tally) : List (String × Json) :=
  [("loans", Json.num t.loans), ("balance", intJson (some t.balance)),
    ("unreported", Json.num t.unreported)]

def runPartition (j : Json) : Except String Json := do
  let loans ← (← j.getObjValAs? (Array Json) "loans").toList.mapM fun l => do
    pure ({ status := ← optStr l "status", balance := ← optInt l "balance" } : Loan)
  let buckets := (split loans).map fun (b, t) =>
    Json.mkObj (("bucket", Json.str (bucketName b)) :: tallyJson t)
  return Json.mkObj [
    ("buckets", Json.arr buckets.toArray),
    ("pool", Json.mkObj (tallyJson (tally loans)))]

end Delinquency

section Wac
open Invariants.Wac

def runWac (j : Json) : Except String Json := do
  let loans ← (← j.getObjValAs? (Array Json) "loans").toList.mapM fun l => do
    pure ({ balance := ← optInt l "balance", rate := ← optInt l "rate" } : Loan)
  return match wac loans with
    | some r => Json.mkObj [
        ("num", intJson (some r.num)), ("den", intJson (some r.den)),
        ("lo", intJson (some r.lo)), ("hi", intJson (some r.hi))]
    | none => Json.null

end Wac

section Decision
open Invariants.Decision

def bandOf : String → Except String Band
  | "low" => .ok .low
  | "unsettled" => .ok .unsettled
  | "high" => .ok .high
  | other => .error s!"not a band: {other}"

def bandName : Band → String
  | .low => "low"
  | .unsettled => "unsettled"
  | .high => "high"

def outcomeName : Outcome → String
  | .autoAccept => "auto-accept"
  | .review => "review"
  | .notStated => "not stated"

def decisionReason : Reason → String
  | .noSupportingSentence => "no supporting sentence"
  | .evidenceNotFound => "evidence not found in source"
  | .unsettledSupport => "unsettled support"
  | .unsettledAnswer => "unsettled answer"

def decisionJson (d : Invariants.Decision.Decision) : List (String × Json) :=
  [("outcome", Json.str (outcomeName d.outcome)),
    ("reason", strJson (d.reason.map decisionReason))]

def runDecision (j : Json) : Except String Json := do
  let answer ← bandOf (← j.getObjValAs? String "answer")
  let support ← bandOf (← j.getObjValAs? String "support")
  let found ← j.getObjValAs? Bool "evidence_found"
  return Json.mkObj (decisionJson (Invariants.Decision.decide answer support found))

def tableRows : Json :=
  Json.arr <| (table.map fun (row : Row) => Json.mkObj ([
    ("support", Json.arr (row.support.map (Json.str ∘ bandName)).toArray),
    ("evidence_found", Json.arr (row.evidence.map Json.bool).toArray),
    ("answer", Json.arr (row.answer.map (Json.str ∘ bandName)).toArray)] ++
    decisionJson row.decision)).toArray

end Decision

def answer (line : String) : Json :=
  let result : Except String Json := do
    let j ← Json.parse line
    match ← j.getObjValAs? String "kind" with
    | "rollforward" => runRollforward j
    | "partition" => runPartition j
    | "wac" => runWac j
    | "decision" => runDecision j
    | "table" => pure tableRows
    | other => throw s!"unknown kind: {other}"
  match result with
  | .ok j => j
  | .error e => Json.mkObj [("error", Json.str e)]

partial def loop (stdin : IO.FS.Stream) (stdout : IO.FS.Stream) : IO Unit := do
  let line ← stdin.getLine
  if line.isEmpty then return
  let text := line.trimAscii.toString
  unless text.isEmpty do
    stdout.putStrLn (answer text).compress
    stdout.flush
  loop stdin stdout

end Oracle

def main : IO Unit := do
  Oracle.loop (← IO.getStdin) (← IO.getStdout)
