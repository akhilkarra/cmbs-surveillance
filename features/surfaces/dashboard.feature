@approved @layer:ui @surface:ui
Feature: Surveillance dashboard
  A PM opens one page to see what changed across the book this month, opens a loan, and
  checks any value against its source in one click.

  Background:
    Given the book holds $25,000,000.00 of "Fixture Trust 2026-F1" and $40,000,000.00 of "Fixture Trust 2026-F2"
    And the September 2026 alerts are:
      | deal                  | loan | severity | alert                                                     |
      | Fixture Trust 2026-F1 | 7    | high     | transferred to special servicing                          |
      | Fixture Trust 2026-F1 | 4    | medium   | payment status worsened: current to 30 days               |
      | Fixture Trust 2026-F1 | 5    | medium   | paid-through date did not advance                         |
      | Fixture Trust 2026-F1 | 7    | medium   | DSCR fell below 1.00x: 1.12x to 0.96x                     |
      | Fixture Trust 2026-F1 | 2    | medium   | workout strategy changed: to be determined to full payoff |
      | Fixture Trust 2026-F1 | 2    | low      | servicer comment changed                                  |
      | Fixture Trust 2026-F2 | 3    | medium   | payment status worsened: current to 60 days               |

  Scenario: The book view lists alerts by severity, then exposure, then loan balance
    When "pat" opens the book view for September 2026
    Then the alerts appear in this order:
      | deal                  | loan | severity |
      | Fixture Trust 2026-F1 | 7    | high     |
      | Fixture Trust 2026-F2 | 3    | medium   |
      | Fixture Trust 2026-F1 | 2    | medium   |
      | Fixture Trust 2026-F1 | 5    | medium   |
      | Fixture Trust 2026-F1 | 7    | medium   |
      | Fixture Trust 2026-F1 | 4    | medium   |
      | Fixture Trust 2026-F1 | 2    | low      |
    And each alert shows its exposure and its state

  Scenario: Closed alerts leave the book view
    Given "alex" closed the high alert for loan "7" of "Fixture Trust 2026-F1"
    When "pat" opens the book view for September 2026
    Then 6 alerts are listed and the first is loan "3" of "Fixture Trust 2026-F2"

  Scenario: Opening a loan shows the sentence behind each extracted value
    When "pat" opens loan "2" of "Fixture Trust 2026-F1"
    Then the borrower request "maturity extension" is shown with sentence 3 of the September 2026 comment
    And a link opens the September 2026 10-D on EDGAR

  Scenario: Each value shows what verified it and which run loaded it
    When "pat" opens loan "2" of "Fixture Trust 2026-F1"
    Then the ending balance shows "contract passed", "roll-forward passed" and "run 43"
    And the borrower request shows "auto-accepted" and "evidence checked"

  Scenario: A value awaiting review is visibly marked
    Given loan "2"'s workout strategy is waiting for review
    When "pat" opens loan "2" of "Fixture Trust 2026-F1"
    Then the workout strategy shows "needs review" instead of a value

  Scenario: Financials show their age
    When "pat" opens loan "2" of "Fixture Trust 2026-F1"
    Then the most recent financials show their end date of 2024-12-31 and "619 days old at period end"
