"""Jev's live answers paired with the local model's, comment by comment.

`uv run python -m tests.fixtures.model.local.cases` prints the agreement report on them;
`agreement-report.md` here is its output, which tests/test_model_local_agreement.py checks.

Each `<name>.json` here is a recording (the format of `cmbs.model.recordings.Recording`) of
Clef-flash answering the comment of `../live/<name>.json`, whose answers are Jev's. The pair
must be the same request apart from the model: the same state and the same questions.
"""

import json
from decimal import Decimal
from pathlib import Path

from cmbs.model.policy import sha256
from cmbs.model.recordings import Recording, read_file
from cmbs.model.types import ModelRequest, ModelResponse
from cmbs.quality.agreement import Case, compare, report_text

HERE = Path(__file__).parent
LIVE = HERE.parent / "live"
# The abstention band of settings version 7, which the local model's settings keep.
LOW, HIGH = Decimal("0.30"), Decimal("0.70")


def local_recordings() -> dict[str, Recording]:
    """The local model's recordings, by the name of the comment they answer."""
    return {path.stem: read_file(path) for path in sorted(HERE.glob("*.json"))}


def case(name: str, recording: Recording) -> Case:
    live = json.loads((LIVE / f"{name}.json").read_text())
    jev = ModelRequest.model_validate(live["request"])
    if recording.input_sha256 != sha256(live["comment"]):
        raise ValueError(f"{name}: the local recording answers another comment")
    if (recording.request.state, recording.request.questions) != (jev.state, jev.questions):
        raise ValueError(f"{name}: the two models were asked different questions")
    return Case(
        name=name,
        comment=live["comment"],
        first=ModelResponse.model_validate(live["response"]),
        second=recording.response,
    )


def cases() -> list[Case]:
    """Every comment both models answered, Jev first, in name order."""
    return [case(name, recording) for name, recording in local_recordings().items()]


def main() -> None:
    print(report_text(compare(cases(), low=LOW, high=HIGH), low=LOW, high=HIGH), end="")


if __name__ == "__main__":
    main()
