# Stage 0: explore before building

**Purpose:** learn the industry, the firm, its holdings and its likely methods well enough to:
- (a) hold your own in conversation with a CMBS desk, and
- (b) check whether the "text leads tape" thesis holds up on public data before we build on it.

Every question below has a method, a source, and an output file. Status: 🔄 = agent running (2026-10-24), ⏳ = next.

## Q1. The market: how big is it, who are the players, how liquid is it? 🔄
- **Method:** desk research. Every number is sourced and dated.
- **Sources:**
  - Data publishers: Trepp (monthly delinquency report), CREFC, SIFMA, the MBA, Fed Z.1
  - Trading data: FINRA TRACE securitized-products statistics
  - Trade press and research: Commercial Mortgage Alert, KBRA/Fitch/Moody's research
  - Firm websites
- **Output:** `10-market-primer.md` (includes a glossary).

## Q2. Data: what's public, at what lag, and where does the text live? 🔄
Methods and sources:
- Read the SEC Reg AB II rules and the Schedule AL field list.
- **Pull real filings** for held deals: the ABS-EE loan-level data (EX-102) and 10-D distribution reports (EX-99.1).
- Check what the CREFC reporting package (IRP) contains and who can access it.
- Survey the vendors (Trepp, Intex, CRED iQ, Bloomberg).

**Key measurement:** across loans in a real filing, how many months old is the "most recent financials" date relative to the reporting period?
- That gap is the window in which text could lead the tape.
- We also need to know whether 144A SASB deals file ABS-EE at all. If they don't, the demo uses public conduits plus SASB 10-D text.

**Output:** `11-data-sources.md` plus samples in `data/samples/`.

## Q3–Q4. Holdings and firm research
Kept outside the repo.

## Q5. Methodology: how do structured-credit desk quants model CMBS? ⏳ (after Q1–Q4)
- **Method:** the literature on CMBS default and maturity-default models (e.g. An/Deng/Sanders, Titman/Tsyplakov), rating agency CMBS methodologies (KBRA, Moody's, S&P), and vendor docs (Intex's CMBS loan-level approach, Trepp's default models).
- **Output:** `14-methods.md`. Focus on which inputs each model needs, which tells us which panel fields matter.

## Q6. Thesis feasibility check ⏳ (gates the build)
Decide **go**, **reshape** or **pivot** using:
- Q2's staleness measurement;
- whether commentary exists in public filings;
- sample size, i.e. the number of held deals with public filings over enough months.

**Fallbacks if the public text is too thin:**
- Use the commentary in 10-D reports of public conduits holding hotel loans.
- Or measure "text leads tape" on the *watchlist/delinquency* timing within the 10-D itself.

## Q7. Learning track (for you, in parallel) ⏳
1. Read `10-market-primer.md` (the "If you remember nothing else" summary and the glossary first).
2. Then walk one real deal end to end with me: its prospectus summary → capital structure → one 10-D distribution report → its loan tape rows.

Knowing one deal concretely beats any amount of abstract theory.

## Working rules
- **Tag every claim:** [V] (verified, with a link) or [I] (inference).
- **Verify before believing:** primary sources over summaries, and never treat a tool's "not found" as proof (see `docs/ai-catch-log.md`).
- **No personal data in requests:** EDGAR requests use a project User-Agent with no personal email.
