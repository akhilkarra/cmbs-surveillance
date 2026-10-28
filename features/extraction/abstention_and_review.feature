@draft @layer:logic @surface:cli @surface:api
Feature: Abstention and human review
  When the model is unsure, the field goes to a review queue instead of the panel.
  A wrong value that looks confident is worse than a blank marked "needs review".
  Probabilities between 0.30 and 0.70 count as unsettled.

  Scenario: A confident, supported answer goes straight to the panel
    Given the model gives "maturity extension" a probability of 0.93 as loan "2"'s borrower request
    And it gives a probability of 0.90 that sentence 3 supports that answer
    When the decision rules run
    Then the panel records the borrower request "maturity extension"
    And the field is marked "auto-accepted"

  Scenario: An unsettled answer goes to review
    Given the model gives a probability of 0.55 that loan "2"'s comment supports a forbearance request
    When the decision rules run
    Then loan "2"'s borrower request is left empty in the panel
    And a review item shows the comment, the question and the probability 0.55

  Scenario: A confident answer without supporting evidence goes to review
    Given the model gives "foreclosure" a probability of 0.95 as loan "2"'s workout strategy
    And it gives a probability of 0.20 that any sentence supports that answer
    When the decision rules run
    Then loan "2"'s workout strategy goes to review with the reason "no supporting sentence"

  Scenario: A reviewer's decision fills the panel and is attributed
    Given a review item for loan "2"'s borrower request
    When reviewer "analyst" chooses "forbearance" and cites sentence 3
    Then the panel records the borrower request "forbearance" with the reviewer and the review time
    And the field is marked "human-reviewed"

  @theorem:decision_table_total
  Scenario: Every combination of answers has exactly one outcome
    Given the decision rules for the borrower request
    When every combination of answer bands is enumerated
    Then each combination leads to exactly one of "auto-accept", "review" or "not stated"
