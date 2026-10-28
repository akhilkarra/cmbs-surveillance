@draft @layer:logic @surface:api @surface:ui
Feature: Alert lifecycle
  Alerts are shared. Each one is new until someone acknowledges it, can be assigned to a
  user, and is closed with a note. Everyone sees the same state, and every step is audited.

  Background:
    Given a high alert "transferred to special servicing" for loan "7" of "Fixture Trust 2026-F1", raised for September 2026

  Scenario: A new alert is acknowledged by a PM
    When "pat" acknowledges the alert
    Then the alert's state is "acknowledged by pat on 2026-09-30"
    And "alex" sees the same state

  Scenario: An alert is assigned to an analyst
    Given "pat" has acknowledged the alert
    When "pat" assigns it to "alex" with the note "check the operating statement"
    Then the alert shows "assigned to alex" and the note
    And "alex"'s work list includes the alert

  Scenario: An alert is closed with a note
    Given the alert is assigned to "alex"
    When "alex" closes it with the note "sponsor funded the shortfall, see October comment"
    Then the alert's state is "closed by alex" with the note
    And it no longer appears in the open alerts

  Scenario: A closed alert does not reopen, but the next month's change is a new alert
    Given the alert was closed in September 2026
    And loan "7"'s workout strategy changes in October 2026
    When the October 2026 alerts are built
    Then a new medium alert "workout strategy changed" is raised for loan "7"
    And the September 2026 alert stays closed

  Scenario: Only the roles that may act on alerts can change their state
    When "quinn" tries to close the alert
    Then the action is refused with "quant may not close alerts"
