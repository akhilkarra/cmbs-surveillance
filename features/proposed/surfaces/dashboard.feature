@layer:ui @surface:ui
Feature: Surveillance dashboard
  A PM opens one page to see this month's alerts, opens a loan, and checks any value against
  its source in one click.

  Background:
    Given the September 2026 alerts for "Fixture Trust 2026-F1" are:
      | loan | severity | alert                                                     |
      | 7    | high     | transferred to special servicing                          |
      | 4    | medium   | payment status worsened: current to 30 days               |
      | 5    | medium   | paid-through date did not advance                         |
      | 7    | medium   | DSCR fell below 1.00x: 1.12x to 0.96x                     |
      | 2    | medium   | workout strategy changed: to be determined to full payoff |
      | 2    | low      | servicer comment changed                                  |

  Scenario: Alerts are listed most severe first, then by loan balance
    When the PM opens the dashboard for September 2026
    Then the alerts appear in this order: loan "7" high, loan "2" medium, loan "5" medium, loan "7" medium, loan "4" medium, loan "2" low

  Scenario: Opening a loan shows the sentence behind each extracted value
    When the PM opens loan "2"
    Then the borrower request "maturity extension" is shown with sentence 3 of the September 2026 comment
    And a link opens the September 2026 10-D on EDGAR

  Scenario: Each value shows what verified it
    When the PM opens loan "2"
    Then the ending balance shows "contract passed" and "roll-forward passed"
    And the borrower request shows "auto-accepted" and "evidence checked"

  Scenario: A value awaiting review is visibly marked
    Given loan "2"'s workout strategy is waiting for review
    When the PM opens loan "2"
    Then the workout strategy shows "needs review" instead of a value

  Scenario: Financials show their age
    When the PM opens loan "2"
    Then the most recent financials show their end date of 2024-12-31 and "619 days old at period end"
