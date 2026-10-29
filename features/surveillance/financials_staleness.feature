@approved @layer:data @surface:cli @surface:api
Feature: Financials staleness
  NOI and DSCR in the loan tape are often months old. Every loan shows how old its financials
  are, measured from the period end and from the filing date, when a reader first sees them.

  Background:
    Given the September 2026 ABS-EE for "Fixture Trust 2026-F1", with reporting period end 2026-09-11, filed on 2026-09-29

  Scenario: Staleness is measured from the period end and from the filing date
    Given loan "1"'s most recent financials end on 2026-06-30
    When staleness is measured
    Then loan "1"'s financials are 73 days old at period end and 91 days old at filing

  Scenario: Loans that cannot report financials are left out
    When staleness is measured
    Then defeased loan "3", co-op loan "4" and paid-off loan "6" are excluded
    And 4 loans are counted as active

  Scenario: A loan without updated financials is reported as such, never as zero days
    Given loan "5" has no most-recent financials
    When staleness is measured
    Then loan "5" is listed as "no updated financials"
    And loan "5" is not part of the staleness percentiles

  Scenario: The deal summary shows the distribution
    When staleness is measured
    Then the deal summary shows 3 loans with financials, a median of 164 days and a 90th percentile of 619 days
    And 1 loan's financials are more than six months old

  @real-data
  Scenario: A newly issued deal has no updated financials yet
    Given the September 2026 ABS-EE for "BANK5 2026-5YR23", a deal that closed in June 2026
    When staleness is measured
    Then the deal summary shows 0 loans with updated financials
    And the summary says "only at-securitization financials so far"
