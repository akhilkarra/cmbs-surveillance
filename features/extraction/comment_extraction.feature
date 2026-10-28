@draft @layer:model @surface:cli @surface:api
Feature: Comment extraction with evidence
  Each special-servicing comment becomes typed fields. Every value is backed by one sentence
  of the comment, quoted exactly, so a reader can check it in seconds.

  Background:
    Given loan "2" of "Fixture Trust 2026-F1" has this September 2026 special-servicing comment:
      """
      The loan transferred to Special Servicing effective 3/6/2025 due to imminent monetary default. As of 12/31/2024, DSCR NOI was 0.71x and occupancy was 61.4%. The borrower has requested a 12-month maturity extension. The special servicer is evaluating the request.
      """
    And the comment is split into 4 numbered sentences
    And model answers are replayed from calls recorded with model "jev-1.14.0"

  Scenario: The transfer reason and date are extracted with their sentence
    When the comment is extracted
    Then the transfer reason is "imminent monetary default", supported by sentence 1
    And the transfer date is 2025-03-06, supported by sentence 1

  Scenario: Financial figures quoted in the comment keep their as-of date
    When the comment is extracted
    Then the comment's DSCR is 0.71x as of 2024-12-31, supported by sentence 2
    And the comment's occupancy is 61.4% as of 2024-12-31, supported by sentence 2

  Scenario: A borrower request is recorded as pending, not as granted
    When the comment is extracted
    Then the borrower request is "maturity extension", supported by sentence 3
    And the request status is "pending", supported by sentence 4

  @layer:logic
  Scenario: Evidence that is not in the comment is rejected
    Given an extraction cites the evidence "The borrower was granted a 12-month extension."
    When the evidence is checked against the comment
    Then the extraction is rejected with the reason "evidence not found in source"
