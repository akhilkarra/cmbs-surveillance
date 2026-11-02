@approved @surface:skill @surface:cli
Feature: A committee brief on a deal's special-servicing book
  The practitioner's real task: brief risk committee on the loans of a deal that are with the
  special servicer. `/cmbs-brief` composes one page only from what asking already gives (the
  roster, each loan's facts, its history), cites every line, lists what the panel does not
  know, and changes nothing (grill log Q98).

  Revision: the section also gives the 10-D's appraisal reduction, cited to the 10-D's report
  table, and condenses the history to fit one page: the first and last months the panel holds,
  then the last six months as filed. A real loan can hold eight years of monthly tapes.

  Background:
    Given "Fixture Trust 2026-F2" reported these loans in September 2026, filed 2026-09-29:
      | loan | property            | ending balance | transfer date | return date | workout strategy |
      | 1    | Harbor Office Tower | 41,000,000.00  |               |             |                  |
      | 2    | Lakeside Hotel      | 30,000,000.00  | 2025-03-06    |             | 2                |
      | 4    | Ridge Industrial    | 12,500,000.00  | 2023-01-10    | 2023-09-15  | 8                |
    And loan "2" of "Fixture Trust 2026-F2" has its September 2026 comment extracted
    And the latest run that loaded "Fixture Trust 2026-F2" was run 42 on 2026-10-01

  Scenario: The brief covers exactly the loans in special servicing
    When "pat" asks for the committee brief of "Fixture Trust 2026-F2"
    Then the brief opens with "Panel current through September 2026 (run 42, 2026-10-01)"
    And the brief has one section, for loan "2", Lakeside Hotel
    And the brief notes that loan "4" returned to the master servicer on 2023-09-15 and loan "1" was never transferred

  Scenario: Each loan's section answers the committee's questions, every line cited
    When "pat" asks for the committee brief of "Fixture Trust 2026-F2"
    Then loan "2"'s section gives, each with its citation, the transfer date and reason, the payment status, the latest DSCR and occupancy with their dates, the servicer's stated plan, the borrower's request, the advances outstanding, the appraisal reduction and the history the panel holds
    And every line of the section that states a value cites an ABS-EE field, a 10-D sentence or a 10-D report table

  Scenario: A long history is condensed to one page
    Given loan "2" of "Fixture Trust 2026-F2" was reported in each month from January 2025 to September 2026
    When "pat" asks for the committee brief of "Fixture Trust 2026-F2"
    Then loan "2"'s history says "21 months held, January 2025 to September 2026"
    And it lists April to September 2026 as filed, each citing its own ABS-EE
    And it lists no other month

  Scenario: What the panel does not know is listed, not filled in
    When "pat" asks for the committee brief of "Fixture Trust 2026-F2"
    Then loan "2"'s section ends with "Not in the panel:" followed by "ASER" and "appraised value"
    And no figure in the brief is computed from others

  Scenario: A field awaiting review shows the filer's words, labelled
    Given loan "2"'s workout strategy is waiting for review
    When "pat" asks for the committee brief of "Fixture Trust 2026-F2"
    Then loan "2"'s section says the workout strategy is awaiting human review
    And it quotes the comment's sentence on it, labelled "unreviewed: not a panel fact"
    And it gives the tape's workout strategy "foreclosure (code 2)", labelled as the tape's

  Scenario: The brief changes nothing
    When "pat" asks for the committee brief of "Fixture Trust 2026-F2"
    Then no stored data changes
