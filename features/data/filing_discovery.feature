@approved @layer:data @surface:cli @surface:api
Feature: Filing discovery
  An analyst names a CMBS deal and learns which public filings exist for it.
  The pipeline then knows which months it can load, and which deals it cannot see at all.

  @real-data
  Scenario: A registered conduit has monthly loan tapes and distribution reports
    Given the analyst looks up the deal "BANK 2018-BNK13"
    When filing discovery runs on 2026-10-27
    Then the deal resolves to issuing entity CIK 1744707
    And September 2026 has a 10-D with accession 0001888524-26-017704 filed on 2026-09-29
    And September 2026 has an ABS-EE with accession 0001888524-26-017705 filed on 2026-09-29
    And October 2026 has a 10-D with accession 0001888524-26-019174 filed on 2026-10-27
    And October 2026 has an ABS-EE with accession 0001888524-26-019175 filed on 2026-10-27

  @real-data
  Scenario: A deal issued before November 2016 has distribution reports but no loan tape
    Given the analyst looks up the deal "WFRBS 2014-C25"
    When filing discovery runs on 2026-10-27
    Then the deal resolves to issuing entity CIK 1623838
    And the deal is marked "distribution reports only"
    And no ABS-EE periods are listed

  @real-data
  Scenario: A deal that stopped reporting shows when it stopped
    Given the analyst looks up the deal "COMM 2014-CR18"
    When filing discovery runs on 2026-10-27
    Then the deal resolves to issuing entity CIK 1610486
    And the match notes that EDGAR names the trust "COMM 2014-CCRE18"
    And the deal is marked "reporting suspended" because it filed a Form 15-15D
    And its last 10-D was filed in September 2024

  @real-data
  Scenario: A privately placed single-borrower deal is reported as not on EDGAR
    Given the analyst looks up the deal "HYT 2024-RGCY"
    When filing discovery runs on 2026-10-27
    Then no issuing entity CIK is found
    And the deal is marked "not publicly reported"
    And the only filing listed is the depositor's ABS-15G filed on 2024-08-06
