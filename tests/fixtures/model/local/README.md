# Local model answers

These are real answers from Cloudflare's Clef-flash, pinned as `clef-flash-2026-10-01`. The pin is
`Cloudflare/clef-flash` at commit `8b2e5fd17c09fd49bc2880b805d5323ec7ff4ff3`, together with the
SHA-256 of every file the model reads and of its reference code (`cmbs.model.local`).

They were recorded on 2026-10-31 on this project's Mac (Apple GPU, MPS, BF16):

- The weights and files came from the checked Hugging Face cache.
- The model ran through the vendored reference code (`cmbs.model.clef_reference`) at batch size 1.
- Probabilities are an unrounded float64 softmax, so they sum to 1.
- A second run gave bit-identical answers. The answers match an earlier recording through the
  reference code's own rounding to within 5e-5, with the same top option everywhere.

Each `<name>.json` answers the comment of `../live/<name>.json` with exactly the questions Jev was
asked (prompt version 3). The files use the recording format of `cmbs.model.recordings.Recording`.

- Each response names the pinned `revision` and `reference_sha256`, and these are checked on
  replay.
- They are not synthetic, and can be imported as recordings.
- The settings version noted, 8, is the one the scenarios approve to name the local model. The
  abstention band is version 7's, 0.30 to 0.70.
- Clef's own `confidence` field is kept as it answered (its top probability), and nothing reads
  it. The panel computes confidence from the probabilities instead (`cmbs.model.policy.confidence`).

`agreement-report.md` is the agreement report between Jev's answers and these
(`uv run python -m tests.fixtures.model.local.cases`). Agreement on five comments measures
consistency, not accuracy, and says nothing about calibration.

## Recording again

The weights are read from the Hugging Face cache, checked against the pin, and never downloaded.

```bash
uv sync --extra local-model
uv run python tools/clef_server.py --port 30000          # loads the pinned snapshot once
CMBS_CLEF_URL=http://127.0.0.1:30000 uv run python tools/record_local_model.py
```

Without a server, `uv run python tools/record_local_model.py` runs the model in this process.

These files were recorded through `tools/clef_server.py`, run from a Python 3.12 environment with
torch 2.11.0 and transformers 5.10.2 (the `local-model` extra's versions). Those were the only
torch wheels cached on this machine.

An SGLang server cannot be used to record: it does not report the revision and reference hash
that every Clef answer must carry.
