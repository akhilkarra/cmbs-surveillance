@layer:data @surface:cli @surface:api @contract:as_of
Feature: Point-in-time availability
  A value can be used only from the moment it became public, which is its filing date.
  Anything that asks "what did we know on date D" must never see data published after D.

  @real-data
  Scenario: A loan tape is invisible before its filing date
    Given the September 2026 ABS-EE for "BANK 2018-BNK13" has reporting period end 2026-09-11 and was filed on 2026-09-29
    When the panel is queried as of 2026-09-20
    Then no September 2026 loan rows are returned
    And the latest period returned is August 2026

  @real-data
  Scenario: A loan tape is visible from its filing date onward
    Given the September 2026 ABS-EE for "BANK 2018-BNK13" was filed on 2026-09-29
    When the panel is queried as of 2026-09-29
    Then the September 2026 loan rows are returned

  Scenario: Before an amendment is filed, the original value is shown
    Given loan "1" of "Fixture Trust 2026-F1" showed DSCR 1.45x in the September 2026 ABS-EE filed on 2026-09-29
    And an ABS-EE/A filed on 2026-10-05 corrects that DSCR to 1.25x
    When the panel is queried as of 2026-10-01
    Then loan "1" shows DSCR 1.45x for September 2026

  Scenario: After an amendment is filed, the corrected value is shown and the original is kept
    Given loan "1" of "Fixture Trust 2026-F1" showed DSCR 1.45x in the September 2026 ABS-EE filed on 2026-09-29
    And an ABS-EE/A filed on 2026-10-05 corrects that DSCR to 1.25x
    When the panel is queried as of 2026-10-06
    Then loan "1" shows DSCR 1.25x for September 2026
    And the DSCR of 1.45x is still stored with its filing date of 2026-09-29

  Scenario: A model input that uses later data is rejected
    Given a model input row for loan "1" is built as of 2026-09-20
    And the row uses the DSCR from the ABS-EE filed on 2026-09-29
    When the row is checked against the as-of contract
    Then the row is rejected with the reason "uses data published after 2026-09-20"
