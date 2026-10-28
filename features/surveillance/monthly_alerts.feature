@draft @layer:logic @surface:cli @surface:api
Feature: Monthly surveillance alerts
  Each month a PM sees what changed since the month before. Every alert has a severity,
  links to the filing it came from, and records the settings version that produced it.
  The thresholds below are the values in settings version 7.

  Background:
    Given "Fixture Trust 2026-F1" has been ingested for August 2026 and September 2026

  Scenario: A new transfer to special servicing raises a high alert
    Given loan "7" had no special-servicer transfer in August 2026
    And loan "7" shows a transfer date of 2026-09-02 in September 2026
    When the September 2026 alerts are built
    Then a high alert "transferred to special servicing" is raised for loan "7"
    And the alert links to the September 2026 ABS-EE

  Scenario: A worse payment status raises a medium alert
    Given loan "4" was current in August 2026 and 30 days delinquent in September 2026
    When the September 2026 alerts are built
    Then a medium alert "payment status worsened: current to 30 days" is raised for loan "4"

  Scenario: A paid-through date that did not advance raises a medium alert
    Given loan "5" was paid through 2026-08-01 in both August 2026 and September 2026
    When the September 2026 alerts are built
    Then a medium alert "paid-through date did not advance" is raised for loan "5"

  Scenario: A DSCR falling below the threshold raises a medium alert
    Given the DSCR alert threshold is 1.00x in settings version 7
    And loan "7"'s DSCR was 1.12x in August 2026 and 0.96x in September 2026
    When the September 2026 alerts are built
    Then a medium alert "DSCR fell below 1.00x: 1.12x to 0.96x" is raised for loan "7"
    And the alert records settings version 7

  Scenario: A changed workout strategy raises a medium alert
    Given loan "2"'s workout strategy was "to be determined" in August 2026 and "full payoff" in September 2026
    When the September 2026 alerts are built
    Then a medium alert "workout strategy changed: to be determined to full payoff" is raised for loan "2"

  Scenario: A changed servicer comment raises a low alert showing only what is new
    Given loan "2"'s September 2026 comment adds the sentence "The borrower has requested a 12-month maturity extension."
    When the September 2026 alerts are built
    Then a low alert "servicer comment changed" is raised for loan "2"
    And the alert shows only the added sentence

  Scenario: A month with no changes has no alerts
    Given no loan changed between August 2026 and September 2026
    When the September 2026 alerts are built
    Then the alert list is empty and says "no changes since August 2026"
