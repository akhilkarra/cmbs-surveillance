@draft @layer:data @surface:cli @surface:api @surface:ui
Feature: The book
  The panel knows what the fund holds, so every alert carries the exposure behind it.
  Positions are supplied as a file of deal, class and face amount. Exposure to a loan-level
  alert is the total face the fund holds in that deal. Tranche-level loss attribution is
  out of scope for now.

  Scenario: Positions are loaded from a file
    Given a positions file dated 2026-09-30:
      | deal                   | class | face amount   |
      | Fixture Trust 2026-F1  | E     | 20,000,000.00 |
      | Fixture Trust 2026-F1  | F     | 5,000,000.00  |
      | Fixture Trust 2026-F2  | D     | 40,000,000.00 |
    When "quinn" loads the positions
    Then the book holds 3 positions in 2 deals as of 2026-09-30
    And exposure to "Fixture Trust 2026-F1" is $25,000,000.00

  Scenario: Every alert carries the exposure behind it
    Given the book holds $25,000,000.00 of "Fixture Trust 2026-F1"
    And a high alert "transferred to special servicing" is raised for loan "7" of "Fixture Trust 2026-F1"
    When the alert is read
    Then it shows exposure $25,000,000.00 in classes "E, F"

  Scenario: A deal not in the book still has alerts, with zero exposure
    Given the book holds nothing in "Fixture Trust 2026-F3"
    When the September 2026 alerts for "Fixture Trust 2026-F3" are built
    Then the alerts exist with exposure $0.00
    And they are left out of the book view

  Scenario: Positions are point in time
    Given a positions file dated 2026-08-31 with $10,000,000.00 of "Fixture Trust 2026-F1"
    And a positions file dated 2026-09-30 with $25,000,000.00 of "Fixture Trust 2026-F1"
    When the August 2026 alerts for "Fixture Trust 2026-F1" are read
    Then they show exposure $10,000,000.00
    When the September 2026 alerts are read
    Then they show exposure $25,000,000.00

  Scenario: The same whole loan held through two deals is reported once
    Given "quinn" has linked loan "1" of "Fixture Trust 2026-F1" and loan "3" of "Fixture Trust 2026-F2" as the same whole loan
    And both September 2026 tapes show that loan transferred to special servicing on 2026-09-02
    When the September 2026 alerts are built
    Then one high alert "transferred to special servicing" is raised for the linked loan
    And it lists both deals with combined exposure $65,000,000.00
