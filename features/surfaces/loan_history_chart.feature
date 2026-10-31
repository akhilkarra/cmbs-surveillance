@draft @layer:ui @surface:ui @surface:api
Feature: A loan's history as a chart
  A PM opening a loan sees how it got here: its balance, debt service coverage and payment status
  month by month, as each month was published, with the months a value was not reported left as
  gaps, never drawn as zero.

  Background:
    Given loan "2" of "Fixture Trust 2026-F1" was reported in these months:
      | month          | filed      | ending balance | DSCR | payment status |
      | July 2026      | 2026-07-29 | 30,400,000.00  | 1.12 | 0              |
      | August 2026    | 2026-08-29 | 30,200,000.00  |      | A              |
      | September 2026 | 2026-09-29 | 30,000,000.00  | 0.96 | 1              |

  Scenario: The loan view charts each month as it was published
    When "pat" opens loan "2" of "Fixture Trust 2026-F1"
    Then the history chart shows ending balance 30,400,000.00, 30,200,000.00 and 30,000,000.00 for July, August and September 2026
    And the DSCR line shows 1.12x for July and 0.96x for September with a gap for August
    And each month's point names the filing it came from

  Scenario: A month published later is not on the chart as of an earlier date
    When "pat" opens loan "2" of "Fixture Trust 2026-F1" as of 2026-09-01
    Then the history chart ends at August 2026

  Scenario: Payment status changes are marked on the chart
    When "pat" opens loan "2" of "Fixture Trust 2026-F1"
    Then the chart marks August 2026 "grace or late" and September 2026 "30 days"

  Scenario: The chart credits its library
    When "pat" opens loan "2" of "Fixture Trust 2026-F1"
    Then the chart shows the TradingView attribution its licence requires
