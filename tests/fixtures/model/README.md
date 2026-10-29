# Model recordings

Recorded model calls that tests and scenarios replay. Tests never call a model provider.

## Every file here is synthetic

**No model produced these answers.** `TYPESAFE_API_KEY` was empty when they were made, so the
answers were written by hand, as a careful reader of the comment would give them, and each file
says `"synthetic": true`. They show how the pipeline treats answers; they say nothing about how
well Jev reads comments. The extraction quality feature measures that, on live answers.

| File | Text | Model, prompt version | Settings version |
|---|---|---|---|
| `fixture-f1-loan-2-2026-09.json` | Loan 2's September 2026 special-servicing comment in the synthetic 10-D for "Fixture Trust 2026-F1" (`../distribution/fixture-trust-2026-f1_10d_2026-09.htm`) | `jev-1.14.0`, 3 | 7 |

The answers it holds, per field (value, probability; probability that a sentence states it;
sentence cited): transfer reason "imminent monetary default" (0.94; 0.97; 1), transfer date
3/6/2025 (0.95; 0.96; 1), DSCR 0.71x as of 12/31/2024 and occupancy 61.4% as of 12/31/2024 (all
above 0.90; sentence 2), borrower request "maturity extension" (0.93; 0.94; 3), request status
"pending" (0.88; 0.90; 4), and no workout strategy (0.31; 0.12), which the decision table reads
as not stated.

## Format

One JSON file per call, the format `cmbs.model.recordings.Recording` defines: the request as
sent (model, state, questions), the response as received, the hashes the call is keyed on
(questions, state, exact input text), the data classification, the settings version in force
and when it was recorded. A live run writes the same format: it records every call in
`app.model_call`, and `cmbs extract export-recordings <dir>` writes them out as these files;
`cmbs extract import-recordings <dir>` loads files back. A file whose key does not match its
content, or whose answer came from another model than the one asked, is refused on import.

## Regenerating

`uv run python -m tests.fixtures.model.synthetic` rewrites the files from `synthetic.py`.
`tests/test_model_fixtures.py` fails when a file no longer matches what the code would ask:
a change to the questions, the sentence splitter or the fixture comment needs a new recording,
and new question wording needs a new prompt version.
