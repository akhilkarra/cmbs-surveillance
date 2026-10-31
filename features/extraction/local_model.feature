@approved @layer:model @surface:cli
Feature: A local decision model as the fallback
  Jev runs only in TypeSafe's cloud. Cloudflare's Clef-flash, an open-weight decision model that
  answers the same typed questions, runs on the analyst's own machine (grill log Q7). It answers
  through the same interface, is pinned and recorded like Jev, and is compared with Jev before
  anyone relies on it.

  Background:
    Given loan "2" of "Fixture Trust 2026-F1" has its September 2026 special-servicing comment

  Scenario: The local model reads a comment through the same questions
    Given the settings name the local model "clef-flash-2026-10-01" with prompt version 3
    And its answers are replayed from calls recorded on this machine
    When the comment is extracted
    Then the transfer reason is "imminent monetary default", supported by sentence 1
    And the extraction records the local model and its pinned version

  Scenario: Confidence is computed the same way for every model
    Given the local model answers the borrower request with probabilities 0.80, 0.15 and 0.05
    When the answer is read
    Then its confidence is computed from those probabilities as for Jev, not taken from the model's own field

  Scenario: Restricted text may go to the local model without zero data retention
    Given zero data retention is not configured
    And a restricted comment from an investor portal
    When it is extracted with the local model
    Then the comment is read, because it never leaves this machine

  Scenario: The two models are compared before the local one is trusted
    Given recorded answers from Jev and from the local model for the same comments
    When the agreement report is built
    Then it shows, for each field, how often the two models give the same outcome and the same value
    And it lists every field on which they disagree, with both answers
