# 23 — Making the trust stack readable to PMs, risk, ops and engineers

*Collected 2026-10-24. Priority: level 1.5, to be done after the product and trust design. Tags: **[V]** verified against the linked source today, **[I]** inference or recommendation.*

Note: `PLAN.md` names pytest-bdd, but this brief assumes behave + behavex. The recommendations below work with either; only the report tooling differs (§1).

## Summary: the minimal set for a 4-day build (about 1 day of effort in total)
| # | Move | Effort | Audience |
|---|---|---|---|
| 1 | **Declarative, BRIEF-style Gherkin**: write the features so a PM can read them unaided | Free if done while writing the features | PMs, risk |
| 2 | **behavex `report.html` + a `--dry-run` "spec report"**, published by CI to GitHub Pages | ~1 h | everyone |
| 3 | **5 theorem cards** (Markdown, one per Lean theorem) instead of a blueprint site | ~2 h | quants, PMs |
| 4 | **"Verified by" chips + a source-text drawer + LLM confidence tier**, driven by one `provenance` table | ~3–4 h | PMs, ops |
| 5 | **README** for a 10-minute walkthrough, one Mermaid diagram, `GLOSSARY.md`, 3 ADRs | ~2 h | engineers, reviewers |
| 6 | **One-page "How we know these numbers are right"** (template in §5) | ~1 h | PMs |

Skip for now **[I]**: Allure, leanblueprint/Verso Blueprint, doc-gen4 hosting, Evidence.dev, CodeTour, D2. Each is a good tool, but each costs setup time that a 5-theorem, single-repo demo doesn't need.

---

## 1. Living documentation from BDD

**What behavex gives you [V]** ([reporting docs](https://behavex.readthedocs.io/reporting.html), [README](https://github.com/hrcorval/behavex)):
- `<output_folder>/report.html`, with `output/` as the default folder (`-o` changes it).
- The report contains per-scenario status, per-scenario logs, linked evidence files (anything copied to `context.evidence_path`) and screenshots via `behavex-images`. Its metrics are automation rate, pass rate, a step-execution counter and average step time.
- It also writes `report.json` and JUnit XML per feature under `output/behave/*.xml`.
- `--dry-run` produces an HTML report listing the scenarios without running them. The docs say this report "can be shared with stakeholders for spec review."
- Parallel runs (`--parallel-processes`) are merged into the same report.
- Current line: v4.6.x.

**Static hosting.** The docs don't mention hosting **[V]**. The output is plain HTML plus files, so uploading `output/` with GitHub's Pages actions (`actions/upload-pages-artifact` → `actions/deploy-pages`; [GitHub docs](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)) should just work **[I]**. If the repo stays private, commit a snapshot as `docs/acceptance-report.html` and open it locally during the demo.

**Alternatives:**
- **Allure** (`allure-behave` formatter → `allure generate` → static `allure-report/`) [V] ([Allure docs](https://allurereport.org/docs/behave/)).
  - Richer than behavex: it maps Gherkin tags to epic/story/owner labels, supports links to issues and specs, and turns Scenario Outline examples into parameters.
  - Costs: Allure 3 needs a Node CLI, and its PyPI README warns that some features break under behave-parallel [V] ([PyPI](https://pypi.org/project/allure-behave)).
  - Verdict **[I]**: not worth the extra toolchain for this demo.
- **Plain Gherkin-to-docs.** The `.feature` files render readably on GitHub as-is. A 20-line script that concatenates them into `docs/requirements.md` with pass/fail from `report.json` is a viable no-dependency option **[I]**.

**Writing Gherkin PMs can read [V]:**
- Cucumber's guidance: *"Declarative style describes the behaviour of the application, rather than the implementation details"* ([cucumber.io](https://cucumber.io/docs/bdd/better-gherkin/)).
- Seb Rose's **BRIEF** ([Cucumber blog](https://cucumber.io/blog/bdd/keep-your-scenarios-brief)):
  - **B**usiness language
  - **R**eal data
  - **I**ntention-revealing (no UI or mechanics)
  - **E**ssential details only
  - **F**ocused on one rule per scenario
  - **B**rief: about 5 lines or fewer

Applied to this domain **[I]**:
```gherkin
# Bad (imperative, pipeline-level)
Given I load "abs-ee_2026-09.xml" into the staging table
When I run the aggregate_pool job with flag --recon
Then column "end_bal" equals sum of "beg_bal" minus "sched_prin" ...

# Good (declarative, desk language)
Scenario: Pool balance rolls forward across a remittance period
  Given deal "BX 2021-VOLT" had a pool balance of $1,000.0M in August
  And loans paid down $4.2M of principal and $0.0M was liquidated in September
  When the September remittance is processed
  Then the September pool balance is $995.8M
  And it matches the trustee's reported balance to the cent
```
Use tags as the bridge to the other layers, e.g. `@theorem:balance_rollforward @contract:pool_balance`. The UI and the report can then cross-link on these tags **[I]**.

## 2. Making Lean accessible

**Tooling status (verified 2026-10-24):**
| Tool | What it is | Status |
|---|---|---|
| **leanblueprint** (Massot) | A plasTeX plugin: LaTeX with `\lean{}`, `\leanok` and `\uses{}` builds HTML, a PDF and a dependency graph. `checkdecls` checks that the named declarations exist. `leanblueprint new` scaffolds a GitHub Pages CI workflow. | Mature, the de facto standard [V] ([repo](https://github.com/PatrickMassot/leanblueprint)) |
| **Verso Blueprint** | A Verso-native blueprint. Lean is linked via `@[blueprint]` attributes or `(lean := "Name")`, progress is computed from `sorry`s, and output goes to `_out/site/`. | v4.35.0 is "the sole maintained release line"; FLT and Sphere Packing are published with it; the widget surface is experimental [V] ([repo](https://github.com/leanprover/verso-blueprint)) |
| **Verso** | Lean-native document system; its HTML has hovers, go-to-definition and rendered proof states | "Undergoing change at a rapid pace"; the renderer is experimental [V] ([repo](https://github.com/leanprover/verso)) |
| **doc-gen4** | API docs for a Lake package (`docbuild/` subproject, `lake build X:docs`) | Active; must be served over HTTP, not `file://` [V] ([repo](https://github.com/leanprover/doc-gen4)) |

**How others present proofs to non-experts:**
- **Mathlib** organizes its overview by ordinary maths area. It uses conventional names ("Fubini's theorem") linking to the doc-gen4 page, and points newcomers to a simpler "undergraduate" list [V] ([overview](https://leanprover-community.github.io/mathlib-overview.html)). The pattern is a human name first, with the formal object one click away.
- **AWS Cedar** doesn't sell the proofs. It sells three things [V] ([Lean use case](https://lean-lang.org/use-cases/cedar/), [paper](https://arxiv.org/abs/2407.01688)):
  - a readable Lean *model* about 10× smaller than the Rust code;
  - millions of differential tests showing that production code matches the model;
  - a release rule: no version ships unless the model, proofs and differential tests are current.

  The paper reports 4 bugs found by proofs and 21 by differential and property-based testing. **[I]** This is the right framing for a PM: "the spec is proven; the production code is tested against the spec at scale; nothing ships otherwise."

**Theorem cards (recommended instead of a blueprint) [I].** With about 5 theorems a dependency graph adds nothing. Put one Markdown section per theorem in `docs/theorems.md`:

```markdown
### T1 · Balance roll-forward   `CmbsSpec.balance_rollforward`   ✅ proved (no sorry)
**Plain English:** End-of-period pool balance = start balance − scheduled principal
− unscheduled principal − realized losses, for every deal and period. Nothing leaks.
**Worked example:** 1,000.0 − 3.1 − 1.1 − 0.0 = 995.8 ($M). BX 2021-VOLT, Sep-2026.
**Why it matters:** A broken roll-forward silently misstates paydowns and CPR.
**Protects:** `pipeline/aggregate.py::pool_balance` · Hypothesis diff-test `tests/test_lean_diff.py::test_rollforward`
**Related scenario:** `features/pool_balance.feature:12` · **Contract:** `contracts/pool.py::PoolSchema.end_bal`
**Not covered:** Whether the *inputs* from the trustee are right — that is the recon check.
```
Every card needs the "Not covered" line. It stops the proof being oversold, and it signals the honesty reviewers look for **[I]**. Add a CI check that each card's Lean name exists, in the spirit of `leanblueprint checkdecls` (a grep, or `#check` in a small Lean file) **[I]**.

## 3. In-app trust surfacing

**Patterns from data tools [V]:**
- **dbt docs**: one YAML holds descriptions *and* tests per column. The site shows the lineage DAG and which tests guard each column, and dbt Docs v2 is a static site querying via DuckDB-WASM ([dbt](https://docs.getdbt.com/docs/build/documentation)). The lesson is that documentation and checks share a source, so they can't drift.
- **Great Expectations Data Docs**: static pages rendered from expectation suites and validation results, rebuilt by a Checkpoint action ([GX Core](https://docs.greatexpectations.io/docs/core/configure_project_settings/configure_data_docs/)). The lesson is a pass/fail history per dataset that people can read.
- **Monte Carlo Data Products**: assets grouped by business outcome, with upstream assets added via lineage. Its health score is monitors passing on the last run ÷ total monitors ([docs](https://docs.getmontecarlo.com/docs/using-data-product-dashboards), [data quality](https://docs.getmontecarlo.com/docs/data-quality)). The lesson is one aggregate number per *business object*, here a deal.
- **Evidence.dev**: Markdown + SQL compiled to a static site, with DuckDB in the browser, so reports are versioned like code ([MotherDuck glossary](https://motherduck.com/glossary/evidence/)). It's useful for a "data quality report" page, but skip it here.

**LLM confidence [V]:** Google PAIR advises the following ([guidebook](https://pair.withgoogle.com/chapter/explainability-trust/)):
- Show confidence only if it changes what the user does.
- Prefer **categorical** levels with a stated action per level over raw percentages.
- Use **N-best alternatives** when confidence is low.

**Recommended design [I]:**
- **One `provenance` table** keyed by `(deal, loan, period, field)`, with these columns:
  - `source_kind` (tape | filing_text | derived | llm)
  - `filing_url`, `char_span`
  - `contracts_passed[]`, `scenarios[]`, `theorems[]`
  - `llm_confidence_tier`, `extractor_version`
- **Chips next to each number.** Example: `✓ T1 · S4 · C2` in muted grey when everything passes, amber when a check is skipped, red when it fails. Hovering shows names ("T1 Balance roll-forward — proved").
- **Click-through drawer.** It shows the filing excerpt with the cited span highlighted, links to the EDGAR filing, the extraction prompt/model version, and the eval accuracy for that field type.
- **LLM fields get three tiers.** **High** (span-quoted, schema-valid, consistent with the tape): show normally. **Medium**: show with a dotted underline that says "review." **Low**: hide the value and show "Needs analyst" plus the top-2 candidate readings.

  Pair the tiers with *measured* per-field accuracy from the eval set, not the model's self-reported score.
- **Deal-level "Data health" panel.** It shows:
  - last load time;
  - recon to remittance (Δ $ to the cent);
  - contracts passed/total (Monte Carlo-style ratio);
  - scenarios passed/total;
  - theorems proved/total;
  - count of LLM fields at Medium/Low.

## 4. Codebase onboarding for engineers and quants

**Diagrams:**
- **Mermaid** renders natively in GitHub Markdown. D2 does not ([GitHub docs](https://docs.github.com/en/get-started/writing-on-github/working-with-advanced-formatting/creating-diagrams)) [V].
- D2 has a nicer CLI, exports SVG/PNG/PDF/PPTX and has 3 layout engines [V] ([d2lang.com](https://d2lang.com/)), but it needs a build step.
- → Use **Mermaid** for one flowchart: EDGAR → parse → LLM extract → contracts → DuckDB → API → UI, with the Lean, BDD and eval checks drawn as side-gates **[I]**.

**ADRs** (Nygard-style fields per the [MS engineering playbook](https://microsoft.github.io/code-with-engineering-playbook/design/design-reviews/decision-log/): title, date, status, context, decision, consequences) [V]. Write 3 short ADRs **[I]**:
1. Why Lean only for aggregation invariants.
2. LLM output typed and span-cited, never free text.
3. DuckDB + Parquet over a database server.

These double as walkthrough talking points.

**Glossary:** use `GLOSSARY.md` (the `domain-modeling` skill already targets this). Give each term a one-line meaning, the canonical column name, and the tape field number. CMBS terms (WAC, DSCR, special servicing, ASER) are where engineers will stall **[I]**.

**CodeTour** is a Microsoft-org VS Code extension with JSON `.tours/*.tour` files of step/file/line items ([repo](https://github.com/microsoft/codetour)) [V]. It's nice to have, but in a live demo you'll drive the walkthrough yourself, so a README "Tour" section with file links does the same job **[I]**.

**README structure for a 10-minute walkthrough [I]:**
1. **One sentence + a screenshot** (the thesis: text leads tape). *0:30*
2. **Run it**: `make demo`, which runs from cached filings and needs no API key. *1:00*
3. **Architecture**: the Mermaid diagram. *1:30*
4. **How we know it's right**: a table of the 4 layers (BDD / Lean / contracts+recon / extraction evals), each with a link and a live status badge. *2:00*
5. **Tour**: 6 file links in reading order (feature → step def → pipeline fn → Lean theorem → diff test → UI provenance drawer). *3:00*
6. **Results / honest limits**: what the lead-lag test found, sample size, what isn't verified. *1:30*
7. **Decisions & glossary**: links to ADRs and `GLOSSARY.md`. *0:30*
8. **How it was built with AI**: `CLAUDE.md`, `docs/ai-catch-log.md`.

## 5. One-page explainer template (PM audience) [I]

> **How we know these numbers are right — [Deal / Panel], as of [date]**
>
> **What you're looking at:** [1 sentence: e.g., loan-level surveillance panel for N deals, built from EDGAR ABS-EE tapes and servicer commentary.]
>
> **Four independent checks. A number reaches you only if all of them pass:**
> 1. **It ties out.** Pool balances, paydowns and delinquencies reconcile to the trustee's remittance report *to the cent*, every month. *[Last run: 412/412 deal-months tie.]*
> 2. **The maths can't drift.** The core formulas are mathematically proven for *every possible input*, not just the ones we tested:
>    - balances roll forward;
>    - every loan sits in exactly one delinquency bucket;
>    - WAC stays between the lowest and highest coupon.
>
>    Production code is checked against the proven version on [10,000] random pools per build. *[5/5 proven.]*
> 3. **It does what the desk asked.** [N] plain-English acceptance scenarios, written with [PM/quant], run on every build. *[Link to report — 38/38 pass.]*
> 4. **AI-read fields show their homework.** Every value extracted from servicer commentary carries a quote and a link to the filing. Accuracy is measured on a hand-labelled set *[e.g., 94% on watchlist reason; 88% on DSCR mentions]*. Low-confidence values are withheld and flagged for an analyst.
>
> **What this does *not* guarantee:** the servicer's own data being true; anything not in the public filings; the predictive value of the signals (see research note).
>
> **If something looks wrong:** click the ✓ chip next to any number to see its source and checks; then [contact/issue link].

Keep it to one page. Use real numbers from the last run, not adjectives. The "does not guarantee" box is mandatory **[I]**.

## Sources
- BehaveX: [reporting](https://behavex.readthedocs.io/reporting.html) · [GitHub](https://github.com/hrcorval/behavex)
- Allure: [behave docs](https://allurereport.org/docs/behave/) · [PyPI](https://pypi.org/project/allure-behave)
- Cucumber: [better Gherkin](https://cucumber.io/docs/bdd/better-gherkin/) · [BRIEF](https://cucumber.io/blog/bdd/keep-your-scenarios-brief)
- GitHub Pages: [custom workflows](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages) · [diagrams](https://docs.github.com/en/get-started/writing-on-github/working-with-advanced-formatting/creating-diagrams)
- Lean tooling: [leanblueprint](https://github.com/PatrickMassot/leanblueprint) · [Verso Blueprint](https://github.com/leanprover/verso-blueprint) · [Verso](https://github.com/leanprover/verso) · [doc-gen4](https://github.com/leanprover/doc-gen4) · [mathlib overview](https://leanprover-community.github.io/mathlib-overview.html)
- Cedar: [Lean use case](https://lean-lang.org/use-cases/cedar/) · [arXiv 2407.01688](https://arxiv.org/abs/2407.01688)
- Data tools: [dbt docs](https://docs.getdbt.com/docs/build/documentation) · [GX Data Docs](https://docs.greatexpectations.io/docs/core/configure_project_settings/configure_data_docs/) · [Monte Carlo Data Products](https://docs.getmontecarlo.com/docs/using-data-product-dashboards) · [Monte Carlo data quality](https://docs.getmontecarlo.com/docs/data-quality) · [Evidence (MotherDuck)](https://motherduck.com/glossary/evidence/)
- [Google PAIR: Explainability + Trust](https://pair.withgoogle.com/chapter/explainability-trust/)
- Onboarding: [MS playbook: ADRs](https://microsoft.github.io/code-with-engineering-playbook/design/design-reviews/decision-log/) · [CodeTour](https://github.com/microsoft/codetour) · [D2](https://d2lang.com/)
