# CLAUDE.md

Public CMBS filings (ABS-EE loan tapes, 10-D servicer text) turned into a verified, point-in-time surveillance panel. Layout and basic usage: `README.md`. Why each choice below was made: `docs/GRILL-LOG.md` (decision numbers in brackets).

## Stack

Exact versions live in `pyproject.toml`, `uv.lock` and `web/package.json`; this table says what exists and where.

| Layer | Choice | State |
|---|---|---|
| Core | Python 3.13, uv, one package `cmbs` in `src/cmbs`, Typer CLI `cmbs` [Q12] | Built |
| Store | PostgreSQL 18 with `vector` and `pg_trgm`; layers are schemas `raw`/`bronze`/`silver`/`gold`, application state in `app`; SQLAlchemy 2.1, each feature's tables in `cmbs.<feature>.tables`; Alembic migrations in `src/cmbs/db/migrations`, psycopg 3 [Q20, Q53, Q54, Q60] | Built |
| Frames and contracts | Polars 2.0, Pandera checks before every load [Q14, Q20] | Built: the loan-tape contract |
| API | FastAPI app in `cmbs.api.app`, each feature's router in `cmbs.<feature>.routes`; its OpenAPI schema committed as `web/openapi.json` (`tools/export_openapi.py`), the web client generated from it by orval into `web/src/generated/` [Q12, Q60] | Built |
| Web | React 19, Vite, strict TypeScript, Vitest, AG Grid Community, TradingView lightweight-charts (a loan's history, with the attribution its licence asks for), Biome in `web/`: the dashboard's book and loan views, served from the built bundle [Q13, Q52] | Built |
| Pipeline model | Jev, TypeSafe's decision model, cloud API pinned to `jev-1.14.0`, and Cloudflare's Clef-flash as the local fallback, pinned to a Hugging Face commit (`clef-flash-2026-10-01`), with its reference code vendored and its weights checked by SHA-256 (`cmbs.model.local`); in-process with `uv sync --extra local-model` or behind `tools/clef_server.py`; one provider-neutral interface (`cmbs.model`), every call recorded and replayed (`tests/fixtures/model/live/`, `tests/fixtures/model/local/`) [Q7, Q87, Q90] | Built |
| Requirements | Gherkin in `features/`, run with behave and behavex [Q5, Q29] | Built |
| UI tests | Playwright for Python inside behave steps; hooks in `features/environment.py` [Q55] | Built |
| Proofs | Lean 4 core v4.35.0 (no Mathlib) in `lean/`, every theorem audited (`Invariants/Audit.lean`: no sorry, no native_decide, standard axioms only), a compiled oracle checked against the Python by Hypothesis differential tests; CI's `lean` job is required [Q4] | Built |
| Mutation testing | mutmut on `src/cmbs` (fork-server isolation, database tests last), Stryker on `web/src`; nightly in `.github/workflows/mutation.yml`, floor 90% [Q51, Q89] | Built |
| Lint and types | pre-commit runs ruff, basedpyright (pyright, standard mode), Biome, typos, actionlint, zizmor, gitleaks and file hygiene on commit; pytest and Vitest with coverage floors on push [Q56] | Built |
| Containers | Docker Compose on OrbStack: `db`, `web`, and `api` (`Dockerfile`: `cmbs serve` with the built dashboard; it serves the API and dashboard only, and the CLI and Claude skills run on the host, so it holds no TypeSafe key) [Q19] | Built |
| Claude skills | `/cmbs-refresh`, read-only `/cmbs-ask`, both calling only the `cmbs` CLI, in `.claude/skills/` [Q12] | Built |

## Running things

```bash
uvx pre-commit install                                           # once: commit and push hooks
uvx pre-commit run --all-files                                   # every commit-stage check
docker compose up -d --wait db                                   # Postgres on localhost:5432
export DATABASE_URL=postgresql+psycopg://cmbs:cmbs@localhost:5432/cmbs   # the `cmbs` command also reads .env
uv run alembic upgrade head                                      # migrations
uv run alembic revision --autogenerate -m "<what changed>"       # new migration
uv run pytest --cov                                              # coverage floor; DB tests skip without DATABASE_URL
uv run behavex -t 'not @draft' -o output/behavex                 # feature scenarios
uv run playwright install chromium                               # once, for @surface:ui scenarios
uv run mutmut run && uv run mutmut results                       # Python mutation testing
docker compose run --rm web npm run test:coverage                # web tests (npm runs only in the container)
docker compose run --rm web npm run lint                         # Biome, same config as the hook
docker compose run --rm web npm run mutate                       # Stryker
docker compose up -d web                                         # Vite dev server on localhost:5173
uv run python tools/export_openapi.py                            # after a route change: web/openapi.json
docker compose run --rm web npm run generate                     # then the client in web/src/generated
docker compose up -d --build --wait api                          # API and dashboard on localhost:8000
```

CI (`.github/workflows/ci.yml`) runs all of the above except the dev server. Mutation testing, Python (sixteen shards, floor 90%) and web (Stryker), runs nightly and on demand in `.github/workflows/mutation.yml` (`gh workflow run mutation`), never on pull requests (grill log Q89).

## Pins and gotchas

- **Python 3.13** (`.python-version`): `minify-html`, pulled in by behavex, has no 3.15 wheel.
- **pytest below 9.2**: mutmut 3.8.0 reads `item._nodeid`, which pytest 9.2.0 removed.
- **mutmut forks from a fork server** (`process_isolation` in `pyproject.toml`): Polars is not fork-safe, so a mutant forked from a process that has run a Polars query hangs on its next one and times out.
- **torch 2.11.0 in the `local-model` extra**: the version Clef-flash's model card was tested with. Its three Dependabot alerts are dismissed as not reaching this project (grill log Q92); moving it means re-checking Clef-flash live against its recordings.
- **TypeScript 6, not 7**: TypeScript 7 drops the JavaScript compiler API that Stryker calls.
- **minimatch overridden to 11** (`overrides` in `web/package.json`): Stryker 10.1.1 pins `~10.2.4`, and the denial-of-service fix exists only from 11.0.1. Drop the override when Stryker moves to 11.
- **Node 24 LTS** in the `web` container and CI; Dependabot skips Node majors, so moving to the next LTS is a deliberate edit to `compose.yaml` and `ci.yml` together.
- **behavex ignores `default_tags` in `behave.ini`**, so every behavex run passes `-t 'not @draft'`.
- **Pinned refs**: Docker images are pinned by digest, GitHub Actions by commit SHA with the release in a comment (zizmor enforces it), pre-commit hooks by tag. Dependabot (`.github/dependabot.yml`) bumps all of these weekly, plus `uv.lock` and `web/package-lock.json`; it skips the pins below via `ignore` rules, the CI service image and the local Biome hook, which are bumped by hand. Resolve a tag with `git ls-remote --tags` or `docker pull` before writing it.
- **typos reports, never rewrites**: its hook's default auto-fix turned `MOR` into `MORE` and a variable `pn` into `on`. A real domain word or identifier goes in `_typos.toml`.
- **Biome runs from inside `web/`** so `web/biome.json` is its root config; the hook's Biome version matches `web/package.json`.
- **Jev has no embeddings endpoint.** `vector` stays unused until an embedding model is chosen; any embeddings get the same record-and-replay treatment as model calls [Q53].
- **The approved-feature guard hook** blocks any Bash command whose text contains the approved status tag, including harmless filters. Filter with `not @draft` and edit prose with the Edit tool.

## Conventions

- **Requirements come first.** Features are written as `@draft` and approved by the reviewer; approved feature files are read-only for the agent, and changes are proposed as `<name>.revision.feature`. Code is built to make approved scenarios pass, and Postgres tables arrive with their feature. Workflow and tags: `features/README.md`.
- **Decisions are recorded.** `docs/GRILL-LOG.md` is binding: a changed decision gets a new entry numbered after the highest in use, never a silent edit. Definitions go in `docs/GLOSSARY.md` (created with its first entry), data mess in `docs/MESS-REGISTER.md`, and agent mistakes caught by a check in `docs/ai-catch-log.md`.
- **`AIDLC.html` explains the lifecycle to outside readers.** It is a static page at the root showing how AI-written code reaches main: its stages, gates and skills. When a skill in `.claude/skills/` is added, removed or changes what it does, update the page in the same change. Do the same when a development skill is adopted or dropped, or when a lifecycle gate changes: a hook, a CI job, or the approval or merge flow.
- **Code is organized by function**: `cmbs` submodules by job (`edgar`, `absee`, `analysis`, `db`), developer helpers in `tools/`.
- **A feature is a package `cmbs.<feature>`** with `service.py` (the logic), and optionally `tables.py`, `routes.py` (FastAPI `router`) and `commands.py` (Typer `app` plus `NAME`). All four are discovered by convention (`cmbs.db.registry`), so never add them to a central list [Q60]. In a route, declare the `User` dependency before `DB`.
- **Shared services, use them rather than re-implementing** [Q63]: `cmbs.clock` (never call `datetime.now()`), `cmbs.domain` (`Period`, `money()`; money is `Decimal`, never float), `cmbs.access.service.authorize()` (checks the role, audits refusals, returns the `Actor`), `cmbs.audit.service.record()`, `cmbs.settings.service.in_force()`, `cmbs.runs.service` (a scenario that names "run 42" sets it up with `cmbs.testing.world.set_next_run_number`; production code never chooses run numbers), `cmbs.deals.service`, `cmbs.checks.service` (flags that keep both sides), `cmbs.filings.service` (filings and stored files with SHA-256). A domain refusal raises a subclass of `cmbs.errors.Refused` with the user-facing message; programming errors stay ordinary exceptions.
- **Scenarios drive the service layer in process** [Q61, Q62]. Steps live in `features/steps/<area>.py` and call services through `cmbs.testing.bdd` (`in_session`, `attempt`). Each scenario gets its own database seeded with the fixture world (`cmbs.testing.world`). Steps shared by several features live in `features/steps/common.py`; search it before writing a step, because behave refuses duplicate step text. A step that names a deal sets `context.deal`. Run drafts with `uv run behave -t @draft <feature files>`.
- **Database tests** use the `db` and `seeded` fixtures in `tests/conftest.py` (a fresh clone per test) and the `needs_db` marker, so they skip without `DATABASE_URL`.
- **Migrations**: one per feature branch, autogenerated against a scratch database and reviewed by hand. Parallel branches use reserved revision ids and are re-chained onto the current head when merged [Q64].
- **Quality floors only rise**: coverage (`fail_under` in `pyproject.toml`, thresholds in `web/vite.config.ts`), the mutmut floor in the nightly mutation workflow and Stryker's `break` threshold move up as tests improve.
- **Model answers are replayed in tests** from recorded calls keyed on model and prompt; decisions are truth tables in code over Jev's judgments.
- **The UI formats values from the API** and leaves every computation to the backend. It calls the API only through the generated client (`web/src/generated/`, never edited by hand) and uses its types; a route change means exporting the schema and regenerating the client in the same commit, or the drift checks fail.
- **Committed files name no firm or real person.** That material lives in the gitignored `private/`, and a pre-commit hook blocks the terms listed there.
