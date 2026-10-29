# Extraction quality fixtures

Synthetic data for `features/governance/extraction_quality.feature` and `tests/test_quality*.py`.
Nothing here is a filed comment or a real person's labels; `alex` and `morgan` are the fixture
analysts of `features/README.md`.

| File | What it is |
|---|---|
| `golden_set.json` | 100 synthetic special-servicing comments labelled for the borrower request and the request status ("not stated" is `null`). 40 state a borrower request. `alex` labels G001 to G060, `morgan` G061 to G080, both G081 to G100; they agree on 19 of those 20 (on G082 they differ on the request status only). |
| `make_golden_set.py` | Writes `golden_set.json` deterministically: `uv run python tests/fixtures/quality/make_golden_set.py`. |

Predictions (what the pipeline found in each golden comment) are built by the scenario steps
and the tests from the counts each one states, so no predictions file is committed.
