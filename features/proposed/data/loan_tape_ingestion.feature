@layer:data @surface:cli @surface:api @contract:loan_period
Feature: Loan tape ingestion
  Each month's ABS-EE loan tape becomes one row per loan per period.
  Every row can be traced to the exact file it came from.

  Background:
    Given the September 2026 ABS-EE for "Fixture Trust 2026-F1", filed on 2026-09-29
    And the tape lists 9 assets: 7 loans, the pari passu companion note "1A" and the portfolio property "2-001"

  Scenario: Each loan becomes one row for the period
    When the tape is ingested
    Then September 2026 has 7 loan rows
    And loan "1" has an ending balance of $50,000,000.00 and is paid through 2026-09-01
    And every loan row records the filing's accession number and the file's SHA-256 hash

  Scenario: Companion notes and portfolio properties are stored but are not loans
    When the tape is ingested
    Then all 9 assets are stored exactly as reported
    And neither "1A" nor "2-001" appears as a loan row

  Scenario: Loading the same filing twice changes nothing
    Given the tape has already been ingested
    When the tape is ingested again
    Then September 2026 still has 7 loan rows
    And no stored value has changed

  Scenario: A loan with an impossible value is quarantined, not dropped
    Given loan "5" reports an ending balance of -$12,000,000.00
    When the tape is ingested
    Then September 2026 has 6 loan rows
    And loan "5" is quarantined with the reason "ending balance is negative"
    And the ingestion summary reports 1 quarantined loan

  Scenario: A missing tag means "not reported", never zero
    Given loan "5" has no most-recent financials tags
    When the tape is ingested
    Then loan "5" has no most-recent NOI, DSCR or financials dates
    And none of those fields is stored as 0
