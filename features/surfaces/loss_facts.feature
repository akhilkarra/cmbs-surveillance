@approved @surface:skill @surface:cli
Feature: The facts that size a loss
  For a delinquent loan a PM needs the advances outstanding, whether the servicer has called them
  non-recoverable, any realized loss and the appraisal reduction. The panel already stores them
  from the tape and the 10-D; asking now states them, each with its citation (grill log Q98).

  Background:
    Given loan "2" of "Fixture Trust 2026-F2" was reported in September 2026, filed 2026-09-29, with:
      | tape field                                     | value        |
      | totalPrincipalInterestAdvancedOutstandingAmount | 1,115,458.57 |
      | totalTaxesInsuranceAdvancesOutstandingAmount    | 214,557.19   |
      | otherExpensesAdvancedOutstandingAmount          | 18,250.00    |
      | nonRecoverabilityIndicator                      | false        |
    And the September 2026 10-D of "Fixture Trust 2026-F2" reports for loan "2" an appraisal reduction of 4,200,000.00 dated 2026-08-15

  Scenario: Advances, non-recoverability and the appraisal reduction are facts with citations
    When "pat" asks for the facts of loan "2" of "Fixture Trust 2026-F2"
    Then the facts include:
      | fact                            | value         | cited to                                                   |
      | P&I advances outstanding        | $1,115,458.57 | ABS-EE, totalPrincipalInterestAdvancedOutstandingAmount     |
      | T&I advances outstanding        | $214,557.19   | ABS-EE, totalTaxesInsuranceAdvancesOutstandingAmount        |
      | other expenses advanced         | $18,250.00    | ABS-EE, otherExpensesAdvancedOutstandingAmount              |
      | advances deemed non-recoverable | no            | ABS-EE, nonRecoverabilityIndicator                          |
      | appraisal reduction amount      | $4,200,000.00 | 10-D, appraisal reduction table, dated 2026-08-15           |

  Scenario: A loan returned to the master servicer says when
    Given loan "4" of "Fixture Trust 2026-F2" was reported in September 2026 with special-servicer transfer date 2023-01-10 and master-servicer return date 2023-09-15
    When "pat" asks for the facts of loan "4" of "Fixture Trust 2026-F2"
    Then the facts include the master-servicer return date 2023-09-15, cited to mostRecentMasterServicerReturnDate

  Scenario: A liquidated loan states its loss
    Given loan "6" of "Fixture Trust 2026-F2" was reported in September 2026 with a realized loss of 2,350,000.00, liquidation date 2026-09-10 and liquidation code 3
    When "pat" asks for the facts of loan "6" of "Fixture Trust 2026-F2"
    Then the facts include a realized loss of $2,350,000.00, cited to realizedLossToTrustAmount
    And the facts include the liquidation date 2026-09-10 and liquidation code 3, cited to the tape

  Scenario: What the filings do not give stays "not in the data"
    When "pat" asks "What is the ASER and the latest appraised value of loan 2?" about "Fixture Trust 2026-F2"
    Then the answer says the panel does not contain that information
    And no figure is estimated
