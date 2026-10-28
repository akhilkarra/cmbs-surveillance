@draft @layer:data @surface:cli @surface:api
Feature: Reproducibility and export
  The panel can be rebuilt from the raw filings and come out identical, and a quant can take
  an as-of snapshot away as files that say exactly what produced them.

  Scenario: Rebuilding from raw filings gives the same panel
    Given "Fixture Trust 2026-F1" is loaded from August 2025 to September 2026
    When the panel is rebuilt from the stored raw filings
    Then every loan row is identical to before, field for field
    And the rebuilt panel's checksum matches the original

  Scenario: An as-of export contains only what was known on that date
    When "quinn" exports the panel as of 2026-09-20
    Then the export holds periods through August 2026 and no September 2026 rows
    And the export file names the as-of date, settings version 7, model "jev-1.14.0" and prompt version 3

  Scenario: Values awaiting review or overridden are marked in the export
    Given loan "2"'s workout strategy is waiting for review
    And loan "2"'s borrower request was overridden by "alex"
    When "quinn" exports the panel as of 2026-10-06
    Then loan "2"'s workout strategy is empty with status "needs review"
    And loan "2"'s borrower request is "forbearance" with status "overridden"

  Scenario: Exports are recorded
    When "quinn" exports the panel as of 2026-10-06
    Then the audit log records the export, its as-of date and "quinn"
