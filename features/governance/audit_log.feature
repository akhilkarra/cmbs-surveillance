@approved @layer:data @surface:api @surface:ui
Feature: Audit log
  Every change to the panel is recorded: who, when, what changed from what to what, and why
  when a reason was required. The log is append-only. Nothing in it can be edited or deleted.

  Scenario: A review decision is recorded with before and after
    Given loan "2"'s borrower request was empty and waiting for review
    When "alex" decides the borrower request is "forbearance", citing sentence 3
    Then the audit log has an entry for loan "2": user "alex", field "borrower request", before "", after "forbearance", reason "sentence 3"

  Scenario: An automated change names the run that made it
    When refresh run 42, started by "olly", loads September 2026 for "Fixture Trust 2026-F1"
    Then every loan row it writes has an audit entry naming run 42 and "olly"

  Scenario: The log can be read by loan, by user and by date
    Given the audit log holds entries for loans "2" and "7" made by "alex" and "olly" during September 2026
    When "rae" reads the audit log for loan "2"
    Then only loan "2"'s entries are returned, newest first
    When "rae" reads the audit log for user "alex" between 2026-09-01 and 2026-09-30
    Then only "alex"'s September 2026 entries are returned

  Scenario: Entries cannot be changed or removed
    Given an audit entry made by "alex" on 2026-09-30
    When "olly" tries to delete that entry
    Then the deletion is refused with "the audit log is append-only"
    And the attempt is itself recorded

  Scenario: Only risk and operators read the whole log, but anyone sees the history of what they can see
    When "pat" opens loan "2"
    Then "pat" sees loan "2"'s change history
    When "pat" tries to read the audit log for user "alex"
    Then the read is refused with "pm may not read the audit log by user"
