@surface:skill
Feature: Refresh routine
  One command refreshes a deal end to end: it finds new filings, loads them, extracts the
  comments, runs every check and reports what changed. It never reports success when a
  check has failed.

  Scenario: A refresh loads a new month and reports what changed
    Given "Fixture Trust 2026-F1" is loaded through August 2026
    And its September 2026 10-D and ABS-EE have been filed
    When the analyst refreshes "Fixture Trust 2026-F1"
    Then September 2026 is loaded
    And the report gives the number of loans loaded, comments extracted, items sent to review and alerts raised
    And each number links to the filings it came from

  Scenario: A refresh with nothing new changes nothing
    Given "Fixture Trust 2026-F1" is loaded through September 2026
    And no later filing exists
    When the analyst refreshes "Fixture Trust 2026-F1"
    Then no stored data changes
    And the report says "up to date through September 2026"

  Scenario: Problems are reported first and the refresh is not called a success
    Given the September 2026 tape has 1 quarantined loan and 1 balance roll-forward break
    When the analyst refreshes "Fixture Trust 2026-F1"
    Then the report opens with "1 loan quarantined, 1 roll-forward break"
    And the refresh is marked "completed with problems"

  Scenario: An unreachable source stops the refresh and says how to fix it
    Given EDGAR answers HTTP 403 when the September 2026 ABS-EE is requested
    When the analyst refreshes "Fixture Trust 2026-F1"
    Then the refresh stops with "HTTP 403 from EDGAR: set SEC_USER_AGENT to a string with a contact address"
    And nothing is loaded for September 2026
