# Local model answers

Real answers from Cloudflare's Clef-flash, pinned as `clef-flash-2026-10-01`
(`Cloudflare/clef-flash` at commit `8b2e5fd17c09fd49bc2880b805d5323ec7ff4ff3`, see
`cmbs.model.local`), recorded on 2026-10-31 on this project's Mac (Apple GPU, MPS, BF16) with
the model card's reference code at batch size 1. A second run gave bit-identical answers.

Each `<name>.json` answers the comment of `../live/<name>.json` with exactly the questions Jev was
asked (prompt version 3), in the recording format of `cmbs.model.recordings.Recording`. They are
not synthetic and can be imported as recordings. The settings version noted, 8, is the one the
scenarios approve to name the local model; the abstention band is version 7's, 0.30 to 0.70.
Clef's own `confidence` field is kept as it answered (its top probability); the panel computes
confidence from the probabilities instead (`cmbs.model.policy.confidence`).

`agreement-report.md` is the agreement report between Jev's answers and these
(`uv run python -m tests.fixtures.model.local.cases`). Agreement on five comments measures
consistency, not accuracy, and says nothing about calibration.

## Recording again

The weights are read from the Hugging Face cache and never downloaded.

```bash
uv sync --extra local-model
uv run python tools/clef_server.py --port 30000          # loads the pinned snapshot once
CMBS_CLEF_URL=http://127.0.0.1:30000 uv run python tools/record_local_model.py
```

Or, without a server, `uv run python tools/record_local_model.py` runs the model in-process.
These files were recorded through the server, run by a Python 3.12 environment holding torch
2.11.0 and transformers 5.10.2 (the versions of the `local-model` extra).
