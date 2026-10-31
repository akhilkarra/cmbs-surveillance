"""Steps for features/extraction/local_model.feature: Clef-flash as the local fallback model.

Loan 2's September 2026 comment is loaded from the fixture deal's synthetic 10-D, the way
features/steps/extraction.py loads it, so its "When the comment is extracted" step reads it.

The local model's answers come from tests/fixtures/model/local/, recorded by running
Clef-flash on this machine (tools/record_local_model.py). Where a scenario needs a request to
reach the local model, the Clef provider is given a transport that stands in for the model in
this process: it answers the `/v1/systemone` body as the reference code would, including
Clef's own `confidence` (the top probability), and never loads weights.
"""

import datetime as dt
from decimal import Decimal
from pathlib import Path
from typing import Any

from behave import given, then, when  # pyright: ignore[reportAttributeAccessIssue]
from sqlalchemy import select

from cmbs import clock
from cmbs.distribution.service import comments_for_loan, load_report
from cmbs.domain import Period
from cmbs.extraction.fields import FIELDS, spec
from cmbs.extraction.questions import plan, value_name
from cmbs.extraction.sentences import split
from cmbs.extraction.service import SourceText, extract_text
from cmbs.extraction.tables import Extraction
from cmbs.model.client import ModelClient
from cmbs.model.local import CLEF_FLASH_2026_10_01, is_local_model, local_model
from cmbs.model.policy import Classification, confidence
from cmbs.model.providers import Clef, Jev
from cmbs.model.recordings import DbRecordings, import_recordings
from cmbs.model.scripted import ScriptedProvider, respond
from cmbs.model.tables import ModelCall
from cmbs.model.types import Choice, ChoiceAnswer, ModelRequest
from cmbs.quality.agreement import compare, report_text
from cmbs.settings.service import in_force, store_version
from cmbs.testing.bdd import attempt, in_session
from cmbs.testing.world import FIXTURE_CIK, FIXTURE_DEAL
from tests.fixtures.model.local.cases import HERE as LOCAL_RECORDINGS
from tests.fixtures.model.local.cases import cases

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "tests" / "fixtures" / "distribution" / "fixture-trust-2026-f1_10d_2026-09.htm"
SEPTEMBER = Period(2026, 9)
ACCESSION, FILED = "0009900001-26-000009", dt.date(2026, 9, 29)
TODAY = dt.datetime(2026, 10, 1, 12, 0, tzinfo=dt.UTC)  # after the September 10-D
LOADER, READER = "quinn", "pat"
LOCAL_SETTINGS_VERSION = 8
PORTAL_COMMENT = (
    "The borrower has asked the lender for a six-month forbearance. "
    "The special servicer is reviewing the request with the sponsor."
)
# Read from the recorded answers by hand: the comments and fields where Jev (decision table
# under the 0.30 to 0.70 band) and Clef-flash decide differently.
DISAGREEMENTS = {
    ("bnk13-loan-11", "workout_strategy"),
    ("bnk13-loan-57", "transfer_reason"),
    ("bnk13-loan-57", "workout_strategy"),
    ("hard-request-granted", "transfer_reason"),
    ("hard-request-granted", "workout_strategy"),
}


@given('loan "{loan}" of "{deal}" has its {period} special-servicing comment')
def step_has_its_comment(context, loan, deal, period):
    assert (loan, deal, Period.parse(period)) == ("2", FIXTURE_DEAL, SEPTEMBER)
    clock.set_now(TODAY)
    in_session(
        lambda db: load_report(
            db,
            user=LOADER,
            deal=FIXTURE_DEAL,
            accession=ACCESSION,
            cik=FIXTURE_CIK,
            filing_date=FILED,
            file_name=REPORT.name,
            content=REPORT.read_bytes(),
            period=SEPTEMBER,
        )
    )
    context.deal = FIXTURE_DEAL
    # What features/steps/extraction.py's steps expect once September is loaded.
    context.september_loaded = True
    context.provider = ScriptedProvider()
    [comment] = [
        item.comment
        for item in in_session(lambda db: comments_for_loan(db, deal=FIXTURE_DEAL, loan="2"))
        if item.period == SEPTEMBER
    ]
    context.comment = comment


def _name_local_model(model: str, prompt_version: int) -> None:
    def store(db):
        current = in_force(db)
        store_version(
            db,
            version=LOCAL_SETTINGS_VERSION,
            values=current.values.model_copy(
                update={"model": model, "prompt_version": prompt_version}
            ),
            proposed_by="quinn",
            approved_by="rae",
            approved_at=clock.now() - dt.timedelta(hours=1),
        )

    in_session(store)


@given('the settings name the local model "{model}" with prompt version {version:d}')
def step_settings_name_local_model(context, model, version):
    assert is_local_model(model), f"{model} is not a pinned local model"
    _name_local_model(model, version)
    values = in_session(in_force).values
    assert (values.model, values.prompt_version) == (model, version)
    context.local_model = model


@given("its answers are replayed from calls recorded on this machine")
def step_local_answers_replayed(context):
    imported = in_session(lambda db: import_recordings(DbRecordings(db), LOCAL_RECORDINGS))
    assert imported == 5, imported
    models = in_session(lambda db: set(db.scalars(select(ModelCall.model))))
    assert models == {context.local_model}, models


@then("the extraction records the local model and its pinned version")
def step_extraction_records_local_model(context):
    pinned = local_model(context.local_model)

    def stored(db):
        extraction = db.scalars(
            select(Extraction).where(Extraction.model_call == context.view.model_call)
        ).one()
        call = db.get(ModelCall, extraction.model_call)
        return extraction.model, call.model, call.answered_by

    assert in_session(stored) == (pinned.name,) * 3
    assert context.view.model == pinned.name
    # The name is the pin: it binds one commit of the weights and the code that runs them.
    assert (pinned.repo, pinned.revision) == (
        "Cloudflare/clef-flash",
        "8b2e5fd17c09fd49bc2880b805d5323ec7ff4ff3",
    )


# Confidence.


def _request(context, model: str) -> ModelRequest:
    asked = plan(context.comment, split(context.comment))
    return ModelRequest(model=model, state=asked.state, questions=asked.questions)


def _systemone(answers: dict[str, dict[str, Any]]):
    """A stand-in for the model: answers `answers` where given, defaults elsewhere, as a
    `/v1/systemone` body, under the model name it was asked for. Keeps every body sent."""
    sent: list[dict[str, Any]] = []

    def answer(body: dict[str, Any]) -> dict[str, Any]:
        sent.append(body)
        reply = respond(ModelRequest.model_validate(body), {}).model_dump(mode="json")
        reply["answers"] |= answers
        return reply

    return answer, sent


@given(
    "the local model answers the borrower request with probabilities {first}, {second} and {third}"
)
def step_local_answers_request(context, first, second, third):
    model = CLEF_FLASH_2026_10_01.name
    question = _request(context, model).questions[value_name("borrower_request")]
    assert isinstance(question, Choice)
    given = [float(first), float(second), float(third)]
    options = list(question.criteria)
    probabilities = {option: 0.0 for option in options} | dict(zip(options, given, strict=False))
    top = max(probabilities, key=probabilities.__getitem__)
    # Clef's reference code reports the top probability as its confidence.
    clef_answer = {
        "type": "choice",
        "choice": top,
        "confidence": probabilities[top],
        "probabilities": probabilities,
    }
    transport, _ = _systemone({value_name("borrower_request"): clef_answer})
    context.local_provider = Clef(model, transport, local=True)
    context.probabilities = [Decimal(first), Decimal(second), Decimal(third)]
    context.options = len(options)


@when("the answer is read")
def step_answer_is_read(context):
    request = _request(context, context.local_provider.model.name)
    response = context.local_provider.ask(request)
    name = value_name("borrower_request")
    question, answer = request.questions[name], response.answers[name]
    assert isinstance(question, Choice) and isinstance(answer, ChoiceAnswer)
    context.question, context.answer = question, answer
    context.confidence = confidence(question, answer)


@then(
    "its confidence is computed from those probabilities as for Jev, "
    "not taken from the model's own field"
)
def step_confidence_computed(context):
    n, top = context.options, max(context.probabilities)
    jev_measure = (n * top - 1) / (n - 1)  # (p_max - 1/n) / (1 - 1/n)
    assert context.confidence == jev_measure, (context.confidence, jev_measure)
    assert context.answer.confidence == float(top), "the model's own field is its top probability"
    assert context.confidence != top
    # Jev's answer with the same probabilities, and Jev's own field, reads the same.
    jev_answer = context.answer.model_copy(update={"confidence": round(float(jev_measure), 2)})
    assert confidence(context.question, jev_answer) == context.confidence
    assert Jev.local is False and context.local_provider.local is True


# Data retention.


@given("zero data retention is not configured")
def step_no_zero_data_retention(context):
    assert not in_session(in_force).values.zero_data_retention


@given("a restricted comment from an investor portal")
def step_restricted_portal_comment(context):
    context.source = SourceText(
        text=PORTAL_COMMENT,
        classification=Classification.RESTRICTED,
        subject=f"investor portal comment on loan 2 of {FIXTURE_DEAL}",
    )


@when("it is extracted with the local model")
def step_extracted_with_local_model(context):
    model = CLEF_FLASH_2026_10_01.name
    _name_local_model(model, 3)
    transport, context.sent = _systemone({})
    provider = Clef(model, transport, local=True)

    def run(db):
        client = ModelClient(DbRecordings(db), provider=provider, replay_only=False)
        return extract_text(db, user=LOADER, source=context.source, client=client)

    context.reading = attempt(context, lambda: in_session(run))


@then("the comment is read, because it never leaves this machine")
def step_comment_read_locally(context):
    assert context.refusal is None, f"refused: {context.refusal}"
    [body] = context.sent
    assert body["state"]["comment"] == PORTAL_COMMENT
    recording = context.reading.answered.recording
    assert (recording.model, recording.classification) == (
        "clef-flash-2026-10-01",
        Classification.RESTRICTED,
    )
    assert not in_session(in_force).values.zero_data_retention


# Agreement.


@given("recorded answers from Jev and from the local model for the same comments")
def step_recorded_answers_both(context):
    context.cases = cases()
    models = {(case.first.model, case.second.model) for case in context.cases}
    assert models == {("jev-1.14.0", "clef-flash-2026-10-01")}, models
    assert len(context.cases) == 5


@when("the agreement report is built")
def step_agreement_report_built(context):
    values = in_session(in_force).values
    low, high = values.abstention_low, values.abstention_high
    context.report = compare(context.cases, low=low, high=high)
    context.report_text = report_text(context.report, low=low, high=high)


@then("it shows, for each field, how often the two models give the same outcome and the same value")
def step_shows_each_field(context):
    asked = {
        field_spec.name: sum(
            field_spec.name in plan(case.comment, split(case.comment)).asked
            for case in context.cases
        )
        for field_spec in FIELDS
    }
    shown = {agreement.field: agreement for agreement in context.report.fields}
    assert set(shown) == {field for field, count in asked.items() if count}, shown
    for field, agreement in shown.items():
        assert agreement.outcomes.trials == asked[field], (field, agreement)
        assert agreement.values.trials <= agreement.outcomes.trials, (field, agreement)
        row = f"| {spec(field).label} | {agreement.outcomes.text()} | {agreement.values.text()} |"
        assert row in context.report_text, row


@then("it lists every field on which they disagree, with both answers")
def step_lists_disagreements(context):
    listed = {(item.case, item.field) for item in context.report.disagreements}
    assert listed == DISAGREEMENTS, listed
    for agreement in context.report.fields:
        differ = sum(item.field == agreement.field for item in context.report.disagreements)
        assert differ >= agreement.outcomes.trials - agreement.outcomes.successes
    for item in context.report.disagreements:
        assert (item.first.model, item.second.model) == ("jev-1.14.0", "clef-flash-2026-10-01")
        assert f"- {item.case}, {spec(item.field).label}" in context.report_text
        assert f"  - {item.first.text()}" in context.report_text
        assert f"  - {item.second.text()}" in context.report_text
