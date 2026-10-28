"""Write the synthetic recordings in this folder: `uv run python -m tests.fixtures.model.synthetic`.

No model answered these: TYPESAFE_API_KEY is empty, so the answers below were written by hand,
as a careful reader would give them, and every file is marked `"synthetic": true`. The files have
exactly the format a live run writes (`cmbs extract export-recordings`), so a live recording can
replace one without any code change.

`tests/test_model_fixtures.py` rebuilds them and fails if a file no longer matches: a change to
the questions, the sentence splitter or the fixture comment needs a new recording, and a change
of question wording needs a new prompt version.
"""

import datetime as dt
from pathlib import Path

from cmbs.distribution.parse import parse_report
from cmbs.extraction.questions import PROMPT_VERSION, plan, sentence_name, support_name, value_name
from cmbs.extraction.sentences import split
from cmbs.model.policy import Classification
from cmbs.model.recordings import Recording, make_recording
from cmbs.model.scripted import choice_answer
from cmbs.model.types import Answer, Choice, ModelRequest, ModelResponse, NoulAnswer

HERE = Path(__file__).parent
FIXTURE_REPORT = HERE.parent / "distribution" / "fixture-trust-2026-f1_10d_2026-09.htm"
MODEL = "jev-1.14.0"
SETTINGS_VERSION = 7
RECORDED_AT = dt.datetime(2026, 9, 30, 12, 0, tzinfo=dt.UTC)

# Per field: the answer, its probability, the probability that a sentence states the field,
# and the sentence cited (1-based). Loan 2's September 2026 comment names no workout strategy:
# its last sentence is the servicer evaluating the borrower's request.
LOAN_2_SEPTEMBER = {
    "transfer_reason": ("imminent monetary default", 0.94, 0.97, 1),
    "transfer_date": ("3/6/2025", 0.95, 0.96, 1),
    "dscr": ("0.71x", 0.97, 0.95, 2),
    "dscr_as_of": ("12/31/2024", 0.93, 0.92, 2),
    "occupancy": ("61.4%", 0.96, 0.94, 2),
    "occupancy_as_of": ("12/31/2024", 0.91, 0.90, 2),
    "borrower_request": ("maturity extension", 0.93, 0.94, 3),
    "request_status": ("pending", 0.88, 0.90, 4),
    "workout_strategy": ("modification", 0.31, 0.12, 4),
}


def loan_2_september_comment() -> str:
    """Loan 2's comment exactly as the fixture 10-D stores it."""
    report = parse_report(FIXTURE_REPORT.read_bytes())
    return next(comment.text for comment in report.comments if comment.loan == "2")


def _choice(request: ModelRequest, name: str) -> Choice:
    question = request.questions[name]
    assert isinstance(question, Choice)
    return question


def recording(comment: str, answers: dict[str, tuple[str, float, float, int]]) -> Recording:
    sentences = split(comment)
    asked = plan(comment, sentences)
    request = ModelRequest(model=MODEL, state=asked.state, questions=asked.questions)
    given: dict[str, Answer] = {}
    for field, (value, value_p, support_p, sentence) in answers.items():
        given[value_name(field)] = choice_answer(
            _choice(request, value_name(field)), value, value_p
        )
        given[support_name(field)] = NoulAnswer(noul=support_p)
        given[sentence_name(field)] = choice_answer(
            _choice(request, sentence_name(field)), sentences[sentence - 1].text, support_p
        )
    return make_recording(
        request=request,
        response=ModelResponse(model=MODEL, answers=given),
        input_text=comment,
        prompt_version=PROMPT_VERSION,
        classification=Classification.PUBLIC,
        settings_version=SETTINGS_VERSION,
        recorded_at=RECORDED_AT,
        synthetic=True,
    )


FILES = {
    "fixture-f1-loan-2-2026-09.json": lambda: recording(
        loan_2_september_comment(), LOAN_2_SEPTEMBER
    )
}


def render(made: Recording) -> str:
    return made.model_dump_json(indent=2) + "\n"


def main() -> None:
    for name, build in FILES.items():
        (HERE / name).write_text(render(build()))
        print(f"wrote {name}")


if __name__ == "__main__":
    main()
