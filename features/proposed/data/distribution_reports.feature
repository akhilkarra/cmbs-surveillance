@layer:data @surface:cli @surface:api
Feature: Distribution reports
  The monthly 10-D distribution report carries what the loan tape lacks: special-servicing
  comments, appraisal reductions and delinquency detail. Each item must attach to the right
  loan and agree with the tape for the same period.

  @real-data
  Scenario: A special-servicing comment attaches to its loan
    Given the September 2026 10-D for "BANK 2018-BNK13", accession 0001888524-26-017704
    When the distribution report is parsed
    Then the Ditson Building loan, asset 11, has a special-servicing comment
    And the comment contains "transferred to Special Servicing effective 6/4/2026 due to monetary default"
    And the comment records which table of the report it was taken from

  @real-data
  Scenario: A report format with no comment column yields no comments and says so
    Given a 10-D for "CGCMT 2016-C1" in the Citi administrator's format
    When the distribution report is parsed
    Then no special-servicing comments are stored
    And the parse summary says "this administrator's format has no comment column"

  Scenario: Appraisal reductions are captured per loan
    Given the September 2026 10-D for "Fixture Trust 2026-F1" lists an appraisal reduction of $6,500,000.00 for loan "2"
    When the distribution report is parsed
    Then loan "2" has an appraisal reduction of $6,500,000.00 for September 2026

  @contract:report_tape_reconciliation
  Scenario: Delinquent loans in the report match the tape
    Given the September 2026 10-D for "Fixture Trust 2026-F1" lists loans "2" and "4" as delinquent
    And the September 2026 ABS-EE shows loan "2" as 90 days or more delinquent and loan "4" as 30 days delinquent
    When the report is reconciled with the tape
    Then the reconciliation passes

  @contract:report_tape_reconciliation
  Scenario: A disagreement between report and tape is flagged, not resolved silently
    Given the September 2026 10-D for "Fixture Trust 2026-F1" lists loan "4" as delinquent
    And the September 2026 ABS-EE shows loan "4" as current
    When the report is reconciled with the tape
    Then loan "4" is flagged "report says delinquent, tape says current"
    And neither the report's value nor the tape's value is changed
