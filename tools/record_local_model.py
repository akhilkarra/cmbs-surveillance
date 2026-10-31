"""Record the local model's answers to the comments Jev answered in tests/fixtures/model/live/.

Usage (with a Clef server running, see tools/clef_server.py):
  CMBS_CLEF_URL=http://127.0.0.1:30000 uv run python tools/record_local_model.py
  # or, with the local-model extra installed, in this process (no CMBS_CLEF_URL):
  uv run python tools/record_local_model.py

Each comment is asked exactly the questions prompt version 3 asks (`cmbs.extraction.questions`),
once, through `cmbs.model.client.ModelClient` with the Clef provider, so the request is checked,
the answering model and its pinned revision and reference code verified, and the call recorded
exactly as a run records it. The comments are public, so a Clef server may read them. One recording
file per comment is written to tests/fixtures/model/local/<name>.json, named like Jev's file.
The settings version noted is the one the scenarios approve to name the local model (8).
"""

import argparse
import json
import sys
import time
from pathlib import Path

from cmbs import clock
from cmbs.extraction.questions import PROMPT_VERSION, plan
from cmbs.extraction.sentences import split
from cmbs.model.client import Ask, ModelClient
from cmbs.model.local import CLEF_FLASH_2026_10_01
from cmbs.model.policy import Classification
from cmbs.model.providers import provider_for
from cmbs.model.recordings import MemoryRecordings, Recording
from cmbs.settings.service import InForce
from cmbs.settings.values import Settings

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "tests" / "fixtures" / "model" / "live"
LOCAL = ROOT / "tests" / "fixtures" / "model" / "local"
SETTINGS_VERSION = 8


def record(name: str, comment: str, client: ModelClient, settings: InForce) -> Recording:
    asked = plan(comment, split(comment))
    answered = client.ask(
        Ask(
            questions=asked.questions,
            state=asked.state,
            input_text=comment,
            classification=Classification.PUBLIC,
            subject=f"live fixture {name}",
        ),
        settings,
        now=clock.now(),
    )
    return answered.recording


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--model", default=CLEF_FLASH_2026_10_01.name)
    args = parser.parse_args(argv)
    settings = InForce(
        version=SETTINGS_VERSION, values=Settings(model=args.model, prompt_version=PROMPT_VERSION)
    )
    client = ModelClient(MemoryRecordings(), provider=provider_for(args.model), replay_only=False)
    LOCAL.mkdir(parents=True, exist_ok=True)
    for path in sorted(LIVE.glob("*.json")):
        comment = json.loads(path.read_text())["comment"]
        started = time.perf_counter()
        recording = record(path.stem, comment, client, settings)
        (LOCAL / path.name).write_text(recording.model_dump_json(indent=2) + "\n")
        print(f"{path.stem}: {time.perf_counter() - started:.2f} s", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
