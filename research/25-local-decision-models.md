# 25 — Running decision models locally (stretch goal)

Written 2026-10-24. Tags, as in `21-trust-pipeline-llm.md`: **[V]** = checked today against the linked primary source. **[S]** = secondary source only. **[I]** = my inference or background knowledge, not re-checked today. **[M]** = measured today on this machine.

Machine: **Apple M5 Max, 128 GB unified memory** (`sysctl`) [M].

## Summary

1. **Jev cannot run locally.** TypeSafe documents no on-prem, VPC, self-hosted or open-weight option. "The same weights serve every account" through one hosted endpoint. The enterprise FAQ *asks* "Can we run Jev in our own VPC or on-premises?" but shows no answer. [V]
2. **Cloudflare Clef is the local path, and it already works on this Mac.**
   - Clef (27B) and Clef-flash (9B) are Apache-2.0 open weights on Hugging Face, post-trained from Qwen3.8-27B and Qwen3.5-9B. The API is "fully compatible with Jev and SystemOne". [V]
   - Both were **already in the local HF cache**. Both ran on MPS in BF16 with the published reference code and CMBS-style questions:
     - **Clef-flash:** ~270–410 ms per request, 20 GB.
     - **Clef:** ~730–860 ms per request, 57 GB. [M]
3. **Clef is architecturally a decision model, not a prompt trick.** It runs one prefill pass, and a small "joint schema head" emits one logit per allowed option. Probabilities are a per-question softmax. [V]
   - Repeat calls on this Mac were **bit-identical at batch size 1**.
   - Batch 1 vs batch 3 differed by up to **0.36–0.42 in logits**. [M]
   - So pin batch size 1 (or cache), as with Jev.
4. **Do the other routes later, not now.** These are logprob scoring with a generic LLM (MLX or llama.cpp) and distilled small classifiers. They are useful as *baselines* or as an eventual CPU-cheap end state, but each needs more work than Clef for a worse fit. Judge/reward models are a poor fit for calibrated multi-class output. [I]
5. **Friday plan:**
   - **Show:** one `Decider` protocol with a `SystemOneDecider` adapter that speaks the shared `/v1/systemone` body to Jev (cloud) and to Clef-flash (local, in-process). Run both on ~20–30 atomic questions and present an agreement table.
   - **Summer:** a 300-item golden set, ECE/Brier per provider, a decision on the Clef vs Jev split, and a distilled classifier.

---

## 1. Jev: any local option? [V]

| Source | Finding |
|---|---|
| [docs `llms.txt`](https://docs.typesafe.ai/llms.txt) | About 150 pages: concepts, cookbooks, SDKs, API. None covers deployment, self-hosting, on-prem or weights. |
| [Models](https://docs.typesafe.ai/models.md) | `jev-1.14.0` (2026-10-14) and `jev-1.13.0`, served from a single `POST /v1/systemone`. "the same weights serve every account". It says nothing on regions, VPC or download. |
| [Enterprise](https://typesafe.ai/enterprise) | The FAQ lists "Can we run Jev in our own VPC or on-premises?" and "Where is our data processed?" with **no visible answer**. ZDR is "available for enterprise customers". Higher rate limits are on request. |

**Conclusion:** there is no public path, and no announcement found. The only option is to ask sales about VPC/on-prem; treat any answer as unknown. Jev's role here stays **cloud reference model** and **label source**.

## 2. Cloudflare Clef

### 2.1 What it is [V]
- **Launch:** announced 2026-10-01 ([Cloudflare blog](https://blog.cloudflare.com/clef-decision-models/)), alongside an RL fine-tuning service. The fine-tuning service is a hands-on FDE service at first, with self-serve "planned".
- **Architecture:** a frozen-backbone LoRA (rank 256) plus a "joint schema head".
  - Inference is "prefill-only"; the head scores valid options in parallel ("non-autoregressive").
  - Training uses label-smoothed CE plus a **Brier loss** "to refine probability calibration", plus RLCD.
- **Calibration evidence:** none published as ECE. The [HF card](https://huggingface.co/Cloudflare/clef) eval block reports accuracy 0.73, Brier 0.10 and KL-from-gold 0.19, sourced to a "LocalLLaMA/typed-decisions" leaderboard I could not verify ([S]).
- **Head file:** the head is a separate 250 MB `joint_head.safetensors`. Its config is 4 layers, width 1024, 2 routing layers. [V]

### 2.2 API shape vs Jev [V]

| | Jev | Clef (Workers AI / HF reference) |
|---|---|---|
| Endpoint | `POST api.typesafe.ai/v1/systemone` | `POST …/accounts/{id}/ai/run/@cf/cloudflare/clef`; SGLang image serves `/v1/systemone` ([card](https://huggingface.co/Cloudflare/clef-flash)) |
| Request | `model`, `state`, `questions`, `assets` | `model`, `state`, `questions`, `images`/`videos` |
| Question types | `choice` (≤255 options), `score` (≤10 levels), `noul`, `box` | `choice`, `score`, `noul` (no `box`) |
| Response | `answers`: choice/score/noul with `probabilities` and `confidence` | Same shape: `model`, `answers`, `usage` |
| Questions per request | not stated | 1–64 ([Workers AI](https://developers.cloudflare.com/workers-ai/models/clef/)) |
| **`confidence`** | `(p_max − 1/n)/(1 − 1/n)` ([docs](https://docs.typesafe.ai/confidence.md)) | **`p_max`** in the HF reference `systemone_answer()` [V, read the code] |

**Integration gotcha:** "compatible" does **not** mean identical confidence semantics. The `Decider` must recompute confidence from `probabilities` itself, and never pass a vendor's value through. Option limits for Clef on Workers AI are not documented. Note also that the Cloudflare blog states Jev's context as 32k; TypeSafe's docs say 64k per request and 32k per question.

### 2.3 Open weights and license [V]
- **Licence:** Apache-2.0 for both [`Cloudflare/clef`](https://huggingface.co/Cloudflare/clef) and [`Cloudflare/clef-flash`](https://huggingface.co/Cloudflare/clef-flash), "following the base model".
- **Open source, not open data:** The Register reports that the training data is not public ([S]).

### 2.4 Running locally [V] + [M]
**Supported runtimes, per the cards [V]:**
- transformers 5.10.2 + torch 2.11 with the bundled `joint_schema_model.py` ("Tested … on a single H200").
- SGLang (`lmsysorg/sglang:dev-clef`, H200/B200/B300).
- `vllm serve`.
- Docker Model Runner.

**Runtime caveats [I]:**
- Plain `vllm serve` and the community GGUF/MLX quantizations (41–57 listed) **probably drop the joint head**. They would give you a Qwen chat model, not Clef decisions. The card does not say they carry the head, so check before trusting any quant.
- Apple Silicon/MLX is not mentioned on the cards.

**What I ran [M]:**
- **Setup:**
  - Weights were already cached from 2026-10-12: Clef-flash 18 GB, Clef 51 GB.
  - Their cached revisions (`8b2e5fd`, `0b33120`) are **older than HF main** (`f0b7d3c`, `a30162b`, updated 10-19), so **pin a revision**.
  - No weights were downloaded today. Torch, transformers, accelerate and torchvision went into a scratch venv.
  - `load_release_model(path, device="mps")`, BF16, unmodified reference code.
  - transformers fell back to the pure-torch linear-attention path, because flash-linear-attention is CUDA/Triton only.
- **Test:** three 10-D-style comments, each asking `transfer_reason` (5-way choice), `severity` (4-level score) and `modification_requested` (noul).

| | Clef-flash 9B | Clef 27B |
|---|---|---|
| Load | 5.7 s | 60 s |
| Per request (3 questions, ~40-word state) | 269–413 ms | 731–863 ms |
| Batch of 3 | 682 ms | 2.2 s |
| GPU memory | 20.1 GB | 57.3 GB |
| Top choice on the 3 comments | imminent / maturity / non-monetary, all correct, p ≈ 0.93–0.95 | same, p ≈ 0.95–0.96 |
| Same input 5×, batch 1 | bit-identical | bit-identical |
| Batch 1 vs batch 3 max logit diff | 0.36 | 0.42 |

- **Cloudflare's H200 numbers:** median 39 ms (flash) and 209 ms (Clef), vs Jev 524 ms (card, Decision Index) [V, vendor-run].
- **The Register's VRAM figures** for self-hosting, 41 GB (flash) and 85 GB (Clef) ([S]), are well above the BF16 footprint I measured. They probably include serving headroom.

**Hardware:** this Mac runs both. A 32 GB+ Mac runs Clef-flash [I]. For throughput or batch-invariant serving, use an NVIDIA box with SGLang.

### 2.5 Pricing and limits (Workers AI) [V]
| | Clef | Clef-flash | Jev |
|---|---|---|---|
| $/M input tokens (output free) | **$0.24** | **$0.038** | $0.042 |
| Context | 65,536 | 24,576 | 64k/request |
| Params | 27B | 9B | undisclosed |

- **Common limits:** 64 questions per request, ≤4 images, 13 MiB body.
- **Rate limits:** the general Workers AI [limits page](https://developers.cloudflare.com/workers-ai/platform/limits/) lists 300 rpm for text generation and has no Clef entry, so treat it as unverified.
- **Local truncation:** the local `encode_record` defaults to `max_length=16384` and truncates `state`. 10-D comments are far shorter.
- **Data:** Cloudflare says it does not store or train on requests unless you opt into fine-tuning [V, blog]. Run locally, nothing leaves the machine, which also unblocks portal/CREFC text (`21` §1.4).

## 3. Other ways to get the Jev interface locally

| Route | Probabilities over options | Calibration | Reproducibility | Apple Silicon latency | Effort |
|---|---|---|---|---|---|
| **(a) Generic LLM, option scoring** ([vLLM structured outputs](https://docs.vllm.ai/en/latest/features/structured_outputs.html) `choice` on xgrammar/guidance [V]; [llama.cpp server](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md) `grammar`/`json_schema` + `n_probs` [V]; MLX `mlx-lm` [I]) | Best done by **teacher-forced scoring**: sum the logprobs of each option's tokens given the prompt, then softmax across options. Single-letter labels (A/B/C…) avoid length bias. Reading the probabilities from a *constrained sample* only gives first-token mass renormalized by the mask [I] | Poor out of the box. Instruction-tuned LLM logprobs are typically overconfident, so they need temperature scaling on the golden set [I]. Also inherits position bias, so rotate option order | vLLM `VLLM_BATCH_INVARIANT=1` covers NVIDIA ≥ sm80, AMD Instinct and Intel XPU only, **not Metal** [V]. llama.cpp warns `cache_prompt` makes logits "not guaranteed to be bit-for-bit identical" [V]. On Mac: batch 1, no prompt cache, plus a replay cache | One prefill per question plus K short scorings. Roughly Clef-like for 8B 4-bit in MLX or llama.cpp Metal [I, not measured] | 1–2 days for an MLX scorer. Write the prompt template yourself, which is exactly what Jev/Clef remove |
| **(b) Distilled small classifier** ([SetFit](https://github.com/huggingface/setfit), DeBERTa-v3, ModernBERT) trained on Jev/Clef labels | Softmax head, one model per field. Can train on Jev's **soft** probabilities (KL loss), not just argmax [I] | Good after temperature scaling. Small encoders calibrate well in-domain and drift out of domain [I] | CPU fp32 at fixed batch is effectively deterministic [I] | ~5–30 ms per field on CPU/MPS [I] | 2–4 days plus ~1–2k labeled comments. The question set is **frozen**: a new field means retraining. Best as the summer "cheap, auditable" end state and as a third opinion |
| **(c) Reward / judge models** | Scalar per (state, option), softmaxed across options | Rewards are trained for *ranking*, not class probability. Calibration is not a design goal [I] | Same kernel issues as (a) | Similar to (a) | Not recommended |

## 4. Recommended local path

### 4.1 Keep one interface
This extends `21` §1.7. Because Jev and Clef share the SystemOne body, **one adapter covers three backends**:

```python
class Decider(Protocol):
    id: str                                   # e.g. "jev-1.14.0", "clef-flash@8b2e5fd"
    def decide(self, state: str, qs: dict[str, Question]) -> DecisionResult: ...

class SystemOneDecider:                       # Jev cloud, Workers AI Clef, or SGLang Clef
    def __init__(self, transport: Callable[[dict], dict], model_id: str): ...

def jev_http(key) -> Callable[[dict], dict]: ...          # POST api.typesafe.ai/v1/systemone
def clef_inprocess(rev, device="mps") -> Callable[[dict], dict]:
    # load_release_model(...) once; return lambda body: systemone(model, proc, body)
```

**Rules:**
- **Normalise:** `DecisionResult` carries `probs` only. `confidence` is computed in our code with the Jev formula for every provider (§2.2).
- **Identity:** `model_id` includes the HF revision hash.
- **Replay cache:** calls go through the `llm_calls` replay cache keyed on `request_hash` (`21` §2 #5), with batch size fixed at 1 for local runs.
- **Coupling:** pipeline code imports only `Decider`. Selecting a provider is a config value.
- **Licence check:** the reference `joint_schema_model.py` is Apache-2.0 and can be **vendored** (23 KB, pure torch, read today). Vendoring avoids importing code from the HF cache via `sys.path`.

### 4.2 Evaluating local vs Jev
Run the same atomic questions (one comment, one field) on every provider. Use **two option orders** (original and reversed) to expose first-option bias.

1. **Agreement:**
   - top-1 agreement rate, with a Wilson CI;
   - Cohen's κ;
   - a confusion matrix per field.
2. **Distribution distance:** mean total-variation (or JS) distance between the probability vectors. For `score` fields, the |Δ expected score|.
3. **Calibration:** against golden labels only, never against Jev as ground truth.
   - Brier score, ECE (10 bins) and a reliability diagram per provider and field.
   - Selective accuracy at a fixed coverage, e.g. accept the top 80% by confidence.
4. **Stability:**
   - re-run variance;
   - batch 1 vs batch N drift (§2.4);
   - flip rate under option reorder.
5. **Disagreements:** they go to the review queue (`21` §2 #8). Note that agreement without golden labels only measures *consistency*, not correctness.

### 4.3 Friday (2026-10-30) vs summer

| Friday: realistic, about 1 day | Summer |
|---|---|
| `Decider` protocol, `SystemOneDecider` with `jev_http` and `clef_inprocess("clef-flash")` | ~300-item golden set; ECE/Brier/selective accuracy for Jev, Clef, Clef-flash |
| 20–30 hand-picked 10-D comments × 3 fields, run on both, stored in `llm_calls` | Calibrated thresholds per provider; conformal sets |
| One table: per-field agreement %, κ, mean TV distance; 2–3 disagreement examples shown side-by-side | Option-order and batch-invariance tests in behavex (`@llm` scenarios read the replay cache) |
| Live: pull the network cable (or `HF_HUB_OFFLINE=1`) and answer one question locally in under 0.5 s | SGLang Clef on a CUDA box; Cloudflare RL fine-tune on CMBS labels if the FDE service is accessible |
| Honest claim: "same interface, local model, agreement X on n=… — not yet calibrated" | Distilled ModernBERT/SetFit baseline (§3b) as the cheap deterministic tier |

**Do not claim on Friday:** calibration, accuracy beyond the tiny sample, or that Clef "beats" Jev. The vendor benchmarks conflict by task: Jev leads on GPQA, BBH and ANLI; Clef leads on BANKING77 and ContractNLI ([card](https://huggingface.co/Cloudflare/clef-flash)).

### Unverified / open
- The Register's VRAM figures and Clef training-data status [S].
- Whether community quants include the joint head.
- Workers AI rate limits for Clef.
- The Decision Index entrants "Laya" and "Kev 9B" (vendor table only).
- Jev on-prem: ask TypeSafe sales.

Secondary coverage seen but not relied on:
- [haimaker.ai](https://haimaker.ai/de-de/blog/entscheidungsmodelle-erklaert-jev-clef)
- [flaviocopes.com/clef](https://flaviocopes.com/clef/)
- [The Register](https://www.theregister.com/a/5300649)
- [eWeek](https://www.eweek.com/news/cloudflare-clef-ai-decision-models-agent-workflows/)
