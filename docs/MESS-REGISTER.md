# Mess register

This register records messy documents, unclear definitions and missing standards found in the data, in the industry and in vendor docs (including TypeSafe's). Each entry says how our build makes the issue unambiguous. Anything the automation doesn't catch gets written down here, in `GRILL-LOG.md` (decisions) or in the glossary (definitions).

**Status values:**
- **open**: not handled yet
- **resolved-in-code**: a rule, test or contract handles it
- **resolved-in-docs**: a definition or decision handles it
- **escalate**: ask the desk or the vendor

| ID | Where | The mess | How we make it clear | Status |
|---|---|---|---|---|
| M-001 | SEC N-PORT | CMBS has no category of its own: CMBS and RMBS are both filed as `ABS-MBS`. | A layered rule classifier with a manual-override file and rule IDs on every row. | resolved-in-code |
| M-002 | SEC N-PORT | Some consumer/auto/timeshare ABS (Hertz, Exeter, Upstart, Navient) are *misfiled* under `ABS-MBS` in older periods. | Exclusion rule L2d, run before the SASB-ticker rule, plus a regression check. | resolved-in-code |
| M-003 | SEC N-PORT | CMBX index names are written inconsistently ("BBB Series 12" vs "BBB- Series 12"), so the rating doesn't parse. | Normalize the index name to {series, rating} with a lookup table; a test covers each series seen. | open |
| M-004 | SEC N-PORT | Futures are filed with market value equal to notional, and "Recv Irs" swaps are filed with a fixed *payment* leg, so their direction is ambiguous. | Derivatives are excluded from the mix charts; swap direction is flagged as unknown, never inferred. | resolved-in-docs |
| M-005 | ABS-EE (Reg AB II) | No free-text or commentary field, and **no appraisal-reduction field** in the CMBS asset XML. | Text comes only from 10-D EX-99.1. The appraisal reduction amount (ARA) is marked "not available from public tape" in the glossary. | resolved-in-docs |
| M-006 | ABS-EE | "Most recent financials" are stale: median 73 days, p90 254 days, max 619 days, with no flag marking them stale. | Store the financials' start and end dates alongside every NOI/DSCR value; a derived `staleness_days` column; the UI shows the age next to the number. | open |
| M-007 | ABS-EE vs 10-D | Several dates mean different things: reporting-period end = determination date ≠ distribution date ≠ filing date. | Glossary definitions, plus an `available_at` = filing-date as-of join for every model feature. | open |
| M-008 | SASB 144A deals | Most SASB deals file no ABS-EE or 10-D at all. Split loans appear only inside public conduits. | Coverage table per deal (public tape? 10-D? via a pari passu note in which conduit?). | open |
| M-009 | 10-D EX-99.1 | The layout depends on the certificate administrator: Computershare has a "Special Servicing Comments" column, Citi has no comment text. | One parser per layout, with a `layout` column; unknown layouts fail loudly. | open |
| M-010 | 10-D comments | Special-servicing comments appear only **after** a loan transfers, so they can't give early warning for the transfer itself. | Thesis scoped accordingly (GRILL-LOG Q2); the "lead" is measured on later outcomes (modification, liquidation, loss). | resolved-in-docs |
| M-011 | Market data | The delinquency rate depends on the source: Trepp 30+ days, KBRA distress, Fitch loan default index. The year-ago Trepp figure appears as 7.55% vs 7.23% in different sources. | Glossary defines each, and every figure we use carries its source and definition. | resolved-in-docs |
| M-013 | TypeSafe docs | The skill says Jev returns "calibrated probabilities", but the docs publish no calibration metric. The skill also says `confidence` measures concentration, not correctness. | Our own calibration check on labeled atomic questions; `confidence` is never used alone as a gate. | open |
| M-014 | TypeSafe docs | `jev-latest` is a moving alias with no published deprecation policy. | Pin the version (e.g. `jev-1.14.0`); the response's `model` field is stored with every call. | open |
| M-015 | TypeSafe docs | Access status was inconsistent (homepage said "early access", docs said self-serve keys, a blog reported signups paused). | Resolved 2026-10-24: open access, and the user has a key. | resolved-in-docs |
| M-017 | Clef vs Jev | Both claim the "same API", but `confidence` means different things. Clef's reference code uses the top probability (`p_max`); Jev rescales it as `(p_max−1/n)/(1−1/n)`. | The `Decider` computes confidence itself from the returned probabilities and ignores the provider's field. | open |
| M-018 | Clef community builds | The quantised GGUF/MLX builds and plain `vllm serve` may lack Clef's separate scoring head, which would silently make it an ordinary chat model. | Use only the official weights at a pinned Hugging Face revision; a startup check verifies the head is loaded. | open |
| M-019 | Clef on Apple MPS | Answers are identical at batch size 1, but batching shifts logits by up to 0.42. | Fix batch size at 1 locally; replay cache; record the batch size in `llm_calls`. | open |
| M-016 | Industry naming | Entities have been renamed or changed hands: Wells servicing → Trimont, Wells trust → Computershare, DBRS Morningstar → Morningstar DBRS, Arena → CCaventus. | Glossary entries carry an "as of" date; agents re-verify named entities. | resolved-in-docs |
