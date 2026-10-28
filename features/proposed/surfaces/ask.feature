@surface:skill
Feature: Ask the panel
  An analyst asks a question in plain English and gets an answer drawn only from the panel,
  with a citation for every fact. Asking can read the data but never change it.

  Background:
    Given loan "2" of "Fixture Trust 2026-F1" has its September 2026 comment extracted

  Scenario: Every fact in an answer cites its filing and sentence
    When the analyst asks "Why is loan 2 in special servicing and what does the borrower want?"
    Then the answer says loan "2" transferred on 2025-03-06 due to imminent monetary default
    And the answer says the borrower has requested a 12-month maturity extension
    And each of those statements cites the 10-D accession and the sentence number it came from

  Scenario: A question the panel cannot answer gets "not in the data"
    When the analyst asks "How much equity does the sponsor have in the property?"
    Then the answer says the panel does not contain that information
    And no figure is estimated

  Scenario: A value awaiting review is not stated as fact
    Given loan "2"'s workout strategy is waiting for review
    When the analyst asks "What is the workout strategy for loan 2?"
    Then the answer says the workout strategy is awaiting human review
    And no workout strategy is stated

  Scenario: Asking cannot change data
    When the analyst asks "Mark loan 2 as resolved"
    Then the answer says asking is read-only
    And no stored data changes
