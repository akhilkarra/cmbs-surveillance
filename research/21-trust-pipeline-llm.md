# 21 — Minimizing "just trust the AI" in the CMBS text pipeline

Written 2026-10-24. Tags: **[V]** = checked today against the linked primary source. **[S]** = secondary source only (blog or press), not confirmed by TypeSafe. **[I]** = my inference or background knowledge, not re-checked today.

Scope: extracting typed fields from 10-D "Special Servicing Comments" (see `11-data-sources.md` §2), and possibly Freddie K-deal watchlist comments. The fields are transfer reason, property-performance issue, borrower request, workout strategy and severity. They are joined to the ABS-EE tape in DuckDB.

## Summary

1. **Jev is a good fit for this extraction shape.** It takes `state` plus named `choice` / `score` / `noul` questions and returns a full probability distribution over a fixed answer set. Choice allows up to 255 options and Score up to 10 levels. Schema adherence is structural, not prompt-based. [V]
2. **Jev's `confidence` is a deterministic formula over the probabilities.** TypeSafe's own docs do not claim it is calibrated. Repeated calls are **not** bit-identical: their own cookbook measured a 0.0098 mean probability standard deviation across re-runs. It has no seed or temperature parameter. [V] So we have to calibrate, cache and pin it ourselves.
3. **Access is self-serve, but its status is murky.** The docs describe self-serve key creation, $0.042 per million input tokens, free output, and a 64k-token default context. Secondary sources conflict on whether signups paused after 22 Sep. **There is no local, on-prem or open-weight option documented.** ZDR is enterprise-only, and the DPA gives no retention period. [V/S]
4. **Put every provider behind one `Decide(state, questions) → {answer, probs, evidence}` interface.** Backends: Jev; Claude strict structured outputs (enum, no logprobs); OpenAI-style logprob scoring; local vLLM/llama.cpp with XGrammar `choice` constraints (the stretch goal); a SetFit-style classifier distilled from the golden set.
5. **Most of the remaining silent-error risk is not in the model.** It sits in **grounding** (does the quote exist and support the label?), **point-in-time joins** (filing date, not period), and **unmonitored version or prompt changes**. All three can be closed deterministically.
6. **The three named layers leave gaps.** Decision models, behavex and Lean leave five open: calibration and abstention, grounding, golden-set eval, replay and versioning, and tape cross-checks. The minimal additions are an evidence-span check, a conformal/threshold review queue, a 300-item golden set, a record/replay cache keyed on the pinned model ID, and a DuckDB cross-check view.

---

## 1. Jev, from primary sources

### 1.1 API shape [V]
- **Endpoint:** a single endpoint, `POST https://api.typesafe.ai/v1/systemone`, with a Bearer key ([quickstart](https://docs.typesafe.ai/introduction/quickstart.md)). SDKs are `typesafe-sdk` (Python ≥3.10) and `@typesafe-ai/sdk`.
- **Request:** `model`, `state` (a string, object or array), `questions` (named, at least one), and optional image `assets` ([API ref](https://docs.typesafe.ai/api/system-one.md)). There are no seed, temperature or sampling fields.
- **Question types:**
  - `choice`: 1–255 named options, each with a description. Returns `choice`, `confidence`, and `probabilities` summing to 1.
  - `score`: an ordered list of 1–10 levels. Returns `score` (the "probability-weighted expected value"), `confidence`, `legend` and `probabilities`.
  - `noul`: a yes/no probability `noul` in [0, 1]. It returns no confidence.
  - `box`: image localisation, irrelevant here.
- **No numeric extraction primitive.** Numbers and dates are handled by pre-parsing candidates in code and asking Jev to choose among them ([date](https://docs.typesafe.ai/cookbooks/date_extraction_cookbook.md) and [pre-parsed value](https://docs.typesafe.ai/cookbooks/pre_parsed_value_extraction_cookbook.md) cookbooks).
- **No multi-select.** Choice selects "one option from a defined set" ([choice](https://docs.typesafe.ai/primitives/choice.md)). Multi-label fields such as "property issues" therefore need one `noul` per label. [V/I]
- **Limits** ([models](https://docs.typesafe.ai/models.md), [errors](https://docs.typesafe.ai/api/errors.md)):
  - 64k tokens per request and 32k per question by default; 262k with the `TypeSafe-Danger-Zone-Long-Context` header, which is "slower and less accurate".
  - 32 MiB body.
  - Rate limits per organization; 429 and 529 must be retried.
- **Versions:** `jev-1.14.0` (released 2026-10-14) and `jev-1.13.0`. `jev-latest` is an alias that can change answers. The response `model` field reports the version that actually answered. No deprecation policy is published.

### 1.2 Confidence and calibration [V]
- For Choice, `confidence = (p_max − 1/n)/(1 − 1/n)`. Score uses a dispersion-based variant. For Noul, `|2p−1|` is suggested ([confidence](https://docs.typesafe.ai/confidence.md)).
- The docs call confidence "one reasonable way to summarize a distribution". They say thresholds "depend on your domain and the performance of the model for your use case."
- Marketing attributes calibration to RLCD and says "higher confidence means higher accuracy" ([launch blog](https://typesafe.ai/blog/introducing-system-one-models-and-jev)). **No ECE or Brier score is published.** Treat calibration as unverified until measured on our golden set.
- **Determinism:** the self-consistency cookbook reports a mean probability standard deviation of `0.0098` across 15 re-runs, versus `0.0012` for Haiku at temperature 0 ([cookbook](https://docs.typesafe.ai/cookbooks/consistency_choice_cookbook.md)). The output is near-deterministic but **not byte-stable**.

### 1.3 Pricing and access
- **Price:** $42 per billion input tokens; output tokens are free ([models](https://docs.typesafe.ai/models.md)). [V] A 10-D comment is about 200–800 tokens [I], so the whole public universe costs cents.
- **Access:**
  - **Docs:** keys are created at `console.typesafe.ai/keys`, and the docs mention no waitlist. [V]
  - **Homepage:** still says "early access", and the blog says the team is "bringing developers off the waitlist as quickly as we can". [V]
  - **Secondary accounts:**
    - Launched 2026-09-15 with a waitlist.
    - Opened about 2026-09-20 with $5 in credits ([Crypto Briefing](https://cryptobriefing.com/typesafe-jev-ai-public-access/)).
    - New signups paused 2026-09-22 ([flaviocopes, updated 9 Oct](https://flaviocopes.com/jev-api-key/)). Another blog says signups are direct ([AI Profit Boardroom](https://aiprofitboardroom.com/blog/jev-waitlist/)).
    - All three are [S], and they conflict. The console returned 403 to my fetcher.
  - **Alternative route:** Vercel AI Gateway serves Jev as `typesafe-ai/jev` ([Vercel changelog](https://vercel.com/changelog/ai-gateway-now-supports-typesafe-clients-and-an-http-api-for-jev), [KB](https://vercel.com/kb/jev-from-typesafe-ai)). [V for the existence of the route; S that it is in the free tier]
  - **Action:** try to sign in today. If blocked, use Vercel. If both fail, use the fallbacks in §1.6.

### 1.4 Data retention [V]
- **Training:** customer data is not used for training ([enterprise](https://typesafe.ai/enterprise): "We don't use your prompts or other inputs to train").
- **ZDR:** "available for enterprise customers" only.
- **Retention period:** the [DPA](https://typesafe.ai/legal/data-processing) gives none, only "as long as necessary". Subprocessors are listed at trust.typesafe.ai.
- **SOC 2 Type II:** report available on request.
- **Implication:** public EDGAR text is fine to send. **Do not send investor-portal or CREFC IRP text without ZDR**, which is an inference from the vendor-access caveats in `11-data-sources.md` §8. [I]

### 1.5 Local, on-prem or open weights
- **None documented** [V]:
  - The models page lists only the hosted endpoint, with "the same weights" serving every account.
  - The enterprise FAQ asks about VPC and on-prem deployment but shows no answer.
- **So the "move it local" stretch goal cannot be met with Jev itself.** The local stand-in is §1.6(c). [I]

### 1.6 Known limitations
These are TypeSafe's own, from [jev-1.13 jaggedness](https://docs.typesafe.ai/model-jaggedness/jev-1.13.md) [V]:
- It answers literally: "the question you wrote, not the one you meant".
- It is weak at counting and arithmetic, and "reads dates as text".
- It struggles with double negatives and multi-hop questions.
- "Accuracy falls as the state grows" with irrelevant content.
- It is not robust to adversarial content.
- It has a first-option bias: it "leans toward the option that comes first".

What this means for us [I]:
- Send **one comment per call**, not the whole 10-D.
- Do the date logic ("transferred 06/04/2026") in code.
- Randomize or rotate option order in eval runs to measure position bias.

### 1.7 Fallbacks behind one interface

```python
class Decider(Protocol):
    def decide(self, state: str, qs: dict[str, Question]) -> DecisionResult: ...
# DecisionResult per field:
#   answer, probs: dict[label, float] | None, evidence_spans: list[str],
#   model_id, request_hash
```

| Backend | Answer guarantee | Probabilities? | Local? | Notes |
|---|---|---|---|---|
| (a) **Jev** | Structural: only option names can come back | Yes, the full distribution | No | Primary option. |
| (b) **Claude strict structured outputs** (`output_config.format` with a JSON Schema `enum`, or `strict: true` tools) | Constrained decoding "guarantee[s] schema-compliant responses", except on refusal or `max_tokens`. Enum capitalization is not guaranteed ([docs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)) [V] | **No logprobs** in the Messages API params list ([API ref](https://platform.claude.com/docs/en/api/messages)) [V, partial page] | No | Gets a confidence proxy from k-sample voting or a verbalized score [I]. It is the best option for an `evidence_quote` string field alongside the enum. |
| (c) **Local constrained decoding**: vLLM `choice`/`json`/`grammar` on XGrammar or guidance ([vLLM](https://docs.vllm.ai/en/latest/features/structured_outputs.html)) [V]; [Outlines](https://github.com/dottxt-ai/outlines); [llama.cpp GBNF](https://github.com/ggml-org/llama.cpp/blob/master/grammars/README.md) [I] | Grammar-enforced | Yes, via the logprobs of each option's first token or the sum over the full sequence [I] | **Yes** | Covers the stretch goal. With [batch-invariant kernels](https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/), vLLM runs can be bitwise reproducible, at about 2x slower in their test. [V] |
| (d) **Logprob classification** via an OpenAI-style `logprobs`/`top_logprobs` API [I] | Prompted, not enforced, unless combined with structured output | Yes | Depends | Score each label as a continuation. |
| (e) **Small fine-tuned classifier**: [SetFit](https://github.com/huggingface/setfit) or a DeBERTa head trained on the golden set plus LLM silver labels [I] | Structural, through a softmax over labels | Yes, and calibratable | Yes | Fully deterministic on CPU. It is the cheapest end state once there are about 1–2k labels. |

---

## 2. Determinism-gap map

"Deterministic?" asks whether the *control* itself is deterministic. The model never is.

| # | Gap | Silent failure mode | Control | Deterministic? | Tool / library |
|---|---|---|---|---|---|
| 1 | **Schema adherence** | A label outside the enum, a misspelled enum, or a free-text "severity" | Enum-only outputs. Re-validate in code anyway, since Claude enum casing is not guaranteed. Add an explicit `unknown/not_stated` option or a gating `noul` (the Jev docs warn "none of the above" "may not work well") | Yes (the validation) | Jev types; Pydantic `Literal`; Pandera `isin` check on the output table |
| 2 | **Grounding** | The label is plausible but nothing in the comment supports it, or the label comes from a *different* loan's comment | Require an `evidence_span` for every non-null field. Then (i) check that it is an **exact substring** of the source after whitespace and Unicode normalization, storing char offsets; (ii) check that the span comes from the same `(deal, loan_id, filing_accession)` row; (iii) run an optional entailment check with a separate question, as in Jev's [citation-check cookbook](https://docs.typesafe.ai/cookbooks/citation_check.md) | (i) and (ii) yes; (iii) no | Python `str.find` after NFKC normalization; [rapidfuzz](https://github.com/rapidfuzz/RapidFuzz) only to *diagnose* misses, never to accept them [I] |
| 3 | **Calibration and abstention** | A confident but wrong label goes straight into the panel | Fit per-field thresholds on the golden set, not vendor defaults. Low-margin rows go to a **review queue table**. For a set-valued output, use split **conformal prediction**: a 90%-coverage label set, with set size greater than 1 meaning review ([Angelopoulos & Bates](https://arxiv.org/abs/2107.07511); [MAPIE](https://github.com/scikit-learn-contrib/MAPIE)) [I]. Report reliability diagrams and ECE per field | Yes, once fit (pure function of the probabilities) | MAPIE or a 20-line numpy implementation; DuckDB `review_queue` |
| 4 | **Evaluation** | "It looks right on 10 examples" | A **golden set** of about 300 comments stratified by deal, servicer and year, double-labeled on 20%. Report per-field precision, recall and F1 with Wilson or bootstrap 95% CIs. Report **Cohen's κ / Krippendorff's α** between annotators as the ceiling: if humans agree at κ=0.6, a 0.9 model claim is noise [I]. Freeze a held-out test split | Yes (the scoring) | scikit-learn metrics; `krippendorff`; statsmodels `proportion_confint` [I] |
| 5 | **Regression and versioning** | `jev-latest` moves, which changes answers ([models](https://docs.typesafe.ai/models.md)); a prompt edit silently shifts labels | Pin `jev-1.14.0` (or a dated Claude ID). Hash `(model_id, prompt/question JSON, option order, state)` into a `request_hash`. A **record/replay cache** stores raw responses keyed by that hash, so reruns are byte-identical *by construction*, since the model is not ([0.0098 std](https://docs.typesafe.ai/cookbooks/consistency_choice_cookbook.md)). On any model or prompt change, re-run the golden set and diff the scores in CI, failing on a CI-significant drop | Yes (the replay) | DuckDB or Parquet `llm_calls` table; [VCR.py](https://github.com/kevin1024/vcrpy) for tests [I] |
| 6 | **Drift monitoring** | New servicer phrasing (e.g. post-2026 hotel language) and accuracy decays unseen | Monthly: label distribution per field, mean confidence, abstention rate, review-queue overturn rate, and the share of unseen n-grams. Alert on PSI or χ² shift. Sample 20 accepted rows a month for spot-labeling, which grows the golden set | Yes (the stats) | DuckDB SQL; [Evidently](https://github.com/evidentlyai/evidently) optional [I] |
| 7 | **Cross-checks against the tape** | The text says "transferred to SS due to imminent default" but the tape has no transfer, or a different date | DuckDB view: text `transfer_event` ⇒ ABS-EE `mostRecentSpecialServicerTransferDate` within ±2 periods. Text `workout_strategy` ⇒ `workoutStrategyCode` (seen codes: 2, 7, 8, 11, 13, 98; `11-data-sources.md` §1.4). Text `modification` ⇒ `modificationCode`. Text "matured/balloon" ⇒ `paymentStatusLoanCode` = 5. **Disagreements go to an `xcheck_flags` table and are never overwritten.** Pre-Reg-AB-II deals have no tape, so their checks run against 10-D EX-99.1 tables instead | Yes | DuckDB SQL plus Pandera; pytest-bdd scenarios |
| 8 | **Ensembles and self-consistency** | A single-call error on ambiguous text | **Worth it only on the review band**: cross-provider agreement (Jev vs Claude) on rows near the threshold, sending disagreements to a human. Re-sampling the *same* model buys little, since the re-run std is already about 0.01 [V/I]. Do **not** use majority vote to overwrite: store both answers | Aggregation yes | Two `Decider`s and an agreement column |
| 9 | **Idempotency and point-in-time correctness** | Look-ahead: joining text by reporting period when it was only public at `filing_date` (about 12 days after distribution; `11-data-sources.md` §1.2). Amendments (10-D/A) silently replace history. Re-running the extraction duplicates rows | The key is `(accession, loan_id, comment_hash)`. Store `available_at = filing acceptance time`, with as-of joins on it. Amendments are new rows with `supersedes`. Use upsert-by-key so extraction is idempotent | Yes | DuckDB `ASOF JOIN` ([docs](https://duckdb.org/docs/sql/query_syntax/from#as-of-joins)) [I]; Pandera uniqueness check |
| 10 | **Input integrity** (easy to miss) | PDF/HTML extraction splits a comment across rows or attaches it to the wrong loan, so the LLM is "right" about the wrong text | Reconcile the parsed comment count with the count of loans in special servicing; golden tests on parsed EX-99.1 snapshots | Yes | pytest snapshot tests [I] |
| 11 | **Prompt injection or odd content** | Jev "does not treat it as hostile by default" ([jaggedness](https://docs.typesafe.ai/model-jaggedness/jev-1.13.md)) | The threat is low for servicer text [I]. Grounding (#2) and the cross-checks (#7) catch the consequences | Yes | — |

---

## 3. Mapping onto the three named trust layers

| Layer (user's) | What it actually covers | Gaps it closes from §2 |
|---|---|---|
| **Decision models (Jev)** | The output is always a member of the enum, with a probability distribution | #1 fully. It provides the *inputs* for #3, but the thresholds still have to be fit by us |
| **behavex** (BDD runner on top of behave: parallel runs, tags, HTML reports; [repo](https://github.com/hrcorval/behavex)) [V] | "Extensions don't break existing pipelines": scenario-level regression of pipeline behaviour | Parts of #5, #7, #9 and #10, *if* the scenarios run against the **replay cache**. Against the live API they will flake. **Caution:** behavex's `@AUTORETRY` tags would mask LLM non-determinism, so ban them on LLM steps [I]. Note also that PLAN.md names pytest-bdd. Pick one runner; both read the same `.feature` files [I] |
| **Lean 4** | Core identities on the *numbers*: balance conservation, delinquency buckets as a partition, WAC bounds | None of the LLM gaps directly. Lean can prove properties of the **deterministic wrappers**: e.g. the threshold/routing function partitions rows into {accept, review} exactly once; the conformal set always contains the argmax; the as-of join never returns a row with `available_at > t`. These are worth stating only if cheap [I] |

**Not covered by any of the three:** #2 grounding, #3 calibration and abstention, #4 the golden set and annotator agreement, #5 the replay cache and version pinning, #6 drift, #7 the tape cross-checks as *data*, and #9 point-in-time correctness.

**Minimal extra controls**, in priority order and roughly 1–1.5 days of build [I]:

1. **Evidence-span check:** an exact-substring check with offsets, and null-unless-evidenced. It removes the largest class of silent error.
2. **`llm_calls` replay table:** pinned model ID plus `request_hash`. All downstream tests and behavex/pytest-bdd scenarios read from it, which makes the pipeline byte-reproducible.
3. **Golden set:** about 300 rows with 20% double-labeled. Per-field P/R with CIs, κ, and a reliability diagram. Rerun in CI on any model or prompt change.
4. **Per-field thresholds into a `review_queue`.** Use conformal sets if time allows; otherwise a margin threshold fit for about 95% precision.
5. **`xcheck_flags` view:** text vs tape transfer date, workout code and modification code. Report the disagreement rate as a headline trust metric.
6. **Point-in-time keying:** `available_at` = filing acceptance, with an ASOF join. Add one Pandera rule rejecting any feature row whose `available_at` is after the prediction date.

Drift (#6) can wait for a monthly cron after the demo. Cross-provider ensembling (#8) is only worth it on the review band.

**One-line summary [I]:** "The model is never trusted. Its output is accepted only if:
- it is in the enum,
- it quotes text that exists,
- it clears a threshold fit on a hand-labeled set with a known error rate,
- and it doesn't contradict the tape.

Everything else goes to a queue, and every run is replayable byte for byte."
