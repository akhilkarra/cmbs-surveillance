@draft @layer:data @surface:cli @surface:skill
Feature: Batch refresh and backfill
  One run can refresh every deal in the book, or load a range of past months. A deal and
  month either loads completely or not at all, and two runs never work on the same deal
  at the same time.

  Scenario: Refreshing the book covers every deal that has public filings
    Given the book holds "Fixture Trust 2026-F1", "Fixture Trust 2026-F2" and "HYT 2024-RGCY"
    When "olly" refreshes the book
    Then "Fixture Trust 2026-F1" and "Fixture Trust 2026-F2" are refreshed
    And "HYT 2024-RGCY" is skipped with "not publicly reported"
    And the run summary lists all three deals with their outcome

  Scenario: A backfill loads a range of months in order
    Given "Fixture Trust 2026-F1" has no months loaded
    When "quinn" backfills "Fixture Trust 2026-F1" from August 2025 to September 2026
    Then 14 months are loaded, oldest first
    And each month's alerts are built against the month before it

  Scenario: A month loads completely or not at all
    Given the September 2026 ABS-EE for "Fixture Trust 2026-F1" loads but its 10-D fails to parse
    When the run finishes
    Then September 2026 for "Fixture Trust 2026-F1" is marked "not loaded"
    And no September 2026 loan rows for "Fixture Trust 2026-F1" are stored
    And the run summary says "10-D failed to parse" for that month

  Scenario: A second run on the same deal waits its turn
    Given run 42 is refreshing "Fixture Trust 2026-F1"
    When "quinn" starts a refresh of "Fixture Trust 2026-F1"
    Then the new run is refused with "run 42 is already refreshing Fixture Trust 2026-F1"
    And nothing is loaded twice
