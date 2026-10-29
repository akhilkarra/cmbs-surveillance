@approved @layer:model @surface:cli @surface:api
Feature: Extraction quality
  A golden set of comments labelled by analysts measures how well extraction works, field by
  field, with a confidence interval on every number. The report is re-run whenever the model,
  prompt or thresholds change, and a risk officer reads it before approving the change.

  Background:
    Given a golden set of 100 special-servicing comments labelled by "alex" and "morgan"
    And 20 of them were labelled by both, who agreed on 19
    And 40 of the comments contain a borrower request

  Scenario: Precision and recall per field, with intervals
    Given extraction with model "jev-1.14.0" and prompt version 3 finds a borrower request in 42 comments, 38 of them correct
    When the quality report is built
    Then the borrower request field shows precision 0.905 with a 95% interval of 0.779 to 0.962
    And recall 0.950 with a 95% interval of 0.835 to 0.986
    And the report names the model, prompt version and settings version it measured

  Scenario: Auto-accepted values are checked separately
    Given 60 of the 100 comments had their borrower request auto-accepted and 58 of those were correct
    When the quality report is built
    Then auto-accepted accuracy for the borrower request shows 0.967 with a 95% interval of 0.886 to 0.991
    And the report says the abstention band is "holding" because the lower bound is above 0.85

  Scenario: Labeller agreement is reported
    When the quality report is built
    Then labeller agreement is 19 of 20 double-labelled comments

  Scenario: A worse result is flagged against the previous report
    Given the previous report, for prompt version 3, showed borrower request recall of 0.950
    And prompt version 4 finds 32 of the 40 borrower requests
    When the quality report for prompt version 4 is built
    Then recall shows 0.800 with a 95% interval of 0.652 to 0.895
    And the report flags "borrower request recall fell from 0.950 to 0.800"

  Scenario: Reviewed and overridden values become label candidates
    Given "alex" overrode loan "2"'s borrower request to "forbearance" with sentence 3 as evidence
    When the golden set is extended
    Then loan "2"'s comment is proposed as a new golden item with label "forbearance"
    And it joins the golden set only after a second analyst confirms it
