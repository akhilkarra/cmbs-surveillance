@draft @layer:logic @surface:cli @surface:api
Feature: Text versus tape cross-checks
  When a servicer comment and the loan tape disagree, the disagreement is recorded with both
  values and their dates. Neither source overwrites the other. Transfer dates within two
  monthly periods of each other count as agreeing.

  Scenario: A transfer date in the comment matches the tape
    Given loan "2"'s comment says it transferred to special servicing on 2025-03-06
    And the tape shows a special-servicer transfer date of 2025-03-06 for loan "2"
    When the cross-checks run
    Then no transfer flag is raised for loan "2"

  Scenario: Transfer dates two monthly periods apart count as agreeing
    Given loan "2"'s comment says it transferred to special servicing on 2025-01-20
    And the tape shows a special-servicer transfer date of 2025-03-06 for loan "2"
    When the cross-checks run
    Then no transfer flag is raised for loan "2"

  Scenario: Transfer dates more than two monthly periods apart are flagged
    Given loan "2"'s comment says it transferred to special servicing on 2024-11-15
    And the tape shows a special-servicer transfer date of 2025-03-06 for loan "2"
    When the cross-checks run
    Then loan "2" is flagged "transfer dates differ by more than two periods"
    And the flag shows both dates

  Scenario: A transfer in the comment that the tape does not show is flagged
    Given loan "5"'s September 2026 comment says it transferred to special servicing on 2026-08-15
    And the August and September 2026 tapes show no special-servicer transfer for loan "5"
    When the cross-checks run
    Then loan "5" is flagged "transfer in text, not in tape"
    And the flag shows both sources' values and as-of dates

  Scenario: A comment with newer financials than the tape is noted without changing the tape
    Given loan "2"'s October 2026 comment quotes DSCR 0.64x as of 2026-06-30
    And the October 2026 tape's most recent DSCR for loan "2" is as of 2024-12-31
    When the cross-checks run
    Then loan "2" gets the note "text has newer financials than tape"
    And the tape's DSCR for loan "2" is unchanged
