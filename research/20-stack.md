# 20 — Tech stack for the CMBS demo (A1)

As of 2026-10-24. Tags: **[V]** = checked against a primary source (link) or run locally on this laptop today; **[I]** = inference or judgement; **[U]** = not verified.
Versions come from the PyPI JSON API / npm registry / GitHub releases API, all queried 2026-10-24.

> **Conflict to settle:** PLAN.md Part B2 names **pytest-bdd**; this brief asks for **behave/behavex**. This doc follows the brief. The trade-off: behavex gives a PM-friendly HTML report plus parallelism for free. pytest-bdd (9.0.0, 2026-09-30 [V](https://pypi.org/project/pytest-bdd/)) reuses pytest fixtures. Pick one; don't run both.

## Recommendation table

| Component | Choice | Version | Why | Alternative |
|---|---|---|---|---|
| A/B architecture | **One Python package `cmbs`** (core → CLI → FastAPI); the Claude skill calls the CLI | — | One source of truth; the skill and the app are thin adapters | Core as an MCP server (Option 2) |
| Python | CPython 3.13 | 3.13 | Every pin below installed and ran on 3.13 [V, local]. DuckDB/pandas need ≥3.11 | 3.14 [U] |
| Env/packaging | uv | 0.13.5 (2026-10-22) [V](https://pypi.org/project/uv/) | Lockfile, `uv run`, fast CI. Local copy is 0.12.10, so run `brew upgrade uv` | — |
| Lint/format | ruff | 0.17.2 (2026-10-22) [V](https://pypi.org/project/ruff/) | One tool for lint and format | — |
| Type checker | **pyright** (strict on `src/`) | 1.1.414 (2026-09-10) [V](https://pypi.org/project/pyright/) | Stable, fast, good inference for Pydantic and Polars | mypy 2.4.0 [V](https://pypi.org/project/mypy/); ty is beta |
| Models / API schema | pydantic v2 | 2.14.1 (2026-10-11) [V](https://pypi.org/project/pydantic/) | Typed LLM output fields and FastAPI models | — |
| DataFrames | **polars** | 2.0.0 (2026-10-06) [V](https://github.com/pola-rs/polars/releases/tag/py-2.0.0) | Strict dtypes, lazy queries, zero-copy with DuckDB | pandas 3.1.0 [V](https://pypi.org/project/pandas/) |
| Warehouse | DuckDB | 2.0.0 LTS (2026-10-21) [V](https://duckdb.org/2026/10/21/announcing-duckdb-200) | Medallion schemas in one file, SQL that quants read | 1.5.6 (supported to 2027-03) |
| Runtime contracts | pandera (`pandera.polars`) | 0.34.1 (2026-10-06) [V](https://pypi.org/project/pandera/) | Recognised name, Polars backend, Hypothesis strategies | dataframely 3.1.2 [V](https://pypi.org/project/dataframely/) |
| Property tests | Hypothesis | 6.170.9 (2026-10-23) [V](https://pypi.org/project/hypothesis/) | Generates pools for the differential tests against the Lean oracle | — |
| Unit tests | pytest | 9.1.1 (2026-06-19) [V](https://pypi.org/project/pytest/) | Standard | — |
| BDD | behavex over behave | 4.6.8 (2026-10-21) / behave 1.3.3 [V](https://pypi.org/project/behavex/) | HTML report for PMs, tag expressions, parallel runs | pytest-bdd 9.0.0 |
| HTTP API | FastAPI + uvicorn | 0.162.0 / 0.54.0 [V](https://pypi.org/project/fastapi/) | Pydantic → OpenAPI → TS client with no hand-written types | Litestar 2.25.0 [V](https://pypi.org/project/litestar/) |
| CLI | Typer | 0.27.3 [V](https://pypi.org/project/typer/) | Type-hinted commands that the skill calls | argparse |
| EDGAR fetch / XML | httpx + lxml | 0.28.1 / 6.1.3 [V](https://pypi.org/project/lxml/) | Explicit User-Agent and rate limiting; streaming XML for ABS-EE | edgartools 5.62.1 [V](https://pypi.org/project/edgartools/) |
| Frontend | React + TypeScript + Vite | 19.3.1 / 7.0.2 / 8.3.5 [V](https://www.npmjs.com/package/vite) | Required; Vite 8 builds with Rolldown | — |
| Data fetching | TanStack Query | 5.104.5 [V](https://www.npmjs.com/package/@tanstack/react-query) | Caching and refetch; orval generates the hooks | — |
| Routing | TanStack Router (code-based) | 1.170.46 [V](https://www.npmjs.com/package/@tanstack/react-router) | Typed search params (`?deal=&period=`) hold the terminal state | React Router 8.5.0 |
| API contract | **orval** → TanStack Query hooks + zod | 8.44.0 / zod 4.6.7 [V](https://www.npmjs.com/package/orval) | One generator: hooks plus runtime response validation | @hey-api/openapi-ts 0.99.0 |
| Grid | AG Grid Community | 36.2.0 [V](https://www.npmjs.com/package/ag-grid-community) | Dense dark grid with sort, filter and pinned columns; MIT | Perspective 5.6.1 (stretch) |
| Time series | TradingView lightweight-charts | 5.2.1 [V](https://www.npmjs.com/package/lightweight-charts) | Terminal look, small bundle. Needs attribution (see §3) | ECharts 6.1.0 |
| JS lint/format | Biome | 2.6.3 [V](https://www.npmjs.com/package/@biomejs/biome) | One Rust binary; doesn't need the TS JS API that TS 7 lacks | ESLint 10 + typescript-eslint (needs the TS 6 bridge) |
| UI tests | Vitest + Playwright | 5.0.5 / 1.64.1 [V](https://www.npmjs.com/package/@playwright/test) | Unit and component tests; e2e against the real API on a fixture DB | — |
| Lean | Lean 4 core, **no Mathlib** | v4.35.0 (2026-10-12) [V](https://github.com/leanprover/lean4/releases/tag/v4.35.0) | The three theorems prove with core tactics [V, local] | Mathlib `v4.35.0` tag |
| Task runner | just | 1.59.0 [V](https://github.com/casey/just/releases) | One recipe list used by humans, CI and the skill. Not installed: `brew install just` | make; mise 5.x |
| Git hooks | pre-commit | 4.7.0 [V](https://github.com/pre-commit/pre-commit/releases) | ruff, biome, no-`sorry` grep | prek 0.5.7 (Rust drop-in) |
| CI | GitHub Actions | checkout v7, setup-uv v10, setup-node v7, lean-action v1.6.2 [V](https://github.com/leanprover/lean-action/releases) | 3 parallel jobs | — |
| Node | Node 24 LTS "Krypton" | 24.22.1 [V](https://nodejs.org/dist/index.json) | Current LTS line | Node 26 (not yet LTS) |

## 1. Architecture options for the A/B split

Current Claude Code primitives, checked against the docs:

- **Skills.** `.claude/skills/<name>/SKILL.md` becomes `/<name>`. Custom commands have been merged into skills. Frontmatter includes `allowed-tools`, `disable-model-invocation` and `context: fork`. `` !`cmd` `` injects command output, and `${CLAUDE_SKILL_DIR}` points at bundled scripts. [V](https://code.claude.com/docs/en/skills.md)
- **Plugins.** A plugin packages skills, agents, hooks and MCP servers as one installable unit. It's only worth it for distribution. [V](https://code.claude.com/docs/en/plugins/overview.md)
- **Dynamic workflows.** JS scripts in `.claude/workflows/` that fan out subagents (`agent()`, `pipeline()`, `parallel()`). They can be resumed, but scripts can't read files or run a shell directly. They suit fan-out research, not a deterministic ETL. [V](https://code.claude.com/docs/en/workflows.md)
- **Agent SDK.** `claude-agent-sdk` 0.2.174 (Python) and `@anthropic-ai/claude-agent-sdk` 0.3.310 [V](https://pypi.org/project/claude-agent-sdk/). It "runs the Claude Code binary", loads `.claude/` skills, and requires **API-key auth**: third-party apps may not offer claude.ai login. [V](https://code.claude.com/docs/en/agent-sdk/overview.md)
- **MCP Python SDK.** 2.3.0 (2026-10-02). `FastMCP` was renamed `MCPServer`; transports are stdio and Streamable HTTP. [V](https://github.com/modelcontextprotocol/python-sdk/releases)

### Option 1 — One core package, three adapters (**recommended**)

```
cmbs.core  (fetch → bronze → silver → gold, contracts, classify, lineage)   ← all logic
   ├── cmbs.cli   (Typer: refresh, validate, report --json, sql)            ← Surface A
   │      └── .claude/skills/cmbs-refresh/SKILL.md  →  `uv run cmbs refresh --period $0`
   └── cmbs.api   (FastAPI, read-only over gold)  → openapi.json → orval → web/  ← Surface B
```

- **What lives where:**
  - The skill holds *no logic*. It runs `just refresh <period>` and then `cmbs report --json`, and Claude narrates the diffs and contract failures.
  - Ad-hoc research questions use a second skill, `/cmbs-ask`, with `allowed-tools: Bash(uv run cmbs sql *)`, against a **read-only** connection.
- **Avoiding two sources of truth:**
  1. Business logic lives only in `cmbs.core`, and the adapters are under ~50 lines each [I].
  2. The gold DuckDB file is the only data store. DuckDB allows one read-write process *or* many read-only processes per file [V](https://duckdb.org/docs/2.0/connect/concurrency.html). So `refresh` writes `warehouse.next.duckdb` and then does an atomic `os.replace`, and the API opens with `read_only=True` [I].
  3. Jev classifications are computed once in the pipeline and cached in silver, keyed by `(accession_no, text_sha256, model_id, prompt_version)`. Neither Claude nor the app re-classifies.
  4. `openapi.json` is committed, and CI regenerates it and fails on any diff.
  5. A unit test asserts that the Pydantic response-model fields equal the pandera gold-schema columns.
- **BDD coverage:**
  - The steps call a `Driver` protocol with two implementations: `CliDriver` (subprocess) and `ApiDriver` (FastAPI `TestClient`).
  - `features/environment.py` picks the driver from behave userdata (`-D surface=api`; behavex passes `define` through [V](https://behavex.readthedocs.io/cli-reference.html)). CI runs the same `.feature` files twice.
  - The skill is covered because it only shells out to the tested CLI.
  - The UI is covered by Playwright specs named after scenario IDs.

### Option 2 — Core exposed as an MCP server

- Use `mcp` 2.3 `MCPServer` with tools such as `refresh`, `get_pool_stats` and `sql_readonly`, registered in `.mcp.json` for Claude Code.
- The React app can't speak MCP directly, so FastAPI would have to become an MCP *client* or duplicate the routes. That means two transports and two schemas [I].
- **Good:** typed tools for Claude, and reuse by other MCP clients such as Claude Desktop.
- **Bad:** an extra protocol surface to test, and no real gain over a CLI for a single user.
- **BDD:** add an `McpDriver` behind the same protocol.
- **Verdict:** it's a day-4 stretch at most, as a ~40-line read-only wrapper over `cmbs.core` [I].

### Option 3 — Agent SDK embedded in the app ("Ask the panel")

- FastAPI hosts `claude-agent-sdk` with in-process tools over gold, and React streams the answers.
- **Costs:**
  - It needs an Anthropic API key and the Claude Code binary on the server.
  - It has branding rules ("Powered by Claude", never "Claude Code") [V](https://code.claude.com/docs/en/agent-sdk/overview.md).
  - Answers are non-deterministic, so BDD can only assert on tool calls or citations, not text.
- **Verdict:** wrong scope for 4 days. Use it as a talking point: "Surface A already *is* this agent; productising it is the Agent SDK."

## 2. Python toolchain notes

- **Type checker, honestly.**
  - Pyright is the stable choice.
  - mypy 2.4.0 (2026-10-01) is the mature alternative and the only one with the Pydantic plugin [U on plugin status in 2.x].
  - **ty 0.0.91** (2026-10-22): its README says "ty is currently in **beta**", uses `0.0.x` versioning, and "does not yet have a stable API" [V](https://github.com/astral-sh/ty). Don't gate CI on it.
- **Polars 2.0 is 18 days old.** Its breaking changes include "Exact SQL numeric literals are typed as Decimal" and Parquet ENUM read as String; out-of-core execution is on by default [V](https://github.com/pola-rs/polars/releases/tag/py-2.0.0). Secondary sources say the streaming engine is now default and no longer guarantees row order [U]. Sort explicitly before any period-over-period diff anyway.
- **DuckDB 2.0 is 3 days old** but LTS (supported to at least 2027-10).
  - Files it writes **can't be read by DuckDB <2.0**.
  - Single-arrow lambdas (`x -> x+1`) are off by default; use `lambda x: x+1`.
  - [V](https://duckdb.org/2026/10/21/announcing-duckdb-200)
  - If anything breaks, pin `duckdb==1.5.6` (1.5 is supported to 2027-03).
- **Compatibility smoke test, run locally today on Python 3.13:** `pandera[polars]==0.34.1` + `polars==2.0.0` + `duckdb==2.0.0` + `pandas==3.1.0`. A `DataFrameModel` validated, rejected a negative balance with `SchemaError`, and `duckdb.sql(...).pl()` round-tripped [V, local]. Pandera's release notes don't mention Polars 2 [V](https://github.com/unionai-oss/pandera/releases), so this smoke test is the evidence.
- **Pandas** only appears where a library forces it. pandas 3.1.0 needs Python ≥3.11 [V](https://pypi.org/project/pandas/).
- **behavex 4.6.8:**
  - **Behave support:** it requires `behave>=1.2.6,<2.0.0` and resolved to **behave 1.3.3**. Cucumber-style tag expressions (`-t="@gold and not @slow"`) need behave ≥1.3.0 [V](https://behavex.readthedocs.io/getting-started.html). A local parallel run passed [V, local].
  - **Report:** `<out>/report.html`, `report.json`, and JUnit XML per feature in `<out>/behave/*.xml` [V](https://behavex.readthedocs.io/reporting.html). The HTML has per-scenario logs, an execution timeline and metrics.
  - **Parallelism:** `--parallel-processes N --parallel-scheme feature|scenario`. Under the `scenario` scheme, **`before_feature`/`after_feature` fire once per scenario**. One-off setup goes in `before_all_workers` (new in 4.6.4) [V](https://behavex.readthedocs.io/migrating-from-behave.html).
  - **Gotcha: output folder.** `-o` is **emptied before every run**. Before 4.6.8, `-o .` deleted the project; 4.6.8 now refuses it [V](https://github.com/hrcorval/behavex/blob/master/CHANGELOG.md).
  - **Gotcha: rerun on a passing run.** Before 4.6.8, `-rf` with a missing `failing_scenarios.txt` ran *everything* and could report green. Now it exits 1 when the file is missing or empty, and the file only exists after a failure. So use `behavex || behavex -rf output/failing_scenarios.txt -o output_rerun`. **Pin ≥4.6.8.**
  - **Gotcha: stop/wip.** `stop` and `wip` don't work with more than one process [V](https://behavex.readthedocs.io/cli-reference.html).
  - **Gotcha: logs.** Parallel runs don't print step logs to the console; they go in the report.
  - **Gotcha: DuckDB locks.** Parallel workers must each use their **own DuckDB copy**, e.g. keyed on `context.behavex.worker_id`, because of the single-writer rule [I].
  - **Gotcha: muted scenarios.** `@MUTE` hides failures from JUnit. Don't use it in a trust demo.
  - **Dependency weight:** it pulls in `allure-python-commons`, `minify-html` and similar [V](https://pypi.org/project/behavex/). Harmless.
- **FastAPI 0.162.0** (2026-10-23). It emits OpenAPI 3.1 from Pydantic v2 models [U on exact spec version]. Litestar is a fine alternative but has no advantage here.

## 3. Frontend

- **TypeScript 7.0** (2026-07-08) is the native Go compiler.
  - `strict` is now on by default.
  - **It ships no programmatic API** until 7.1. Tools that need one, such as typescript-eslint, must use the `@typescript/typescript6` side-by-side package [V](https://devblogs.microsoft.com/typescript/announcing-typescript-7-0/).
  - Hence Biome instead of ESLint, and orval instead of hey-api.
- **Codegen: orval vs hey-api.**
  - @hey-api/openapi-ts declares `peerDependencies: typescript >=5.5.3` [V](https://www.npmjs.com/package/@hey-api/openapi-ts). It is still 0.x, and its last `latest` tag was 0.99.0 on 2026-06-22. Whether it breaks on TS 7 is [U].
  - orval 8.44.0 has no `typescript` dependency [V, npm metadata]. It emits TanStack Query hooks plus zod schemas in one config.
- **Vite 8** bundles with Rolldown [V](https://vite.dev/blog/announcing-vite8), via `@vitejs/plugin-react` 6.1.2.
- **Charts and grids for a terminal look:**
  - **AG Grid Community** handles the loan tape, watchlist and holdings grids (dark theme, pinned columns, number formatting).
  - **lightweight-charts 5.2.1** handles balance, delinquency and DSCR time series. Its license requires the TradingView attribution notice and a link; the `attributionLogo` option satisfies the link part [V](https://github.com/tradingview/lightweight-charts#license-and-attribution).
  - **ECharts 6.1.0** only if you need a heatmap, e.g. a delinquency transition matrix.
  - Skip **visx**: it's low-level D3 and costs days.
- **Perspective, honest assessment:**
  - **Rehoming:** the project moved from FINOS to the **OpenJS Foundation** [V](https://perspective-dev.github.io/guide/), and the npm scope moved to `@perspective-dev/*` (5.6.1, 2026-10-23). `@finos/perspective*` is **frozen at 3.8.0 (2025-09-03)**, so most tutorials are stale [V](https://www.npmjs.com/package/@finos/perspective).
  - **React support:** `@perspective-dev/react` exists, with peer `react >17 <20` [V](https://www.npmjs.com/package/@perspective-dev/react). It wraps a custom element.
  - **Bundle weight:** I measured the 5.6.1 tarballs. The WASM is `perspective-server.wasm` 2.62 MB + `perspective-viewer.wasm` 1.65 MB + `perspective-js.wasm` 0.63 MB ≈ **4.9 MB**, and it is already compressed (gzip saves under 1%) [V, local]. The d3fc chart plugin adds 12 MB unpacked.
  - **Vite setup:** WASM and worker loading is undocumented on the guide page [U].
  - **Verdict:** a superb pivot UX, but a risky 4-day dependency. At most, put it behind a lazy-loaded "Pivot" route on day 4.
- **Tests.** Vitest 5 covers formatters, the zod parse of fixtures and components. Playwright runs against `uvicorn` on a committed fixture DuckDB. **No MSW**: mocks would be a second source of truth.

## 4. Repo layout, runner, hooks, CI

```
cmbs-llm/
├── pyproject.toml  uv.lock  justfile  .pre-commit-config.yaml
├── src/cmbs/{core/{edgar,bronze,silver,gold,contracts,classify,lineage}.py, cli.py, api/}
├── tests/{unit,property,oracle}/            # oracle = Hypothesis vs Lean exe
├── features/{*.feature, steps/, environment.py}   behavex.cfg
├── lean/{lakefile.toml, lean-toolchain, CmbsSpec/*.lean, Oracle/Main.lean}
├── web/{package.json, orval.config.ts, src/, e2e/}   openapi.json (committed)
├── .claude/skills/{cmbs-refresh,cmbs-ask}/SKILL.md   CLAUDE.md
├── data/fixtures/fixture.duckdb (small, committed)    data/warehouse*.duckdb (ignored)
└── .github/workflows/ci.yml
```

- Use a single Python package, not a uv workspace. Splitting core, CLI and API into separate distributions is fluff at this size [I].
- **`justfile` recipes:** `lint`, `typecheck`, `test`, `bdd surface`, `lean`, `openapi` (dump the spec and run orval), `web-test`, `refresh period`, `ci` (all of them).
- **pre-commit:** ruff and ruff-format (the ruff-pre-commit `v0.17.2` tag [U]), plus local hooks `biome check`, `just openapi` drift, and `! grep -rn sorry lean/CmbsSpec`.

**CI (`ci.yml`): three parallel jobs**

| Job | Steps |
|---|---|
| `py` | checkout@v7 → setup-uv@v10 → `uv sync --locked` → `ruff check` + `ruff format --check` → `pyright` → `pytest tests/unit tests/property` → `behavex -D surface=cli --parallel-processes 4 -o out/cli` → same with `surface=api` → upload `out/` (upload-artifact@v7) |
| `lean` | leanprover/lean-action@v1 (runs `lake build`, caches) → `lake build 2>&1 \| tee b.log; ! grep -q "declaration uses \`sorry\`" b.log` → `lake env lean CmbsSpec/Axioms.lean` (`#print axioms` on each main theorem; fail if output contains `sorryAx`) → `lake build oracle` → `uv run pytest tests/oracle` |
| `web` | setup-node@v7 (Node 24) → `npm ci` → `just openapi && git diff --exit-code` → `tsc --noEmit` → `biome ci` → `vitest run` → `npx playwright install --with-deps chromium` → start uvicorn on the fixture DB → `playwright test` |

The exact wording of the `sorry` warning and the `sorryAx` axiom name were checked locally with Lean 4.35.0 [V, local].

## 5. Lean 4 project

- **Toolchain:**
  - Local install: elan 4.2.4, **Lean 4.35.0**, Lake 5.0.0 [V, local]. 4.35.0 is the current stable release; v4.36.0-rc1 shipped 2026-10-14 [V](https://github.com/leanprover/lean4/releases).
  - Mathlib `master` is on `v4.36.0-rc1`, and Mathlib tags `v4.35.0` [V](https://github.com/leanprover-community/mathlib4/tags).
  - Pin `lean-toolchain` to `leanprover/lean4:v4.35.0`.
- **Is Mathlib needed? No.** I proved shapes of all three theorems in core Lean 4.35.0 today [V, local]:
  - **Roll-forward:** `b0 - sched - unsched - loss ≤ b0` over `Int` by `omega`.
  - **Bucket partition:** `(xs.filter p).length + (xs.filter (!p ·)).length = xs.length` by induction plus `simp`/`omega`. Generalise it to a list of buckets.
  - **WAC bound:** `Σ wᵢrᵢ ≤ (Σ wᵢ)·m` over core `Rat`, with a structural recursion. It uses `Rat.mul_le_mul_of_nonneg_left` and `grind`; bare `grind` fails because the goal is nonlinear.
  - Mathlib only pays off if you want `Finset.sum`/BigOperators notation. The cost is a multi-GB cache download plus slower CI [U on size]. Use the `math-lax` template if you add it later.
- **Setup:** `lake new CmbsSpec std` (TOML config; the templates are `std|exe|lib|math-lax|math` [V, local]). Put definitions and theorems in `CmbsSpec/`, and an executable `oracle` that imports **the same defs** that the theorems are about.
- **Oracle protocol:**
  - The oracle reads JSON Lines on stdin using core `Lean.Data.Json`, which compiled and ran with `lean --run` [V, local]. Each line is a pool with balances in **integer cents** and rates as `{num, den}`.
  - It writes `{end_balance, bucket_counts, wac_num, wac_den}`.
  - Python starts **one** oracle subprocess per pytest session. Hypothesis generates pools, and the test asserts exact equality between the Polars implementation (cast to `Fraction`) and the oracle.
  - This gives two different guarantees. The proofs say the *spec* is right for all inputs; the differential test says the *Python matches the spec* on generated inputs.
  - Floats never cross the boundary [I].

## 6. What NOT to include (4-day build)

- **Pipeline infrastructure:**
  - No Dagster, Airflow or Prefect. `just refresh` plus a skill is the orchestrator. Dagster 1.13.26 exists [V](https://pypi.org/project/dagster/) and adds nothing for one monthly job.
  - No dbt or SQLMesh. Medallion layers are DuckDB schemas plus Python functions.
  - No Docker or Kubernetes, and no Postgres, Redis or auth. The demo runs locally, single-user and read-only.
- **Claude Code extensions:**
  - No MCP server (Option 2) and no Agent SDK in the app (Option 3) before day 4.
  - No plugin packaging and no dynamic workflow for the refresh, which is deterministic. A workflow could later fan out *extraction-eval adjudication*.
- **Frontend:**
  - No Perspective in the critical path, and no visx or D3.
  - No Redux or Zustand: TanStack Query plus URL search params is the state.
  - No MSW. No Storybook. No ESLint: Biome plus `tsc` is enough, and ESLint needs the TS 6 bridge.
- **Tooling duplicates:**
  - Don't run both pytest-bdd and behavex.
  - Don't run both pandera and dataframely, or both mypy and pyright.
  - No ty in CI while it is beta.
- **Other:**
  - No Mathlib.
  - No mise. uv, elan and `.nvmrc` already pin Python, Lean and Node.
  - No DuckDB server mode (Quack), new in 2.0. The single-writer file plus atomic swap is enough.
