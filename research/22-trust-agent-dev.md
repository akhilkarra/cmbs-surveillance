# 22 · Trust controls for agent-assisted development

*Written 2026-10-24. Scope: controls that stop Claude Code, and the human working with it, from shipping subtle mistakes in this repo (EDGAR → DuckDB pipeline, behave/behavex BDD, Lean 4 invariants, React UI).*

**Source keys.** **[H]** [hooks](https://code.claude.com/docs/en/hooks) · **[P]** [permissions](https://code.claude.com/docs/en/permissions) · **[PM]** [permission modes](https://code.claude.com/docs/en/permission-modes) · **[M]** [memory / CLAUDE.md](https://code.claude.com/docs/en/memory) · **[SA]** [subagents](https://code.claude.com/docs/en/sub-agents) · **[SK]** [skills](https://code.claude.com/docs/en/skills) · **[BP]** [best practices](https://code.claude.com/docs/en/best-practices). All were fetched as raw `.md` on 2026-10-24.

**Inference** marks a claim I reasoned to rather than read in a source. Tool versions come from PyPI, npm and GitHub on the same day.

> **A note on method (it echoes catch-log entry 1).** `WebFetch` on the hooks page reported "covers only characters 0 to 100000" of a 281,770-character page. It also said `stop_hook_active` was "not documented". That was wrong: the field appears at character ~200k. I downloaded the full pages with `curl` and grepped them, the same fix the catch log records.
>
> **A plan inconsistency to settle.** PLAN.md names **pytest-bdd** as the BDD runner, while this brief says **behave/behavex**. This doc assumes behavex. Pick one before day 1.

---

## 1. Claude Code mechanisms: enforced vs advisory

| Mechanism | Enforced? | What the docs say |
|---|---|---|
| **PreToolUse hook** | **Enforced**, for the tools it matches | "Exit 2 blocks the tool call… Exit 2 blocks even if the JSON on stdout says `allow`." Exit 1 is *non-blocking* [H]. |
| **PostToolUse hook** | Feedback only; can't undo | "The tool has already run, so exit 2 can't undo it"; stderr or `{"decision":"block","reason":…}` goes back to Claude [H]. |
| **Stop / SubagentStop / TaskCompleted hooks** | **Enforced**, with a cap | `decision:"block"` or exit 2 keeps Claude working. There is an "8-consecutive-continuation cap", and a `stop_hook_active` input [H]. |
| **Permission rules** (`allow`/`ask`/`deny`) | **Enforced** in the harness | "Evaluated in order: deny, then ask, then allow"; "Deny rules block in every mode, including `bypassPermissions`" [P][PM]. A hook returning `allow` can't override a deny or ask rule [P]. |
| **Plan mode** | **Enforced** read-only until approval | "Edits stay blocked until you approve the plan" [PM]. Exception: interactive sessions where bypass is available. |
| **CLAUDE.md** | **Advisory** | "Claude treats them as context, not enforced configuration. To block an action regardless of what Claude decides, use a PreToolUse hook" [M]. Keep it under 200 lines [M]. |
| **Skills** | Advisory routine | They encode the steps but don't force them to run [SK]. |
| **Subagents** | Structural isolation | Each has "its own context window… specific tool access, and independent permissions" [SA]. |

**Limits of what is enforced. Design around them.**
- Edit deny rules cover the built-in file tools, recognised shell commands (`sed`, `tee`, redirects) and Bash redirections. They do **not** cover "a Python or Node script that opens files itself" [P].
- Bash deny rules don't match a program "by path or inside `sh -c`" [P].
- For OS-level blocking, the docs point to the sandbox [P].
- *Inference:* a CI-side check is the only guarantee the agent can't route around, so every protected-file rule below has a CI backstop.
- Writes to `.claude/` (where hooks and settings live) are a "protected path" and always prompt in `default`/`acceptEdits` [PM]. So the agent can't silently disable its own hooks.

### 1a. `.claude/settings.json` (committed)

```json
{
  "permissions": {
    "defaultMode": "acceptEdits",
    "deny": [
      "Edit(/data/golden/**)",
      "Bash(git push *)", "Bash(git commit --no-verify *)", "Bash(git reset --hard *)",
      "Read(./.env)"
    ],
    "ask": [
      "Edit(/lean/Cmbs/Spec/**)",
      "Edit(/features/accepted/**)",
      "Edit(/tests/golden/**)",
      "Edit(/scripts/protected.sha256)"
    ],
    "allow": ["Bash(uv run pytest *)", "Bash(uv run ruff *)", "Bash(uv run behavex *)", "Bash(lake build *)"]
  },
  "hooks": {
    "PreToolUse": [
      { "matcher": "Edit|Write|NotebookEdit",
        "hooks": [{ "type": "command", "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/guard_protected.py" }] }
    ],
    "PostToolUse": [
      { "matcher": "Edit|Write",
        "hooks": [{ "type": "command", "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/lint_typecheck.sh", "timeout": 120 }] }
    ],
    "Stop": [
      { "hooks": [{ "type": "command", "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/require_green.sh", "timeout": 600 }] }
    ]
  }
}
```

**Rule syntax.**
- `/path` is anchored at the settings source, and `Edit` rules are the ones that match writes.
- `Write(path)` rules "never match", so use `Edit(...)` [P].
- `${CLAUDE_PROJECT_DIR}` and `timeout` (seconds) are documented [H].

**Approval design.** An `ask` rule means "unless explicitly approved": the human must click yes in the prompt for each edit. A matching ask rule prompts even when an allow rule matches too [P]. Golden data is `deny` outright; you regenerate it yourself, outside the agent.

### 1b. Hook: block protected edits and explain why (`guard_protected.py`)

```python
#!/usr/bin/env python3
import json, sys, re
d = json.load(sys.stdin)
p = d["tool_input"].get("file_path", "").replace("\\", "/")   # always absolute [H]
RULES = {
  r"/lean/Cmbs/Spec/": "Lean theorem STATEMENTS are spec. Change proofs in /lean/Cmbs/Proofs/.",
  r"/features/accepted/": "Accepted .feature files are the requirements. Propose changes in features/proposed/.",
  r"/(tests|data)/golden/": "Golden data is regenerated by the human via `make regolden`.",
}
for pat, msg in RULES.items():
    if re.search(pat, p):
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
              "permissionDecision": "ask", "permissionDecisionReason": msg}}))
        sys.exit(0)
sys.exit(0)
```

`permissionDecision` can be `allow|deny|ask|defer`, and the reason is shown to Claude [H]. With `ask`, the human sees the reason at approval time.

### 1c. Hook: ruff + type check after each edit (`lint_typecheck.sh`)

```bash
#!/usr/bin/env bash
f=$(jq -r '.tool_input.file_path')
case "$f" in
  *.py)  out=$(uv run ruff check --fix "$f" && uv run ruff format "$f" && uv run mypy "$f" 2>&1) ;;
  *.ts|*.tsx) out=$(cd "$CLAUDE_PROJECT_DIR/web" && npx tsc --noEmit 2>&1) ;;
  *.lean) out=$(cd "$CLAUDE_PROJECT_DIR/lean" && lake build --wfail 2>&1) ;;
  *) exit 0 ;;
esac
[ $? -ne 0 ] && { echo "$out" | tail -40 >&2; exit 2; }   # stderr → Claude [H]
exit 0
```

**What I verified** (`lake --help` and a local Lean v4.35.0 test, 2026-10-24):
- `lake build --wfail` "fail[s] build if warnings are logged".
- `sorry` is only a *warning*: `lean` exited 0 on a file with `sorry`. So `--wfail` is what turns `sorry` into a failure.

### 1d. Hook: refuse to stop while tests fail (`require_green.sh`)

```bash
#!/usr/bin/env bash
cd "$CLAUDE_PROJECT_DIR"
if ! out=$(uv run pytest -x -q 2>&1 && uv run behavex -t="not @wip" --no-report 2>&1); then
  jq -n --arg r "Tests failing; not done. Fix the code, not the tests. Last output:
$(echo "$out" | tail -30)" '{decision:"block", reason:$r}'
fi
exit 0
```

**How stopping is bounded.**
- `stop_hook_active` is true while Claude is already continuing because of a stop hook [H].
- The 8-continuation cap still ends the turn [H]. *Inference:* when that happens, the human sees a red state, not a "done" claim.
- The cap is configurable through `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP` [H].

### 1e. CLAUDE.md, plan mode, subagents, skills

**CLAUDE.md (advisory).** Keep it to facts and short rules [M]:
- the commands
- "never edit `Spec/` or `features/accepted/`; propose instead"
- "a summarizer's 'not found' is not evidence"
- "show the command and its output, not 'tests pass'"
- "any 'is public' claim needs an EDGAR accession number"

The last three are lifted from the catch log. Path-scoped rules in `.claude/rules/` load only for matching files [M].

**Plan mode.** Use it for every change to more than one file. Use `Ctrl+G` to edit the plan before approving it [PM]. The plan has to name the requirement IDs it touches (§3).

**Review subagent** (`.claude/agents/spec-reviewer.md`).
- Frontmatter: `tools: Read, Grep, Glob, Bash`, `disallowedTools: Edit, Write`.
- `isolation: worktree` is optional [SA]; `omitClaudeMd` is a documented field [SA].
- The prompt passes only the `.feature` file, the Lean statement and `git diff`, and asks the reviewer to *refute* the change.
- The docs' rationale: "a fresh model try to refute the result, so the agent doing the work isn't the one grading it" [BP], and "A fresh context improves code review since Claude won't be biased toward code it just wrote" [BP].

**Skills.**
- `.claude/skills/cmbs-pipeline/SKILL.md` covers collect → parse → validate → curate.
- `.claude/skills/close-task/SKILL.md` lists the evidence a "done" message must carry.
- Set `disable-model-invocation: true` on skills that should only run when the human types them [SK].

---

## 2. Engineering controls

Versions checked on 2026-10-24.

| Control | Tool and version | Why |
|---|---|---|
| Lint + format | ruff 0.17.2 | Fast, and catches dead code and unused imports that agents leave behind. |
| Python types | mypy 2.4.0 (`--strict`), or pyright 1.1.414 | Catches hallucinated attributes and signatures before runtime. *Inference:* the cheapest guard against hallucinated APIs. |
| Strict TS | TypeScript 7.0.2 (npm); TS 6.0 made `strict` the default ([secondary report](https://visualstudiomagazine.com/articles/2026/03/23/typescript-6-0-ships-as-final-javascript-based-release-clears-path-for-go-native-7-0.aspx)) | Still set `"strict": true, "noUncheckedIndexedAccess": true` explicitly so the setting can't drift. |
| Property tests | Hypothesis 6.170.9 | Checks invariants over generated inputs: balances conserved, every loan in exactly one bucket. Also drives the differential tests in §4. |
| Mutation testing | mutmut 3.8.0; cosmic-ray 8.7.0 | Tests the tests. A surviving mutant in `aggregate.py` means some assertion is too weak. mutmut is simpler; cosmic-ray scales out across workers. |
| Snapshot/golden | syrupy 6.1.1, or inline-snapshot 0.36.1 | Freezes curated outputs for fixture filings. Diffs are reviewed by a human, and the snapshots are protected (§1). |
| Data contracts | pandera 0.34.1; pydantic 2.14.1 | Pandera checks every DataFrame before DuckDB load. Pydantic types the LLM-extraction outputs and the FastAPI models. |
| API fuzzing | schemathesis 4.32.4 | Generates requests from the FastAPI OpenAPI schema, so it finds 500s and schema-violating responses the UI would render. |
| Hooks at commit | pre-commit 4.7.0 | Runs the same ruff/mypy/hash checks for the human too. Agent `--no-verify` is denied in §1a. |
| CI gates | GitHub Actions | The only layer the agent can't bypass. *Inference:* it re-runs everything, plus the hash and traceability checks. |
| Reproducible env | uv 0.13.5 (`uv.lock`, `uv sync --locked`) | The same resolution everywhere. `--locked` fails if the lock is stale. |
| HTTP record/replay | vcrpy 8.3.0; pytest-recording 0.14.0 | Records EDGAR responses as cassettes, so tests are deterministic and offline. Run CI with `--record-mode=none` so a missing cassette fails. |
| Deterministic fixtures | Committed raw ABS-EE XML and 10-D samples, plus Hypothesis `derandomize=True` in CI | The same bytes in gives the same table out, and a failure reproduces. |
| BDD runner | behavex 4.6.8 (on behave 1.3.3) | Supports tag expressions (`-t="@REQ-AGG-003"`), runs in parallel, and produces an HTML report that a reviewer can read. |
| Lean gate | Lean 4 v4.35.0 / Lake | `lake build --wfail` makes `sorry` fail, and an axioms check (below) confirms each theorem's dependencies. |

**Lean axioms check** (`lean/CheckAxioms.lean`, run in CI). Run `#print axioms Cmbs.balance_conserved` for each theorem, then grep the output. Allow only `propext`, `Classical.choice` and `Quot.sound`.

- I confirmed locally that a `sorry` proof prints `depends on axioms: [sorryAx]`.
- *Inference, from Lean community practice:* also reject `Lean.ofReduceBool`, which `native_decide` introduces. It trusts the compiler.

---

## 3. Traceability: requirement ID scheme

**The ID.** Use `REQ-<AREA>-<NNN>`, with areas `SRC` (sourcing), `PARSE`, `CLS` (classification), `AGG` (aggregation), `EXT` (LLM extraction) and `UI`. Catch-log rows get `C-001`…`C-006`, in the order already in the log.

**Where the ID appears.**

| Artifact | How the ID appears |
|---|---|
| `.feature` | `@REQ-AGG-003 @lean:Cmbs.balance_conserved @catch:C-004` on the scenario |
| Python code | `# REQ-AGG-003` on the function, or `__requirements__ = [...]` in the module |
| pytest | `@pytest.mark.req("REQ-AGG-003")` |
| Lean | `/-- REQ-AGG-003: sum of closing balances = opening − principal − liquidations -/` docstring on the theorem in `Spec/` |
| Catch log | A new "Req" column, e.g. C-001 → REQ-SRC-001 |

**How it is run and checked.**
- `behavex -t=@REQ-AGG-003` runs exactly that requirement. `-t="@lean:*"` runs every scenario backed by a theorem. Tag expressions v2 support wildcards (behavex README).
- `scripts/trace.py` builds the matrix by grepping all five places. It fails CI if:
  - an accepted scenario has no test or code reference
  - a `Spec/` theorem has no REQ
  - a catch-log row has no REQ

  *Inference:* that last check is the useful one. Every past mistake must become a regression requirement.

**Example mapping from today's log:**
- C-001 (truncated summary) → REQ-SRC-001, "a source read asserts `chars_read == content_length`".
- C-002/3/4 (classifier) → REQ-CLS-001…003. "Classified per period across all 26 N-PORTs; no consumer-ABS keywords in the CMBS bucket" is a `Scenario Outline` with one example row per period.
- C-005 (public claim) → REQ-SRC-002, "`is_public=True` requires a cited 10-D or ABS-EE accession number".

**The reviewer's view** (*inference*). For any number on the UI, a tooltip shows its REQ IDs. `trace.py --req REQ-AGG-003` lists the scenario, functions, tests, theorem, mutation score and the catch entries behind it.

---

## 4. Lean proves the spec, not the Python

### Pattern: theorems and the oracle share the same definitions

1. **Definitions.** In `lean/Cmbs/Defs.lean`, define `poolBalance`, `bucketOf` and `wac` over `Int` cents and `Rat`, not `Float`. *Inference:* proofs about `Float` are impractical, and Python can match exactly using `int` and `fractions.Fraction`/`Decimal`.
2. **Theorems.** `lean/Cmbs/Spec/*.lean` states the theorems *about those same definitions*. `Proofs/` proves them.
3. **The oracle.** `lean_exe oracle` (Lake supports `[[lean_exe]]` targets; Lake README) imports `Defs` and reads JSON lines on stdin. It writes `poolBalance`/`bucketOf`/`wac` results to stdout.
   - The oracle executes the **exact definitions the theorems are about**. There is no second hand-written Lean implementation.
   - *Inference:* CI should grep for `@[implemented_by]` and `@[extern]` in `Defs.lean` and fail if either appears. Both let compiled code differ from the logical definition.
4. **The differential test** (Hypothesis):

```python
@settings(max_examples=2000, derandomize=True)
@given(st.lists(loan_strategy(), max_size=200))
def test_python_matches_lean(loans):           # REQ-AGG-003
    assert py.pool_aggregate(loans) == oracle.run(loans)   # persistent subprocess, batched
```

   Add `@example`s for edge cases: an empty pool, a single liquidated loan, zero balances and the boundaries between delinquency buckets. *Inference:* run the same differential check on the real DuckDB output for fixture filings, which also catches SQL that diverges from the Python.

### What remains unverified (honestly)

- **The Python itself.** Differential testing only samples the input space. A bug outside the generator's distribution survives. Mutation testing measures how sharp the tests are, not how complete they are.
- **Whether the spec is adequate.** A theorem can be true and still state the wrong property, e.g. buckets that are disjoint but don't cover every loan. Only human review of `Spec/` addresses this, which is why it is protected.
- **The serialization boundary.** JSON encoding, rounding and decimal ↔ cents conversion sit outside both proof and oracle.
- **Trusted base.** That covers the Lean kernel and the compiler/runtime that runs `oracle`. `#print axioms` says nothing about compiler correctness (*inference*).
- **Everything upstream of the numbers.** That includes EDGAR parsing, LLM extraction and the "is public" classification. Pandera contracts, extraction evals and reconciliation to remittance totals are tests there, not proofs.
- **Real-world fit.** Reconciliation breaks in actual remittance reports may be legitimate trustee adjustments that the spec doesn't model.

---

## 5. Agent failure modes → one concrete control each

| Failure mode | Control | Enforced by |
|---|---|---|
| **Tests rewritten to pass** | Accepted `.feature` files and golden tests sit behind `ask`/`deny` plus the PreToolUse hook. CI checks `scripts/protected.sha256` against the protected files and requires an `Approved-by: <human>` commit trailer when a hash changes. | Harness + CI |
| **Silently weakened assertions** | Mutation score floor on `aggregate.py`/`classify.py` (e.g. ≥ 85% killed with mutmut) in a nightly or pre-merge job. Any diff to `tests/` triggers the review subagent with the instruction "list every assertion whose strength decreased". | CI + subagent |
| **Hallucinated APIs** | mypy `--strict` / `tsc --strict` in the PostToolUse hook. Plus record/replay: an invented EDGAR endpoint fails because no cassette exists. | Hook + tests |
| **Over-broad edits** | Plan mode with named files and REQ IDs. A PreToolUse hook (*inference*) can ask when a path outside the plan's file list is edited. Review the `git diff --stat` before commit. | Plan mode + hook |
| **Unverifiable "done" claims** | The Stop hook blocks while tests fail. CLAUDE.md and the `close-task` skill require showing the command and output [BP: "show evidence rather than asserting success"]. For bug fixes, require **red-then-green evidence**: the failing test's output *before* the fix, then the passing output. | Hook + routine |
| **Stale-context mistakes** (outdated names, a superseded plan, "public" from memory) | Re-verify named entities against a primary source (catch-log C-005/C-006). Wire a `SessionStart`/`PostCompact` hook to re-inject `git status` and open REQs ([H] lists both events). Verify any "is public / not found" claim through a second agent or a full-text check. | Hook + routine |

---

## 6. Recommended minimal set for the 4-day build

**Day 1, about 2–3 hours of setup:**
1. `uv init` with `uv.lock`. Pin ruff, mypy, pytest, hypothesis, pandera, behavex and pytest-recording.
2. `.claude/settings.json` from §1a, with the three hooks (guard, lint/typecheck, require-green).
3. A CLAUDE.md under 60 lines: commands, the protected paths, and the evidence rule.
4. Directory split: `features/accepted/` vs `features/proposed/`, and `lean/Cmbs/{Defs,Spec,Proofs}`. Create `protected.sha256`.
5. `lake build --wfail` plus the axioms-check script.
6. EDGAR cassettes for 2–3 deals, recorded once.
7. A `spec-reviewer` subagent with read-only tools.
8. One GitHub Actions workflow: `uv sync --locked`, ruff, mypy, pytest, `behavex`, Lean build and axioms check, protected-hash check, and `trace.py`.
9. Add `ID` and `Req` columns to `docs/ai-catch-log.md`.

**Day 2–3 additions:**
- The Hypothesis differential test against `lean_exe oracle` for one or two definitions (balance conservation, bucket partition).
- Pandera schemas on each DuckDB load.
- One mutmut run on `aggregate.py`, with the score recorded in the README.

**Nice-to-haves to skip this week:**
- cosmic-ray (mutmut is enough)
- schemathesis (unless the FastAPI surface grows past a few endpoints)
- Stryker for the React code
- the sandbox / OS-level write isolation (the CI hash check covers the bypass risk)
- auto mode and its classifier tuning
- `isolation: worktree` for subagents
- a full traceability UI (the CLI `trace.py` is enough)
- syrupy snapshots beyond 2–3 curated outputs
- pyright alongside mypy (pick one)
- `ty` (0.0.91 is still pre-1.0)
- HTTP or agent-type hooks
