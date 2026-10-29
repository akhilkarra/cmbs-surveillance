@approved @layer:data @surface:cli @surface:api @surface:ui
Feature: Run history
  Every refresh and backfill is a run with a number, a starter, a start and end time, what
  it loaded and what went wrong. Operators see runs; everyone can trace a value to its run.

  Scenario: A run records what it did
    When "olly" refreshes the book on 2026-10-27 at 09:00 and it ends at 09:04
    Then the run history shows run 43: "olly", 2026-10-27 09:00 to 09:04, 2 deals refreshed, 1 skipped, 14 loan rows loaded, 3 alerts raised

  Scenario: A failed run says where it stopped
    Given run 44 fails while loading October 2026 for "Fixture Trust 2026-F2"
    When "olly" reads run 44
    Then the run shows "failed", the deal and month it was on, and the error "10-D failed to parse"
    And months loaded before the failure stay loaded

  Scenario: A value traces back to its run
    When "pat" opens loan "7"'s September 2026 balance
    Then it shows "loaded by run 43 on 2026-10-27"

  Scenario: Runs older than a year are kept
    Given run 1 was made on 2025-10-01
    When "olly" reads the run history for October 2025
    Then run 1 is still listed with its full summary
