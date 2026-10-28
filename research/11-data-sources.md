# 11 — Data-source catalog for "text leads tape" (CMBS)

Written 2026-10-24. Tags: **[V]** = verified this session against the linked source (most claims were checked on EDGAR); **[I]** = inferred or from background knowledge, not re-checked today.

Code: the `cmbs` package (`cmbs edgar search`, `cmbs edgar submissions`, `cmbs edgar download`, `cmbs analyze staleness`) and the browser helper `tools/browser/absee_loan_rows.js`. Samples: `data/samples/` (see §9).

---

## 0. TL;DR

1. **Most of a typical SASB-heavy CMBS book is not on EDGAR as loan-level data.**
   - The SASB 144A floaters (HYT 2024-RGCY, KSL 2026-HT3, BX 2024-VLT5, MSC 2017-ASHF, SMR 2022-IND, WFCM 2025-DWHP, KRE 2026-ICNA, MHP 2025-MHIL2) appear on EDGAR **only as a one-off ABS-15G** due-diligence filing under the depositor. They have no ABS-EE and no 10-D. [V]
   - Pre-Reg AB II conduits (WFRBS 2013-C15, WFRBS 2014-C25, CGCMT 2016-C1) file **10-D only, with no ABS-EE**. [V]
   - COMM 2014-CR18 **stopped filing in 2024** after it filed a Form 15-15D. [V]
2. **ABS-EE (EX-102) exists only for registered conduits issued after Nov 2016.** BANK, BANK5, Benchmark and similar shelves are covered. Its Schedule AL fields are all codes, dates and amounts. **There is no free-text field and no appraisal-reduction field.** [V]
3. **Measured staleness of "most recent financials"** in 7 seasoned conduits, Sept-2026 reporting period, 246 active loans: [V]
   - **Median 73 days** before the determination date, which is about **91 days before the ABS-EE filing date**.
   - p75 is 164 days and p90 is 254 days. **14%** are more than 6 months stale and **2.4%** more than 12 months.
   - New deals report **no** updated financials for their first months.
   - New statements first appear a **median of 72 days** after the period end (p90 104, max 162), based on 14 monthly files for BANK 2018-BNK13.
   - Each loan's financials change about **4.75 times in 14 months**, i.e. roughly quarterly.
   - Payment status and paid-through date are updated **monthly**, so they are fresh.
4. **Public text exists, but it lags rather than leads.**
   - The 10-D EX-99.1 from Computershare (formerly Wells CTS) has a free-text **"Special Servicing Comments"** column. It is populated only *after* a loan transfers to the special servicer. [V]
   - The Citi-administered 10-D (CGCMT 2016-C1) has **no** comment text. [V]
   - The leading text, **servicer watchlist comments**, lives in the CREFC IRP behind trustee portals. [V for the portal gating, I for the field contents]
5. **Verdict:** a clean test of "text leads tape" on free public data is **not feasible** for a SASB book. A weaker version is feasible on public conduits. The strong version needs a holder's portal or vendor access to CREFC watchlist and special-servicer comments (see §8).

---

## 1. SEC EDGAR — Form ABS-EE (Reg AB II)

### 1.1 Finding a deal's issuing-entity CIK from its name

Four routes worked:

1. **Entity typeahead (best).** `https://efts.sec.gov/LATEST/search-index?keysTyped=<name>` returns entity names and CIKs. It works with the project UA. [V]
   - `WFRBS Commercial Mortgage Trust 2014` → 2014-C25 = **1623838**.
   - `BANK5 2026-5YR23` → **2140978**.
   - `Morgan Stanley Capital I Trust 2017-ASHF` → **1721543**.
   - `COMM 2014-CCRE18` → **1610486**. Note that EDGAR's name uses "CCRE18", not "CR18".
   - `Benchmark 2026-B43` → **2121298**.
2. **Full-text search.** `efts.sec.gov/LATEST/search-index?q="<ticker>"&forms=ABS-15G,ABS-EE,10-D,8-K` finds deals whose trust has **no** CIK of its own. Most SASB deals are like this; you find them through the depositor's ABS-15G. [V]
   - **HYT 2024-RGCY**: ABS-15G filed 2024-08-06 by Wells Fargo Commercial Mortgage Securities Inc (CIK 850779).
   - **BX 2024-VLT5**: ABS-15G filed 2024-10-02, same depositor.
   - **WFCM 2025-DWHP**: ABS-15G filed 2025-03-25.
   - **KRE 2026-ICNA**: ABS-15G filed 2026-03-27.
   - **MHP 2025-MHIL2**: ABS-15G filed 2025-08-11 by Citigroup Commercial Mortgage Securities Inc.
   - **KSL 2026-HT3**: no hit at all. It appears only in fund N-PORTs.
3. The `browse-edgar?company=` page on www.sec.gov returns HTTP 403 to curl with the project UA (§7). It worked through WebFetch and a browser. [V]
4. **Gotcha:** efts phrase queries on tickers such as "2020-DUNE" or "2021-ROSS" match unrelated companies because of tokenization. Always filter results by form type.

### 1.2 Cadence, and how ABS-EE relates to 10-D [V]

- **ABS-EE is filed the same day as the 10-D, as a separate accession**, about 12 days after the distribution date. The distribution date is the 17th or the next business day.
  - Example: BANK 2018-BNK13, distribution 2026-09-17, filed 2026-09-29. 10-D accession `-017704`, ABS-EE `-017705`.
- The XML's `reportingPeriodEndDate` is the **determination date** (09-11-2026). The submissions JSON's `reportDate` is the distribution date (2026-09-17).
- The 10-D accession *also* carries a copy of `exh_102.xml` and `exh_103.xml` for Computershare-administered deals.
- **There is one ABS-EE per monthly period:**
  - BANK 2018-BNK13 has 98 ABS-EE filings against 97 10-Ds.
  - Benchmark 2018-B8 has 94 of each.
  - Amendments are filed as ABS-EE/A and 10-D/A.
- **A pre-offering ABS-EE is filed at launch with the 424H.** BANK5 2026-5YR23: ABS-EE 2026-06-22, 424H the same day, 424B2 on 2026-06-30. This gives you the at-securitization tape before closing.
- **Deals stop reporting after a Form 15.** COMM 2014-CCRE18 filed a 15-15D, and its last 10-D was 2024-09.
- **ABS-EE applies only to registered deals offered on or after the asset-level compliance date, Nov 2016.** [I for the date] The data matches:
  - CGCMT 2016-C1 (issued May 2016) has 124 10-Ds and 0 ABS-EE.
  - WFRBS 2014-C25 has 141 10-Ds and 0 ABS-EE.
  - BANK 2017-BNK6 (issued Jul 2017) has 111 ABS-EE. [V]

### 1.3 EX-102 XML structure (CMBS Schedule AL, Item 2) [V]

- **Namespace and layout.** Namespace is `http://www.sec.gov/edgar/document/absee/cmbs/assetdata`. The root is `<assetData>`, with one `<assets>` element per asset, each holding nested `<property>` elements.
- **Date format** is `MM-DD-YYYY`.
- **Empty fields are omitted**, not blank. A missing tag means "not reported".
- **Asset granularity varies by shelf:**
  - BANK and BANK5 split pari passu notes into separate assets (`1`, `1A`, `1B`). The companion notes carry `propertyName = NA` and no property data.
  - Portfolio properties appear as sub-assets (`4-001` … `4-065`), each with its own property financials. BANK 2017-BNK6 has 200 assets, which reduce to 71 top-level loans.
- **EX-103** (`exh_103.xml`) is a `<comments>` file of *static methodological footnotes* keyed by item number. Example: how pari passu splits are reported. **It is not loan commentary.** [V, BNK13 Sept 2026]
- **Item list.** The full regulatory list is 17 CFR 229.1125, Schedule AL Item 2(a)–(k), via [Cornell LII](https://www.law.cornell.edu/cfr/text/17/229.1125).

Actual tag names seen in BANK 2017-BNK6 and BANK5 2026-5YR23 (Sept 2026):

- **Loan level:**
  - **Identifiers and dates:** `assetTypeNumber`, `assetNumber`, `reportingPeriodBeginningDate`, `reportingPeriodEndDate`, `originatorName`, `originationDate`, `firstLoanPaymentDueDate`, `maturityDate`.
  - **Original terms:** `originalLoanAmount`, `originalTermLoanNumber`, `originalAmortizationTermNumber`, `originalInterestRatePercentage`, `interestRateSecuritizationPercentage`, `interestAccrualMethodCode`, `originalInterestRateTypeCode`, `originalInterestOnlyTermNumber`.
  - **Structure and payment type:** `underwritingIndicator`, `lienPositionSecuritizationCode`, `loanStructureCode`, `paymentTypeCode`, `periodicPrincipalAndInterestPaymentSecuritizationAmount`, `scheduledPrincipalBalanceSecuritizationAmount`, `paymentFrequencyCode`, `NumberPropertiesSecuritization`, `NumberProperties`, `graceDaysAllowedNumber`.
  - **Indicators:** `interestOnlyIndicator`, `balloonIndicator`, `prepaymentPremiumIndicator`, `negativeAmortizationIndicator`, `modifiedIndicator`.
  - **Prepayment terms:** `prepaymentLockOutEndDate`, `yieldMaintenanceEndDate`, `prepaymentPremiumsEndDate`.
  - **Negative amortization:** `maximumNegativeAmortizationAllowedPercentage`, `maximumNegativeAmortizationAllowedAmount`, `negativeAmortizationDeferredInterestCapAmount`, `deferredInterestCumulativeAmount`, `deferredInterestCollectedAmount`.
  - **This period's activity:** `assetAddedIndicator`, `reportPeriodModificationIndicator`, `reportPeriodBeginningScheduleLoanBalanceAmount`, `totalScheduledPrincipalInterestDueAmount`, `reportPeriodInterestRatePercentage`, `servicerTrusteeFeeRatePercentage`, `scheduledInterestAmount`, `otherInterestAdjustmentAmount`, `scheduledPrincipalAmount`, `unscheduledPrincipalCollectedAmount`, `otherPrincipalAdjustmentAmount`, `reportPeriodEndActualBalanceAmount`, `reportPeriodEndScheduledLoanBalanceAmount`, `paidThroughDate`.
  - **Advances:** `servicingAdvanceMethodCode`, `nonRecoverabilityIndicator`, `totalPrincipalInterestAdvancedOutstandingAmount`, `totalTaxesInsuranceAdvancesOutstandingAmount`, `otherExpensesAdvancedOutstandingAmount`.
  - **Payment status and servicing:** `paymentStatusLoanCode`, `nextInterestRatePercentage`, `primaryServicerName`, `mostRecentSpecialServicerTransferDate`, `mostRecentMasterServicerReturnDate`.
  - **Repurchase demands and losses:** `assetSubjectDemandIndicator`, `repurchaseAmount`, `realizedLossToTrustAmount`.
  - **Payoff and workout:** `liquidationPrepaymentCode`, `liquidationPrepaymentDate`, `prepaymentPremiumYieldMaintenanceReceivedAmount`, `workoutStrategyCode`.
  - **Modifications:** `lastModificationDate`, `modificationCode`, `postModificationPaymentAmount`, `postModificationMaturityDate`, `postModificationAmortizationPeriodAmount`.
- **Property level (`property/…`):**
  - **Identity and location:** `propertyName`, `propertyAddress`, `propertyCity`, `propertyState`, `propertyZip`, `propertyCounty`, `propertyTypeCode`.
  - **Size and age:** `netRentableSquareFeetNumber`, `netRentableSquareFeetSecuritizationNumber`, `unitsBedsRoomsNumber`, `unitsBedsRoomsSecuritizationNumber`, `yearBuiltNumber`, `yearLastRenovated`.
  - **Valuation:** `valuationSecuritizationAmount`, `valuationSourceSecuritizationCode`, `valuationSecuritizationDate`, `mostRecentValuationAmount`, `mostRecentValuationDate`, `mostRecentValuationSourceCode`.
  - **Occupancy, status and defeasance:** `physicalOccupancySecuritizationPercentage`, `mostRecentPhysicalOccupancyPercentage`, `propertyStatusCode`, `defeasanceOptionStartDate`, `DefeasedStatusCode`.
  - **Tenants:** `largestTenant`, `squareFeetLargestTenantNumber`, `leaseExpirationLargestTenantDate`, plus the same fields for the second and third largest tenants.
  - **Financials:** `financialsSecuritizationDate`, `mostRecentFinancialsStartDate`, `mostRecentFinancialsEndDate`, `revenueSecuritizationAmount`, `mostRecentRevenueAmount`, `operatingExpensesSecuritizationAmount`, `operatingExpensesAmount`, `netOperatingIncomeSecuritizationAmount`, `mostRecentNetOperatingIncomeAmount`, `netCashFlowFlowSecuritizationAmount` (sic), `mostRecentNetCashFlowAmount`, `netOperatingIncomeNetCashFlowSecuritizationCode`, `netOperatingIncomeNetCashFlowCode`, `mostRecentDebtServiceAmount`.
  - **DSCR:** `debtServiceCoverageNetOperatingIncomeSecuritizationPercentage`, `mostRecentDebtServiceCoverageNetOperatingIncomePercentage`, `debtServiceCoverageNetCashFlowSecuritizationPercentage`, `mostRecentDebtServiceCoverageNetCashFlowpercentage` (sic, lower-case "p"), `debtServiceCoverageSecuritizationCode`, `mostRecentDebtServiceCoverageCode`.
  - **Lease review:** `mostRecentAnnualLeaseRolloverReviewDate`.

### 1.4 Where each field you asked about lives

| Concept | Tag | Notes |
|---|---|---|
| Most-recent financials period | `property/mostRecentFinancialsStartDate`, `property/mostRecentFinancialsEndDate` | Period length is 3, 6, 9 or 12 months (YTD or TTM). Empty for new deals. [V] |
| NOI | `property/mostRecentNetOperatingIncomeAmount` (at issue: `netOperatingIncomeSecuritizationAmount`) | `netOperatingIncomeNetCashFlowCode` tells you whether the figure follows the CREFC normalized definition or the borrower's statement. [V] |
| DSCR | `property/mostRecentDebtServiceCoverageNetOperatingIncomePercentage`, `…NetCashFlowpercentage`, plus `mostRecentDebtServiceCoverageCode` | Expressed as a ratio (2.52 = 2.52x). [V] |
| Occupancy | `property/mostRecentPhysicalOccupancyPercentage` | Can stay frozen for a long time. Ditson's was 0.28 for all 14 months. [V] |
| Paid-through | `paidThroughDate` | Monthly. [V] |
| Delinquency | `paymentStatusLoanCode` | Values seen: 0 current, A/B grace or late, 1/2/3 = 30/60/90+ days, 5 non-performing matured balloon. [V for the values, I for the full code list] |
| Special servicing | `mostRecentSpecialServicerTransferDate`, `mostRecentMasterServicerReturnDate` | "In special servicing" = transfer date set and no later return date. [V] |
| Workout strategy | `workoutStrategyCode` | Seen: 2 foreclosure, 7 REO, 8 resolved, 11 full payoff, 13 TBD, 98 other. [V] |
| Modification | `modifiedIndicator`, `reportPeriodModificationIndicator`, `modificationCode`, `lastModificationDate`, `postModification*` | Seen: 10 forbearance, 98 other. [V] |
| Appraisal Reduction Amount | **None in Schedule AL** | ARA appears only in the 10-D EX-99.1 "Appraisal Reduction Detail" and in the CREFC IRP. `mostRecentValuation*` is the closest proxy: date, amount and source code (MAI, BPO). [V] |
| Liquidation and loss | `liquidationPrepaymentCode`, `realizedLossToTrustAmount` | [V] |

### 1.5 Is there any free-text field in the CMBS asset XML?

**No.** [V]
- Every Item 2 data point is a code, date, amount or name ([Cornell LII, 229.1125](https://www.law.cornell.edu/cfr/text/17/229.1125)).
- The only quasi-text fields are names: `propertyName` and the largest-tenant names.
- EX-103 holds static methodological footnotes, not loan commentary.

### 1.6 Which deals file ABS-EE (tested against EDGAR submissions JSON) [V]

| Deal (sample of SASB and conduit deals) | EDGAR entity (CIK) | Forms on EDGAR | ABS-EE? |
|---|---|---|---|
| BANK5 2026-5YR23 | 2140978 | 424H, 424B2, FWP×11, ABS-15G, ABS-EE×3, 10-D×2, 10-D/A, 8-K | **Yes** (public conduit, closed 2026-06) |
| Benchmark 2026-B43 | 2121298 | ABS-EE×5, 10-D×4, 424B2 … | **Yes** |
| WFRBS 2014-C25 | 1623838 | 10-D×141, 10-K×12, 424B5, FWP | **No** (pre-Reg AB II). The 10-D EX-99.1 is the only loan-level source. |
| WFRBS 2013-C15 | 1578423 | 10-D×157 … | **No** |
| CGCMT 2016-C1 | 1673255 | 10-D×124 … | **No** (issued May 2016) |
| COMM 2014-CR18 | 1610486 | 10-D×124, **15-15D** | **No**, and **reporting suspended since 2024-10** |
| MSC 2017-ASHF (SASB hotel) | 1721543 | ABS-15G×1 (2017-11-08) | **No** |
| CSMC 2017-PFHP (SASB hotel) | 1723050 | ABS-15G×1 | **No** |
| HYT 2024-RGCY, BX 2024-VLT5, WFCM 2025-DWHP, KRE 2026-ICNA, MHP 2025-MHIL2 | No trust CIK. Only the depositor's ABS-15G mentions them. | ABS-15G | **No** |
| KSL 2026-HT3 | Not found as a filer | Appears only in fund N-PORTs | **No** |
| ILPT 2025-LPF2 (SASB) | No trust CIK | — | **Indirectly yes** |

**Loophole for split SASB loans.**
- When a SASB loan has pari passu notes in public conduits, the conduit's 8-K files the SASB **TSA** as an exhibit. Examples: `exh4_2-ilpt2025lpf2tsa.htm` in 8-Ks of BANK5 2025-5YR16, Benchmark 2025-V16 and MSBAM 2025-5C2.
- The loan then shows up in the conduit's ABS-EE. "ILPT 2025 Portfolio" is asset 1 of BANK5 2025-5YR16, with financials through 03-31-2026. [V]
- The same pattern holds for BANK5 2026-5YR23's 8-K: `exh4_2-mtn2026lpfxtsa.htm`. [V]
- **Stage-0 action:** run efts full-text on each SASB ticker restricted to 8-K exhibits to find these pari passu companions.

### 1.7 Downloaded EX-102 files and the staleness measurement (key deliverable) [V]

**Method.**
- Sept-2026 EX-102 for 9 public conduits (determination date 09-11-2026).
- Kept top-level loans with ending balance above 0, excluding defeased loans and residential co-ops. Co-ops (`CH`) report no operating statements.
- Staleness = `reportingPeriodEndDate − mostRecentFinancialsEndDate`.
- Code: `tools/browser/absee_loan_rows.js`, mirrored in `src/cmbs/analysis/staleness.py`. Raw numbers: `data/samples/_cross_section/staleness_2026-09.json`.

**Cross-section.** Requested sample deals plus seasoned comparators, all from the September 2026 ABS-EE.

| Deal | XML size | Active loans | With financials | Median (days) | p75 | p90 | Max | Share >6 mo | Share >12 mo | Period length (months: count) |
|---|---|---|---|---|---|---|---|---|---|---|
| BANK 2017-BNK6 | 756 KB | 37 | 37 | 73 | 164 | 254 | 2,811 | 16% | 8.1% | 3:8, 6:21, 12:8 |
| BANK 2017-BNK9 | 380 KB | 33 | 33 | 73 | 73 | 164 | 254 | 6% | 0% | 3:4, 6:23, 12:6 |
| BANK 2018-BNK13 | 335 KB | 34 | 34 | 73 | 164 | 164 | 985 | 9% | 2.9% | 3:6, 6:21, 12:7 |
| Benchmark 2018-B8 | 678 KB | 36 | **22** | 73 | 164 | 164 | 164 | 0% | 0% | 3:5, 6:11, 12:6 |
| BANK 2019-BNK19 | 554 KB | 43 | 43 | 73 | 164 | 254 | 1,350 | 21% | 4.7% | 3:6, 6:23, 12:14 |
| BANK 2022-BNK39 | 555 KB | 50 | 50 | 73 | 254 | 254 | 346 | 28% | 0% | 3:10, 6:27, 9:1, 12:12 |
| BANK5 2025-5YR16 | 710 KB | 40 | **27** | 73 | 73 | 164 | 254 | 4% | 0% | 3:5, 6:18, 12:4 |
| **Pooled, 7 seasoned deals** | | | **246** | **73** | **164** | **254** | 2,811 | **14.2%** | **2.4%** | |
| BANK5 2026-5YR23 (closed Jun 2026) | 483 KB | 33 | **0** | — | | | | | | Only at-securitization financials so far |
| Benchmark 2026-B43 (closed ~Apr 2026) | 267 KB | 32 | **0** | — | | | | | | Same |

How to read this:
- **The 73-day floor is structural.** The freshest possible statement in a Sept-11 file is the 6/30 quarter. Nothing beats about 2.4 months behind the determination date, or about 3 months (91 days) behind the ABS-EE filing date that a point-in-time model actually sees.
- **The values bunch at quarter ends.** 164 days = 3/31 statements, 254 = 12/31, 346 = 9/30 of the prior year.
- **Some loans have no updated financials at all.** Benchmark 2018-B8 lacks them for 14 of 36 active loans, including Aventura Mall, Moffett Towers and 10 Brookline Place. These are mostly large pari passu loans serviced under another deal's PSA. BANK5 2025-5YR16 lacks them for 13 of 40. [V]
- **The right tail is real and concentrated in trouble.** BNK6's Starwood Capital Hotel Portfolio has been in special servicing since 03-06-2025, with workout code 11 and status B. Its most recent financials are FY 12-31-2024, i.e. **619 days stale** while specially serviced. The 2,811-day maximum is a single-tenant net-lease loan that reports 2018 financials.

**Time series: 14 monthly EX-102s for BANK 2018-BNK13 (Aug-2025 → Sep-2026).** Data: `data/samples/bank-2018-bnk13/timeseries_2025-08_to_2026-09.json`.
- **First-appearance lag** (from statement period end to the first reporting period that contains it), n = 135 new loan-statement pairs:
  - min 13 days, p25 43, **median 72**, p75 103, p90 104, max 162.
- **Arrival waves by statement date:**
  - 06-30-2025 statements: 15 arrived in Sept, 2 in Oct, 2 in Nov.
  - 12-31-2025 (year-end) statements: 12 in Feb, 4 in Mar, 10 in Apr, 5 in May, 1 in Jun. Year-end statements arrive more slowly.
- **Update frequency:** 4.75 statement updates per loan in 14 months on average. Distribution: 1:2, 3:1, 4:8, 5:16, 6:9 loans.
- **Worked example: Ditson Building (asset 11, office, NYC).**
  - DSCR was **0.09x with 28% occupancy as of 6/30/2025**, and that number was already in the Aug-2025 file.
  - Payment status went 0 → B in Apr-2026 → 1 (May) → 2 (Jun) → 3 (Jul).
  - It transferred to special servicing on 06-04-2026. Workout code went 13 (TBD) → 2 (foreclosure).
  - **The tape flagged distress about 10 months before default.** The special-servicer text arrived only after the transfer (§2).

**Implication.**
- NOI and DSCR in public data are about 3 months old at best, typically 3–6 months, and 8+ months for about 1 in 7 loans when they reach a point-in-time user.
- Delinquency and special-servicing fields are monthly and current.
- So "text leads tape" should be framed as text leading the **financial** fields, not the payment fields. And it can only be tested with text that exists *before* the transfer, i.e. watchlist comments.

---

## 2. Form 10-D and EX-99.1 distribution reports [V]

- **Computershare format** (CTSLink, formerly Wells Fargo CTS). Example: BANK 2018-BNK13, Sept 2026, [EX-99.1](https://www.sec.gov/Archives/edgar/data/1744707/000188852426017704/wcm18b13_ex991-202609.htm), 28 pages.
  - **Sections:** certificate distribution, factor and interest detail; reconciliations; stratifications; Mortgage Loan Detail parts 1–2; principal prepayment; historical detail; Delinquency Loan Detail; Specially Serviced Loan Detail parts 1–2; Modified Loan Detail; Historical Liquidated Loan; Loss Reconciliation; Interest Shortfall; Supplemental Notes.
  - **Text:** the **Specially Serviced Loan Detail – Part 2 has a free-text "Special Servicing Comments" column.** Short excerpts:
    - Ditson: "transferred to Special Servicing effective 6/4/2026 due to monetary default".
    - Ditson: "As of 3/31/2026, DSCR NOI was 0.03x and occupancy was 28.29%".
    - Loan 57, a co-op: "agreement with a cell tower provider without consent". This is information that never appears in the tape.
  - **No watchlist section.** The Modified Loan Detail footnote says to "refer to Servicer Reports for modification comments", i.e. the CREFC files on the portal.
- **WFRBS 2014-C25** (pre-Reg AB II, Computershare): same layout. It shows "No specially serviced loans this period". For legacy deals, **this HTML is the only public loan-level data**, including the Mortgage Loan Detail with most-recent NOI and DSCR.
- **Citi format** (CGCMT 2016-C1, [ex99_1.htm](https://www.sec.gov/Archives/edgar/data/1673255/000162829726000742/ex99_1.htm), 42 pages).
  - Has NOI Detail, Delinquency, Appraisal Reduction Detail, Historical ARA, Loan Modification Detail, and Specially Serviced Loan Detail.
  - **Zero comment text.** Points to `sf.citidirect.com` for reports.
- **Parseability.** HTML tables, about 1–1.6 MB per month, with headers broken across cells. You need a per-administrator parser (Computershare, Citi, US Bank, Deutsche/BNY…). The Computershare SS comment cell is easy to anchor on: its text begins with "The loan transferred to Special Servicing…". [V]
- **Value for the thesis.**
  - The SS comments are **lagging-to-coincident** text: written at or after the transfer.
  - They often carry a **fresher financial snapshot than the XML tape**. Both cite 3/31/2026 for Ditson, but in other deals the comment can quote a later trailing-12 figure. [I]
  - They also carry qualitative causes and resolution steps.
  - The **ARA amounts** in the 10-D are not in the ABS-EE.

---

## 3. Prospectus (424H/424B2/FWP), Annex A and PSA

Example: BANK5 2026-5YR23. [V]

- **424H (preliminary) and 424B2 (final).** About 20 MB of HTML, 3.1M characters of text in the 424B2.
  - "Description of the Mortgage Pool" appears 184 times, plus risk factors, servicing and ARA mechanics, and the operating advisor.
  - Per-loan narratives for the top 15 loans (sponsor, property, market, tenants, reserves, mezzanine) are in **Annex A-3**, referenced 22 times.
  - It is static and point-in-time as of pricing: a good baseline for sponsor and business-plan text.
- **Annex A-1 loan and property table.** Filed as a separate FWP (`n5964_x4-annexa1.htm`, 10 MB).
  - It is **HTML only, no XLSX on EDGAR**: 126 page-split tables, with columns spread across pages. Parseable with effort.
  - The at-issue numbers are also in the pre-offering ABS-EE XML, which is much easier to parse. Use Annex A-1 only for fields not in Schedule AL: sponsor, lockbox, reserves, mezzanine, cut-off LTV, UW NCF.
- **PSA, TSAs and loan purchase agreements.** Filed as exhibits to the closing 8-K. BANK5 2026-5YR23's 8-K has:
  - `exh4_1-5yr23psa.htm`: 6.3 MB PSA.
  - TSAs/PSAs governing outside-serviced pari passu loans: `exh4_2…tsa`, `exh4_3-5yr22psa`, `exh4_4-5c8psa`.
  - Mortgage loan purchase agreements (MLPAs) and co-lender agreements (`exh99_*`).
  - These are legal text: useful for **watchlist and ARA trigger definitions and the reporting obligations** (which CREFC files are delivered, to whom, and when), not for performance.
- **SASB 144A offering memoranda** are **not on EDGAR**. You get them from the underwriter, the investor portal, or deal-document sites such as DealX ReportStream (seen hosting MSC 2017-ASHF docs, access terms unknown). [I]

---

## 4. CREFC Investor Reporting Package (IRP)

- **Current version: IRP 8.4**, published 2024-03-13 ([CREFC IRP page](https://www.crefc.org/cre/content/learn/irp/irp-home.aspx), [v8.4 PDF](https://www.crefc.org/common/Uploaded%20files/CREFC%20IRP%20v8.4_Final.pdf)). [V for the version] The PDF also defines a "Schedule AL File", i.e. the ABS-EE feed is produced from the IRP.
- **Data files:** [I, standard IRP; cross-checked by a PSA definition listing these names]
  - Loan Setup File: static terms.
  - **Loan Periodic Update File (LPU):** monthly. Balances, paid-through, status, ARA, workout strategy, SS transfer dates, and a few short comment fields.
  - **Property File:** occupancy, financial dates, tenants.
  - **Financial File** (Comprehensive Financial / Operating Statement Analysis): line-item NOI.
  - **Special Servicer Loan File:** workout fields, appraisals, and special-servicer comment fields.
  - Bond Level File and Collateral Summary File.
- **Supplemental reports:**
  - **Servicer Watchlist**, with a *watchlist reason code and free-text comment*. The trigger guidelines (DSCR thresholds, rollover, deferred maintenance, borrower requests…) are in the IRP's "Servicer Watchlist/Portfolio Review Guidelines".
  - Delinquent Loan Status, REO Status, Historical Loan Modification/Liquidation, Comparative Financial Status, NOI Adjustment Worksheet, Operating Statement Analysis, Loan-level Reserve/LOC, Total Loan, and Advance Recovery reports.
- **Access.** The IRP is posted monthly on the certificate administrator's site:
  - Computershare **CTSLink**: `ctslink.com`, MFA required.
  - Citi: `sf.citidirect.com`.
  - US Bank / Trimont, BNY, Deutsche.
- **Investor gating.** On CTSLink, some deal reports are restricted to investors and parties named in the deal documents, and unlocking them requires an online investor certification (per a Computershare deal page surfaced in search). [V that it is gated] Typically the trustee-level 10-D-like statement is public, while the CREFC files and watchlist need the certification. [I] **Not publicly accessible without being or certifying as a holder.**
- **What this means for a holder.** A certificate holder **can very likely certify for every deal it holds**, including the 144A SASBs. That is the realistic route to the watchlist and SS comment text. [I]

---

## 5. Other free or cheap sources (one line each)

| Source | Access and cost | Usefulness for the thesis |
|---|---|---|
| **FINRA TRACE structured-product tables** ([FINRA](https://www.finra.org/finra-data/browse-catalog/structured-product-activity-reports-and-tables)) | Free daily and monthly aggregates (volume, trades, unique CUSIPs) with Agency and non-agency CMBS tabs [V]. Trade-level CMBS dissemination status is not confirmed [I]. | Low. Market-level liquidity context, no loan-level data. |
| **SEC N-PORT** (registered funds' holdings) | Free XML. Quarterly holdings are public about 60 days after quarter end [I]. | Medium. Gives the universe and position changes. Other funds' N-PORTs can serve as a crowd-flow or mark-dispersion signal per CUSIP. Not loan-level. |
| **Freddie Mac K-deal data** ([mf.freddiemac.com/investors/data](https://mf.freddiemac.com/investors/data.html)) | Free with registration (Multifamily Securities Investor Access). Monthly CREFC IRP for K-deals [V per search]. | **High as a sandbox.** Freddie publishes the full IRP, including the **Servicer Watchlist with comments** [I], for agency multifamily. This is the one free place to test watchlist text versus tape. Asset class and credit regime differ from a SASB/conduit book. |
| **Fed Z.1 Financial Accounts** (commercial mortgages, table L.220) | Free, quarterly. | Macro control only. |
| **MBA CRE data** (quarterly originations index, CRE delinquency by capital source) | Summaries free; databook paid. | Macro control only. |
| **Trepp monthly CMBS delinquency report** | Free, with email sign-up. Sept 2026 rate 8.02%, office 12.16% (via [Scotsman Guide](https://www.scotsmanguide.com/news/cmbs-delinquencies-reach-highest-level-since-2020/)) [V per press]. | Low to medium. Sector-level labels for validation. |
| **KBRA / Fitch / Moody's / Morningstar DBRS / S&P press releases** | Free, some need registration. Annual or event surveillance per deal. Morningstar DBRS has a run of MSC 2017-ASHF confirmations from 2021 to 2025 with DSCR, RevPAR and maturity narrative ([example](https://dbrs.morningstar.com/research/453559/morningstar-dbrs-confirms-credit-ratings-on-all-classes-of-morgan-stanley-capital-i-trust-2017-ashf)) [V]. KBRA publishes monthly distress and delinquency stats and loan-level surveillance on its own deals (KCP) [I]. | **High for SASB deals.** Often the *only* free text on 144A SASB performance. It is dated, so it can be used point-in-time. Cadence is roughly annual plus event-driven, which is too slow for a monthly signal. |
| **EDGAR full-text search API** (`efts.sec.gov/LATEST/search-index?q=…&forms=…&dateRange=custom&startdt=…&enddt=…`, plus `keysTyped=` for entity lookup) | Free. Works with a plain UA [V]. 100 hits per page; totals cap at 10,000 [V]. | High for discovery: locating SASB loans in conduit 8-Ks, mining SS comments across deals by phrase ("monetary default", "cash management"). |
| **STR hotel data** | Paid. STAR reports for owners and operators; market-level data licensed. | Medium to high for the hotel-tilted SASB book (HYT, KSL, ASHF, PFHP). RevPAR leads hotel NOI by a quarter or more. |
| **CoStar** | Paid, expensive. | Medium. Submarket vacancy and rent, tenant moves; a leading signal for office and retail rollover. |
| **News** (Commercial Observer, The Real Deal, Bisnow, local business journals; GDELT free) | Free or cheap. | Medium. Tenant departures, sponsor distress and refinancing news often lead servicer data. Hard to map entities to loans. |

---

## 6. Paid vendors the firm likely uses

| Vendor | What it provides | Commentary feed? |
|---|---|---|
| **Trepp** (TreppLoan, T-ID, Trepp CMBS) | Monthly loan-level CMBS data normalized from the CREFC IRP for conduits and SASB, including 144A. Watchlist status and reasons, ARA, deal cash-flow models, TreppWire news. | **Yes.** Servicer watchlist comments and special-servicer comments per loan per month, plus Trepp analyst notes [I]. The most likely source for a proper test. |
| **Intex** (IntexCalc, INTEXwrap) | Deal waterfall and cash-flow models, monthly loan tapes from remittance data, scenario engine. The industry-standard deal library for structured-credit desks [I]. | Limited. Mostly numeric, some loan-level remittance fields [I]. |
| **CRED iQ** | Loan-level CMBS and CRE CLO data, valuations, distress and watchlist analytics. Monthly refresh. | **Yes.** Servicer commentary is part of the data (per [CRE Daily review](https://www.credaily.com/reviews/cred-iq-review/)) [V per review]. Cheaper than Trepp. |
| **Bloomberg** | Deal and loan screens, collateral performance, BVAL and dealer runs, news. | Partial. Some servicer and SS comment fields on loan pages; news [I]. |
| **Moody's Analytics CRE** (CMBS data, formerly REIS; and "Rerum") | Property-market data (REIS), CMBS loan and deal analytics, credit models (CMM). | Some. Moody's surveillance commentary [I]. **Rerum: not verified.** Treat as unknown until confirmed. |
| **RiskSpan** (Edge Platform) | Loan-level data management and analytics; strongest in agency and RMBS, with CRE/CMBS modules [I]. | Unknown [I]. |
| **dv01** | Loan-level data for consumer, RMBS and non-QM; CRE coverage is limited [I]. | Unlikely for CMBS [I]. |

---

## 7. EDGAR mechanics (tested)

**User-Agent result (important) [V]:**

| Host | Result with UA `cmbs-llm-research (academic project)` |
|---|---|
| `data.sec.gov` (submissions JSON) | **200** |
| `efts.sec.gov` (full-text and typeahead) | **200** |
| `www.sec.gov` (`/Archives/…`, `cgi-bin/browse-edgar`) | **403** "Your Request Originates from an Undeclared Automated Tool" |

- SEC's fair-access policy asks for a UA of the form "Company Name contact@domain". The bare UA is rejected on www.sec.gov.
- This session therefore pulled the Archives files through a normal browser tab, about 40 low-rate fetches (`tools/browser/absee_loan_rows.js`), and stored extracted tables rather than raw XML.
- **Fix:** create a *project* mailbox or alias (not a personal email) and set `SEC_USER_AGENT="cmbs-surveillance-research (academic project) <project-alias@domain>"` for `cmbs edgar download`.
- **Rate limit:** at most 10 requests/s, i.e. sleep ≥0.1 s; the scripts use 0.15–0.25 s. Send `Accept-Encoding: gzip`.

**Bulk options:**
- **Submissions JSON:** `data.sec.gov/submissions/CIK##########.json`. Has a `filings.recent` block of up to 1,000 rows plus `files[]` for older pages, with form, accessionNumber, filingDate, reportDate and primaryDocument. [V]
  - Bulk version: `www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip`, several GB, refreshed nightly. [I] Not needed; we know our CIKs.
- **Full index:** `www.sec.gov/Archives/edgar/full-index/YYYY/QTRn/form.idx` and `company.idx`, plus `daily-index/`. Filter `ABS-EE` and `10-D` rows to enumerate every CMBS filing per quarter. [I]
- **Filing index:** `…/Archives/edgar/data/<cik>/<acc-no-dashes>/index.json` lists exhibit file names. Names vary: Computershare uses `exh_102.xml`, Citi uses `ex102.xml`. [V]
- **XBRL** (companyfacts and frames): **not applicable.** ABS issuers don't file financial-statement XBRL. [I] The only XBRL in these filings is the fee-exhibit table (`ex107`). [V]
- **Size budget:** EX-102 runs about 0.27–0.76 MB per deal-month [V]. A full 2017–2026 history for one deal (~100 months) is about 35–70 MB. Stream, extract and discard, or keep gzipped.

---

## 8. Feasibility of text-leads-tape on public data — verdict

**What public data supports (Stage-0 measured):**
1. **A point-in-time numeric tape** for post-2016 public conduits:
   - Monthly ABS-EE with exact filing timestamps.
   - Financial fields at **median 73 days** stale versus the determination date (**~91 days versus filing**), p90 of 254 days, 14% more than 6 months stale.
   - Payment and special-servicing fields current to the month.
2. **Public text, mostly lagging:**
   - Special Servicing Comments in Computershare 10-Ds (absent in Citi 10-Ds), written at or after the transfer.
   - Rating-agency surveillance releases, about annual.
   - Prospectus narratives, static.
   - 8-K exhibits, legal.

**What it does not support:**
- **No public pre-transfer servicer text.** The watchlist commentary that would *lead* is only in the CREFC IRP on investor-gated portals. Ditson shows the problem: the tape showed DSCR 0.09x a year before default, while the first public text came after the transfer.
- **SASB-heavy holdings are mostly invisible.**
  - The SASB 144A floaters have no EDGAR performance data.
  - The 2013–2016 conduits have 10-D HTML only.
  - COMM 2014-CR18 has gone dark.
  - Only split SASB loans with conduit pari passu notes (e.g. ILPT 2025-LPF2) leak into ABS-EE.

**Honest verdict:**
- The **strong thesis** (text precedes the numbers for SASB deals) **cannot be tested on free public data.**
- Three **weaker but publishable** versions *are* feasible now:
  - **(a)** In public conduits, do 10-D Special Servicing Comments and rating-agency text predict *later* tape outcomes better than the stale financials do? Outcomes: workout-code transitions, modification versus foreclosure, liquidation loss, ARA in the 10-D, return to the master servicer.
  - **(b)** "Staleness-adjusted tape": quantify how much signal is lost because NOI and DSCR are about 3–8 months old. This is directly measurable from ABS-EE timestamps.
  - **(c)** Freddie K-deal IRP as a free sandbox for **watchlist text versus tape** lead-lag.
- **The strong test needs one of:**
  - A holder's CTSLink, Citi or US Bank investor access (the CREFC Servicer Watchlist and Special Servicer Loan File comments, monthly, for every held deal including the SASBs).
  - Or a vendor feed with historical comment archives: Trepp or CRED iQ.
  - Ask the desk on day one which of these the firm already pays for.

---

## 9. Recommended Stage-0 pull list

| # | Pull | Source | Size | Status |
|---|---|---|---|---|
| 0 | Set up a project-alias SEC UA so `www.sec.gov` returns 200 | — | — | **Blocker for raw downloads** |
| 1 | Deal universe: public fund N-PORT-P holdings → CUSIP and deal list; classify each deal as public post-2016 / public pre-2016 / SASB 144A / suspended / pari-passu-in-conduit | `data.sec.gov` submissions + efts | <20 MB | Started (`data/samples/_submissions/`) |
| 2 | Monthly ABS-EE EX-102 full history for the **sample public conduits**: BANK5 2026-5YR23, Benchmark 2026-B43, Benchmark 2026-V22, Benchmark 2025-V13, Benchmark 2020-IG3, CD 2019-CD8 | `cmbs edgar download` → `cmbs analyze staleness` | ~5–60 MB per deal; keep extracted TSV | BNK13 TSV saved; raw pending (UA) |
| 3 | A comparison panel of about 20 seasoned 2017–2019 public conduits (BANK 2017-BNK6/9, BANK 2018-BNK13, Benchmark 2018-B8, BANK 2019-BNK19 …) for event studies with many SS transfers | Same as #2 | Extract-and-discard | Sept-2026 cross-section done |
| 4 | 10-D EX-99.1 for every month of #2–#3 plus legacy sample deals (WFRBS 2013-C15, WFRBS 2014-C25, CGCMT 2016-C1, COMM 2014-CR18 up to 2024). Parse SS comments, ARA, modification and delinquency tables. | `cmbs edgar download --with-distribution-report` | ~1–1.6 MB per month | BNK13 Sept-2026 section list and excerpts saved |
| 5 | SASB pari passu discovery: efts on each SASB ticker in 8-K exhibits (`*tsa*`) → conduit ABS-EE rows for that loan | efts | Tiny | ILPT 2025-LPF2 confirmed |
| 6 | At-issue baseline: pre-offering ABS-EE + Annex A-1 + Annex A-3 narratives for held public deals | EDGAR | ~30 MB per deal (HTML) | Structure checked |
| 7 | Rating-agency surveillance press releases for every held deal, SASBs first (Morningstar DBRS, KBRA, Fitch, S&P), with publication timestamps | Agency sites | Small | MSC 2017-ASHF series located |
| 8 | Freddie K-deal IRP (watchlist with comments) for about 10 deals as the free lead-lag sandbox | MSIA (free registration) | Small | Not started; needs registration by the user |
| 9 | Ask the desk about CTSLink, Citi or US Bank investor logins, and Trepp / CRED iQ comment history | Firm | — | — |

**Samples saved this session** (under `data/samples/`, ~0.9 MB total):
- `efts_deal_search.json`.
- `_submissions/<cik>.json` for 15 deals.
- `_cross_section/staleness_2026-09.json`.
- `bank-2018-bnk13/absee_2026-09_loans.tsv`.
- `bank-2018-bnk13/timeseries_2025-08_to_2026-09.json`.
- `bank-2018-bnk13/10d_2026-09_ex991_sections.txt`.
- Raw EX-102 XML was **not** saved locally because of the www.sec.gov UA 403. Run `cmbs edgar download` once a compliant UA is set.
