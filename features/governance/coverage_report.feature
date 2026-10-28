@approved @layer:data @surface:cli @surface:api @surface:ui
Feature: Coverage report
  For every deal in the book, the report says what the panel can and cannot see, and how much
  of the book's exposure sits in each class. Blind spots are shown, never silently skipped.

  Background:
    Given the book holds:
      | deal            | class | face amount   |
      | BANK 2018-BNK13 | D     | 12,000,000.00 |
      | WFRBS 2014-C25  | E     | 6,000,000.00  |
      | COMM 2014-CR18  | D     | 2,000,000.00  |
      | HYT 2024-RGCY   | E     | 30,000,000.00 |

  @real-data
  Scenario: Each deal gets a coverage class from its filings
    When the coverage report runs on 2026-10-27
    Then the deals are classed:
      | deal            | coverage                   | detail                                      |
      | BANK 2018-BNK13 | full                       | loan tape and distribution reports monthly  |
      | WFRBS 2014-C25  | distribution reports only  | issued before November 2016, no loan tape   |
      | COMM 2014-CR18  | reporting suspended        | Form 15-15D, last 10-D September 2024       |
      | HYT 2024-RGCY   | not publicly reported      | only the depositor's ABS-15G of 2024-08-06  |

  Scenario: Exposure is summed by coverage class
    When the coverage report runs on 2026-10-27
    Then exposure by coverage class is:
      | coverage                  | exposure      | share of book |
      | full                      | 12,000,000.00 | 24%           |
      | distribution reports only | 6,000,000.00  | 12%           |
      | reporting suspended       | 2,000,000.00  | 4%            |
      | not publicly reported     | 30,000,000.00 | 60%           |
    And the report says "60% of the book's exposure cannot be surveilled from public filings"

  Scenario: A deal whose latest filing is late is called out
    Given "BANK 2018-BNK13" has filed every month through September 2026
    And no October 2026 filing has arrived by 2026-11-05
    When the coverage report runs on 2026-11-05
    Then "BANK 2018-BNK13" is marked "latest filing overdue" with "expected by about 2026-10-29"

  Scenario: Adding a vendor source changes a deal's coverage
    Given a vendor loan tape source is configured for "HYT 2024-RGCY"
    When the coverage report runs
    Then "HYT 2024-RGCY" is classed "full, via vendor"
    And the report names the vendor source
