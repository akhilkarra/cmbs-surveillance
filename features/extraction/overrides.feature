@draft @layer:logic @surface:api @surface:ui
Feature: Overriding an extracted value
  An analyst who knows better can replace a value the model produced, whether it was
  auto-accepted or reviewed. The model's answer is kept, the override is attributed, and a
  reason and a supporting sentence are required.

  Background:
    Given loan "2"'s borrower request is "maturity extension", auto-accepted with sentence 3 as evidence

  Scenario: An override replaces the value and keeps the original
    When "alex" overrides the borrower request to "forbearance" citing sentence 3 because "the request is for payment relief, not more time"
    Then the panel shows borrower request "forbearance", marked "overridden by alex"
    And the model's answer "maturity extension" is still stored alongside it

  Scenario: An override without a reason is refused
    When "alex" overrides the borrower request to "forbearance" with no reason
    Then the override is refused with "a reason is required"
    And the panel still shows "maturity extension"

  Scenario: An override must cite a sentence that exists
    When "alex" overrides the borrower request to "forbearance" citing sentence 9
    Then the override is refused with "sentence 9 does not exist in this comment"

  Scenario: A PM cannot override
    When "pat" tries to override the borrower request to "forbearance"
    Then the action is refused with "pm may not override values"

  Scenario: Overrides show up everywhere the value does
    Given "alex" overrode the borrower request to "forbearance"
    When "pat" asks "What does the borrower of loan 2 want?"
    Then the answer says "forbearance", noting it was set by "alex" and citing sentence 3
    When the next quality report is built
    Then the override is listed as a label candidate
