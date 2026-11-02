@approved @surface:skill @surface:cli
Feature: A loan's history, month by month, when asked
  "Did it fall off a cliff or bleed out?" is answered from the tapes as filed: each month's
  balance, payment status, DSCR, occupancy and financials date, each month citing its own tape.
  Nothing is computed, and a month that did not report a value says so (grill log Q98).

  Background:
    Given loan "2" of "Fixture Trust 2026-F2" was reported in these months:
      | month          | filed      | ending balance | payment status | DSCR | occupancy | financials end |
      | June 2026      | 2026-06-29 | 30,600,000.00  | 0              | 1.31 | 0.91      | 2025-12-31     |
      | July 2026      | 2026-07-29 | 30,400,000.00  | 0              | 1.12 | 0.84      | 2026-03-31     |
      | August 2026    | 2026-08-28 | 30,200,000.00  | A              |      | 0.84      | 2026-03-31     |
      | September 2026 | 2026-09-29 | 30,000,000.00  | 1              | 0.96 | 0.77      | 2026-06-30     |

  Scenario: Each month is listed as filed, with its citation
    When "pat" asks for the history of loan "2" of "Fixture Trust 2026-F2"
    Then the history lists June, July, August and September 2026 in that order
    And September 2026 reads "ending balance $30,000,000.00; payment status 30 days (code 1); DSCR 0.96x; occupancy 77%; financials as of 2026-06-30"
    And each month cites its own ABS-EE accession

  Scenario: A value a month did not report is "not reported", never zero
    When "pat" asks for the history of loan "2" of "Fixture Trust 2026-F2"
    Then August 2026 reads "DSCR not reported"

  Scenario: The history is point in time
    When "pat" asks for the history of loan "2" of "Fixture Trust 2026-F2" as of 2026-09-01
    Then the history ends at August 2026
