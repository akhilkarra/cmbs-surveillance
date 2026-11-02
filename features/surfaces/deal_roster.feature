@approved @surface:skill @surface:cli
Feature: A deal's loans and their special-servicing state
  A PM briefing a committee first asks which loans of a deal are with the special servicer. The
  panel answers for every loan at once, read only from the loan tape's dates and balance (grill
  log Q96), each row citing its tape, and says how current the panel is.

  Background:
    Given "Fixture Trust 2026-F2" reported these loans in September 2026, filed 2026-09-29:
      | loan | property             | ending balance | transfer date | return date | workout strategy |
      | 1    | Harbor Office Tower  | 41,000,000.00  |               |             |                  |
      | 2    | Lakeside Hotel       | 30,000,000.00  | 2025-03-06    |             | 2                |
      | 3    | Mill Creek Retail    | 0.00           |               |             |                  |
      | 4    | Ridge Industrial     | 12,500,000.00  | 2023-01-10    | 2023-09-15  | 8                |
      | 5    | Bayview Apartments   | 9,800,000.00   | 2024-02-01    |             | 8                |
    And loan "3" of "Fixture Trust 2026-F2" last had a balance above zero in July 2026

  Scenario: Every loan is listed with its state, and the loans in special servicing first
    When "pat" asks for the loans of "Fixture Trust 2026-F2"
    Then the roster lists, in this order:
      | loan | state                  | since      |
      | 2    | in special servicing   | 2025-03-06 |
      | 5    | in special servicing   | 2024-02-01 |
      | 4    | returned               | 2023-09-15 |
      | 3    | paid off               | 2026-08    |
      | 1    | never transferred      |            |
    And each row names its property and ending balance and cites the September 2026 ABS-EE accession and the tape fields it read

  Scenario: A workout code that disagrees with the dates is shown, not used
    When "pat" asks for the loans of "Fixture Trust 2026-F2"
    Then loan "5" reads "in special servicing since 2024-02-01; tape workout strategy: resolved (code 8)"

  Scenario: The roster opens with how current the panel is
    Given the latest run that loaded "Fixture Trust 2026-F2" was run 42 on 2026-10-01
    When "pat" asks for the loans of "Fixture Trust 2026-F2"
    Then the roster opens with "Panel current through September 2026 (run 42, 2026-10-01)"

  Scenario: The roster is point in time
    Given "Fixture Trust 2026-F2" reported loan "1" transferred to special servicing on 2026-10-05 in October 2026, filed 2026-10-28
    When "pat" asks for the loans of "Fixture Trust 2026-F2" as of 2026-10-15
    Then loan "1" reads "never transferred"
    And the roster opens with "Panel current through September 2026"

  Scenario: Only the special-servicing loans, when asked
    When "pat" asks for the loans of "Fixture Trust 2026-F2" in special servicing
    Then the roster lists loans "2" and "5" only
