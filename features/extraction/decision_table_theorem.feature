@approved @layer:logic
Feature: The decision table is total
  Split from abstention_and_review.feature so the rest of that feature can be approved before
  this scenario's Lean proof exists (grill log Q4: Lean 4 proofs come later). The decision table
  is already checked over every combination in tests/test_extraction_decision.py.

  @theorem:decision_table_total
  Scenario: Every combination of answers has exactly one outcome
    Given the decision rules for the borrower request
    When every combination of answer bands is enumerated
    Then each combination leads to exactly one of "auto-accept", "review" or "not stated"
