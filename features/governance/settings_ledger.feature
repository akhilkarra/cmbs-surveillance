@draft @layer:logic @surface:api @surface:ui
Feature: Settings ledger
  Every number that changes what the panel says is a setting with a history: alert
  thresholds, the abstention band, the model and prompt versions, the date tolerance for
  cross-checks. A quant proposes a change, a risk officer approves it, and every result
  records which version of the settings produced it.

  Background:
    Given settings version 7 is in force, with the DSCR alert threshold at 1.00x and the abstention band at 0.30 to 0.70

  Scenario: The settings in force are listed with their history
    When "rae" reads the settings
    Then the DSCR alert threshold shows 1.00x, version 7, approved by "rae" on 2026-09-15, proposed by "quinn"

  Scenario: A proposal does not change anything until approved
    When "quinn" proposes the DSCR alert threshold be changed to 1.05x because "sponsors start defaulting above 1.00x in this market"
    Then the DSCR alert threshold in force is still 1.00x
    And the proposal is listed as "awaiting approval" with the reason

  Scenario: An approval creates a new version and takes effect for later runs
    Given "quinn" proposed the DSCR alert threshold be changed to 1.05x
    When "rae" approves the proposal on 2026-10-01
    Then settings version 8 is in force with the DSCR alert threshold at 1.05x
    And alerts built after 2026-10-01 record settings version 8
    And alerts built before 2026-10-01 still record settings version 7

  Scenario: A rejection keeps the old value and the reason for rejecting
    Given "quinn" proposed the abstention band be changed to 0.40 to 0.60
    When "rae" rejects the proposal because "narrower band needs a quality report first"
    Then the abstention band in force is still 0.30 to 0.70
    And the proposal is listed as "rejected" with the reason

  Scenario: A model or prompt change must carry a quality report
    When "quinn" proposes the prompt version be changed from 3 to 4 without a quality report
    Then the proposal is refused with "a model or prompt change needs a quality report"
