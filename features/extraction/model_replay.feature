@draft @layer:model @surface:cli
Feature: Model replay
  Model answers are recorded once and replayed, so tests and reruns give identical results,
  and a change of model can never slip in unnoticed.

  Scenario: A recorded answer is reused instead of calling the model
    Given an answer is recorded for loan "2"'s September 2026 comment with model "jev-1.14.0" and prompt version 3
    When the comment is extracted again with model "jev-1.14.0" and prompt version 3
    Then the recorded answer is used
    And no request is sent to the model provider

  Scenario: A changed comment has no recording and stops the replay run
    Given an answer is recorded for loan "2"'s September 2026 comment with model "jev-1.14.0" and prompt version 3
    And loan "2"'s October 2026 comment differs from September's by one word
    When the October 2026 comment is extracted in replay mode
    Then the run stops with "no recorded answer for this input"
    And no request is sent to the model provider

  Scenario: A floating model alias is refused
    Given the configured model is "jev-latest"
    When an extraction run starts
    Then the run stops with "pin an exact model version"

  Scenario: An answer from a different model version is refused
    Given the configured model is "jev-1.14.0"
    And the provider's response says model "jev-1.15.0" answered
    When the answer is recorded
    Then the run stops with "model version mismatch: asked for jev-1.14.0, got jev-1.15.0"
    And nothing is recorded
