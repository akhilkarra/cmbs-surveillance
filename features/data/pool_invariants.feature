@approved @layer:data @surface:cli @surface:api
Feature: Pool invariants
  Three identities must hold for every deal and every period. Each one is proved in Lean
  for all possible pools, so when a check fails on real data, the data is wrong, not the math.

  @theorem:balance_rollforward @contract:balance_rollforward
  Scenario: A loan's balance rolls forward within a period
    Given loan "1" of "Fixture Trust 2026-F1" began September 2026 with a balance of $50,100,000.00
    And it paid $100,000.00 of scheduled principal and $0.00 of unscheduled principal
    And it had $0.00 of other principal adjustments and $0.00 of realized loss
    And the tape reports an ending balance of $50,000,000.00
    When the September 2026 period is checked
    Then the balance roll-forward check passes for loan "1"

  @theorem:balance_rollforward @contract:balance_rollforward
  Scenario: A roll-forward break is flagged with the size of the gap
    Given loan "7" of "Fixture Trust 2026-F1" began September 2026 with a balance of $9,050,000.00
    And it paid $40,000.00 of scheduled principal and $0.00 of unscheduled principal
    And it had $0.00 of other principal adjustments and $0.00 of realized loss
    And the tape reports an ending balance of $9,000,000.00
    When the September 2026 period is checked
    Then loan "7" is flagged "balance does not roll forward" with a gap of $10,000.00

  @theorem:delinquency_partition
  Scenario: Delinquency buckets cover every loan exactly once
    Given the September 2026 pool of "Fixture Trust 2026-F1" is:
      | loan | payment status | ending balance |
      | 1    | 0              | 50,000,000.00  |
      | 2    | 3              | 30,000,000.00  |
      | 3    | 0              | 8,000,000.00   |
      | 4    | 1              | 4,000,000.00   |
      | 5    | B              | 12,000,000.00  |
      | 7    | not reported   | 9,000,000.00   |
    When the pool is split into delinquency buckets
    Then the buckets are:
      | bucket                                  | loans | balance       |
      | current                                 | 2     | 58,000,000.00 |
      | grace or late                           | 1     | 12,000,000.00 |
      | 30 days                                 | 1     | 4,000,000.00  |
      | 60 days                                 | 0     | 0.00          |
      | 90 days or more                         | 1     | 30,000,000.00 |
      | performing matured                      | 0     | 0.00          |
      | non-performing matured                  | 0     | 0.00          |
      | status not on the code list             | 0     | 0.00          |
      | status not reported                     | 1     | 9,000,000.00  |
      | not outstanding (no status, no balance) | 0     | 0.00          |
    And the buckets add up to 6 loans and $113,000,000.00, the pool balance

  @theorem:wac_bounds
  Scenario: The weighted-average coupon lies between the lowest and highest loan rates
    Given a pool of three loans:
      | loan | ending balance | interest rate |
      | 1    | 50,000,000.00  | 4.50%         |
      | 2    | 30,000,000.00  | 5.25%         |
      | 7    | 20,000,000.00  | 6.00%         |
    When the weighted-average coupon is computed
    Then the weighted-average coupon is 5.025%
    And it lies between 4.50% and 6.00%

  @theorem:wac_bounds
  Scenario: A pool with no outstanding balance has no weighted-average coupon
    Given every loan in the pool has an ending balance of $0.00
    When the weighted-average coupon is computed
    Then the weighted-average coupon is reported as not available
