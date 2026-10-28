@draft @layer:logic @surface:api @surface:ui
Feature: The review queue
  Items waiting for a human are ordered so the ones that matter most come first, and nothing
  waits forever unnoticed. An analyst can decide, decline or leave an item for someone else.

  Background:
    Given these review items are open on 2026-10-06:
      | item | loan | deal                  | exposure      | waiting since |
      | A    | 2    | Fixture Trust 2026-F1 | 25,000,000.00 | 2026-10-01    |
      | B    | 3    | Fixture Trust 2026-F2 | 40,000,000.00 | 2026-10-05    |
      | C    | 5    | Fixture Trust 2026-F1 | 25,000,000.00 | 2026-09-26    |

  Scenario: The queue is ordered by exposure, then by age
    When "alex" opens the review queue
    Then the items appear in the order B, C, A

  Scenario: Items waiting more than five business days are overdue
    When "alex" opens the review queue on 2026-10-06
    Then item C is marked "overdue, waiting 8 days"
    And items A and B are not overdue

  Scenario: An analyst declines an item the comment cannot settle
    When "alex" declines item A because "the comment does not say what was requested"
    Then loan "2"'s borrower request is marked "not determinable"
    And item A leaves the queue with the reason recorded

  Scenario: A decided item leaves the queue for everyone
    When "alex" decides item B
    Then "morgan" no longer sees item B in the queue

  Scenario: The queue summary is visible to risk
    When "rae" reads the review queue summary on 2026-10-06
    Then it shows 3 open items, 1 overdue, and the oldest waiting since 2026-09-26
