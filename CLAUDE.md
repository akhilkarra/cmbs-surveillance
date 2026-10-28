# CLAUDE.md

Public CMBS filings (ABS-EE loan tapes, 10-D servicer text) turned into a verified, point-in-time surveillance panel. Layout and basic usage: `README.md`. Why each choice below was made: `docs/GRILL-LOG.md` (decision numbers in brackets).

## Stack

Exact versions live in `pyproject.toml`, `uv.lock` and `web/package.json`; this table says what exists and where.

| Layer | Choice | State |
|---|---|---|
| Core | Python 3.13, uv, one package `cmbs` in `src/cmbs`, Typer CLI `cmbs` [Q12] | Built |
| Store | PostgreSQL 18 with `vector` and `pg_trgm`; layers are schemas `raw`/`bronze`/`silver`/`gold`; SQLAlchemy 2.1 models in `cmbs.db.models`, Alembic migrations in `src/cmbs/db/migrations`, psycopg 3 [Q20, Q53, Q54] | Built, no tables yet |
| Frames and contracts | Polars 2.0, Pandera checks before every load [Q14, Q20] | Planned |
| API | FastAPI with a committed OpenAPI schema; orval generates the React client, CI fails on drift [Q12] | Planned |
| Web | React 19, Vite, strict TypeScript, Vitest in `web/` [Q13, Q52] | Scaffold built |
| Web, to come | AG Grid Community, TradingView lightweight-charts, Biome [Q13] | Planned |
| Pipeline model | Jev, TypeSafe's decision model, cloud API pinned to `jev-1.14.0`, behind a provider-neutral interface; Cloudflare Clef as the local fallback [Q7] | Planned |
| Requirements | Gherkin in `features/`, run with behave and behavex [Q5, Q29] | Built |
| UI tests | Playwright for Python inside behave steps; hooks in `features/environment.py` [Q55] | Built |
| Proofs | Lean 4 core (no Mathlib) on the host via elan, wired to Hypothesis differential tests [Q4] | Planned |
| Mutation testing | mutmut on `src/cmbs`, Stryker on `web/src` [Q51] | Built |
| Lint and types | pre-commit runs ruff, basedpyright (pyright, standard mode), Biome, typos, actionlint, zizmor, gitleaks and file hygiene on commit; pytest and Vitest with coverage floors on push [Q56] | Built |
| Containers | Docker Compose on OrbStack: `db`, `web` built; `api` planned [Q19] | Partly built |
| Claude skills | `/cmbs-refresh`, read-only `/cmbs-ask`, both calling only the `cmbs` CLI [Q12] | Planned |

## Running things

```bash
uvx pre-commit install                                           # once: commit and push hooks
uvx pre-commit run --all-files                                   # every commit-stage check
docker compose up -d --wait db                                   # Postgres on localhost:5432
export DATABASE_URL=postgresql+psycopg://cmbs:cmbs@localhost:5432/cmbs
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
```

CI (`.github/workflows/ci.yml`) runs all of the above except the dev server.

## Pins and gotchas

- **Python 3.13** (`.python-version`): `minify-html`, pulled in by behavex, has no 3.15 wheel.
- **pytest below 9.2**: mutmut 3.8.0 reads `item._nodeid`, which pytest 9.2.0 removed.
- **TypeScript 6, not 7**: TypeScript 7 drops the JavaScript compiler API that Stryker calls.
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
- **Code is organized by function**: `cmbs` submodules by job (`edgar`, `absee`, `analysis`, `db`), developer helpers in `tools/`.
- **Quality floors only rise**: coverage (`fail_under` in `pyproject.toml`, thresholds in `web/vite.config.ts`), the mutmut floor in CI and Stryker's `break` threshold move up as tests improve.
- **Model answers are replayed in tests** from recorded calls keyed on model and prompt; decisions are truth tables in code over Jev's judgments.
- **The UI formats values from the API** and leaves every computation to the backend.
- **Committed files name no firm or real person.** That material lives in the gitignored `private/`, and a pre-commit hook blocks the terms listed there.
