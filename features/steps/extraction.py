"""Steps for features/extraction/: model replay, comment extraction, abstention and review.

Loan 2's comment comes from the synthetic September 2026 10-D of "Fixture Trust 2026-F1"
(tests/fixtures/distribution/), loaded through the distribution loader as a quant would. Model
answers come from the synthetic recordings in tests/fixtures/model/, or, where a scenario needs
a request to reach a provider, from `ScriptedProvider`, which never touches the network.

In the abstention scenarios, the Given steps state the model's judgments for one field. A
judgment a scenario leaves out is taken as settled in favour of the answer: the answer at 0.95,
support at 0.95, and the first sentence cited. Every other field gets "no" answers, so it is not
stated. The clock is pinned so settings, extraction and review times are exact.
"""

import datetime as dt
from decimal import Decimal
from pathlib import Path

from behave import given, then, when  # pyright: ignore[reportAttributeAccessIssue]
from sqlalchemy import func, select

from cmbs import clock
from cmbs.distribution.service import comments_for_loan, load_report
from cmbs.domain import Period
from cmbs.extraction import fields
from cmbs.extraction.questions import WORDING, sentence_name, support_name, value_name
from cmbs.extraction.sentences import find, split
from cmbs.extraction.service import (
    TEN_D_CLASSIFICATION,
    SourceText,
    call_key,
    comment_view,
    decide_review,
    extract_comments,
    extract_text,
    open_review_items,
)
from cmbs.extraction.tables import Extraction
from cmbs.model.client import ModelClient
from cmbs.model.policy import Classification
from cmbs.model.recordings import DbRecordings
from cmbs.model.scripted import ScriptedProvider, choice_answer, respond
from cmbs.model.tables import ModelCall
from cmbs.model.types import Answer, Choice, ModelRequest, NoulAnswer
from cmbs.settings.service import in_force, store_version
from cmbs.testing.bdd import attempt, in_session
from cmbs.testing.world import FIXTURE_CIK, FIXTURE_DEAL
from tests.fixtures.model.synthetic import load as load_fixtures

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "tests" / "fixtures" / "distribution" / "fixture-trust-2026-f1_10d_2026-09.htm"
SEPTEMBER = Period(2026, 9)
OCTOBER = Period(2026, 10)
LOADER = "quinn"  # a quant loads reports and runs extraction
READER = "pat"
# The fixture deal's 10-Ds: (accession, filing date, file name).
FILINGS = {
    SEPTEMBER: ("0009900001-26-000009", dt.date(2026, 9, 29), REPORT.name),
    OCTOBER: (
        "0009900001-26-000010",
        dt.date(2026, 10, 29),
        "fixture-trust-2026-f1_10d_2026-10.htm",
    ),
}
# Today in these scenarios: after the September 10-D, before the October one.
SEPTEMBER_TODAY = dt.datetime(2026, 10, 1, 12, 0, tzinfo=dt.UTC)
OCTOBER_TODAY = dt.datetime(2026, 10, 30, 12, 0, tzinfo=dt.UTC)
REVIEW_TIME = dt.datetime(2026, 10, 2, 9, 30, tzinfo=dt.UTC)
WATCHLIST = (
    "The borrower reported that the largest tenant will vacate at lease expiry. "
    "The servicer has asked for a leasing plan."
)
SETTLED = 0.95


def _load(context, period: Period, content: bytes) -> None:
    accession, filed, name = FILINGS[period]
    in_session(
        lambda db: load_report(
            db,
            user=LOADER,
            deal=FIXTURE_DEAL,
            accession=accession,
            cik=FIXTURE_CIK,
            filing_date=filed,
            file_name=name,
            content=content,
            period=period,
        )
    )
    context.deal = FIXTURE_DEAL


def _september(context) -> str:
    """Load the September 10-D once; loan 2's comment as stored."""
    if not getattr(context, "september_loaded", False):
        clock.set_now(SEPTEMBER_TODAY)
        _load(context, SEPTEMBER, REPORT.read_bytes())
        context.september_loaded = True
        context.provider = ScriptedProvider()
    return _stored_comment(SEPTEMBER)


def _stored_comment(period: Period) -> str:
    comments = in_session(lambda db: comments_for_loan(db, deal=FIXTURE_DEAL, loan="2"))
    found = [item.comment for item in comments if item.period == period]
    assert len(found) == 1, f"loan 2 comments for {period}: {found}"
    return found[0]


def _import(context) -> None:
    context.imported = in_session(lambda db: load_fixtures(DbRecordings(db)))


def _provider(context) -> ScriptedProvider:
    provider = getattr(context, "provider", None)
    if provider is None:
        provider = context.provider = ScriptedProvider()
    return provider


def _extract(context, *, replay_only: bool, period: Period = SEPTEMBER) -> None:
    provider = _provider(context)

    def run(db):
        client = ModelClient(
            DbRecordings(db), provider=provider, replay_only=replay_only, allow_synthetic=True
        )
        return extract_comments(
            db, user=LOADER, deal=FIXTURE_DEAL, period=period, loan="2", client=client
        )

    context.summary = attempt(context, lambda: in_session(run))


def _store_settings(version: int, **changes) -> None:
    def store(db):
        current = in_force(db)
        store_version(
            db,
            version=version,
            values=current.values.model_copy(update=changes),
            proposed_by="quinn",
            approved_by="rae",
            approved_at=clock.now() - dt.timedelta(hours=1),
        )

    in_session(store)


# Model replay.


@given(
    'an answer is recorded for loan "{loan}"\'s {period} comment with model "{model}" '
    "and prompt version {version:d}"
)
def step_answer_recorded(context, loan, period, model, version):
    assert (loan, Period.parse(period)) == ("2", SEPTEMBER), "only loan 2's September is recorded"
    comment = _september(context)
    _import(context)
    key = call_key(comment, model=model, prompt_version=version)
    stored = in_session(lambda db: db.get(ModelCall, key))
    assert stored is not None, f"no recording for loan 2's comment under {model}, {version}"
    assert (stored.model, stored.prompt_version) == (model, version)
    context.recorded_key = key


@when('the comment is extracted again with model "{model}" and prompt version {version:d}')
def step_extracted_again(context, model, version):
    settings = in_session(in_force)
    assert (settings.values.model, settings.values.prompt_version) == (model, version)
    _extract(context, replay_only=False)


@then("the recorded answer is used")
def step_recorded_answer_used(context):
    assert context.refusal is None, f"refused: {context.refusal}"
    [extracted] = context.summary.comments
    assert extracted.replayed, "the model was asked again"
    assert extracted.model_call == context.recorded_key, extracted


@then("no request is sent to the model provider")
def step_no_request_sent(context):
    assert _provider(context).requests == [], f"{len(context.provider.requests)} requests sent"


@given("loan \"{loan}\"'s {period} comment differs from September's by one word")
def step_comment_differs(context, loan, period):
    assert (loan, Period.parse(period)) == ("2", OCTOBER)
    september = _september(context)
    clock.set_now(OCTOBER_TODAY)
    content = (
        REPORT.read_bytes()
        .replace(b"evaluating the request", b"reviewing the request")
        .replace(b"09/17/26", b"10/16/26")
    )
    _load(context, OCTOBER, content)
    october = _stored_comment(OCTOBER)
    changed = [
        pair for pair in zip(september.split(), october.split(), strict=True) if pair[0] != pair[1]
    ]
    assert changed == [("evaluating", "reviewing")], changed


@when("the {period} comment is extracted in replay mode")
def step_extracted_in_replay_mode(context, period):
    _extract(context, replay_only=True, period=Period.parse(period))


@then('the run stops with "{message}"')
def step_run_stops_with(context, message):
    assert context.refusal is not None, "the run did not stop"
    assert str(context.refusal).startswith(message), f"stopped with {str(context.refusal)!r}"


@given('the configured model is "{model}"')
def step_configured_model(context, model):
    clock.set_now(SEPTEMBER_TODAY)
    if in_session(in_force).values.model != model:
        _store_settings(8, model=model)
    assert in_session(in_force).values.model == model


@when("an extraction run starts")
def step_extraction_run_starts(context):
    _extract(context, replay_only=True)


@given('the provider\'s response says model "{model}" answered')
def step_provider_says_model(context, model):
    _september(context)
    context.provider = ScriptedProvider(lambda request: respond(request, {}, model=model))


@when("the answer is recorded")
def step_answer_is_recorded(context):
    _extract(context, replay_only=False)


@then("nothing is recorded")
def step_nothing_recorded(context):
    def count(db):
        calls = db.scalar(select(func.count()).select_from(ModelCall))
        extractions = db.scalar(select(func.count()).select_from(Extraction))
        return calls, extractions

    assert in_session(count) == (0, 0)


@given('loan "{loan}"\'s comment comes from a 10-D on EDGAR, classified "{classification}"')
def step_comment_from_10d(context, loan, classification):
    assert loan == "2"
    _september(context)
    assert Classification(classification) == TEN_D_CLASSIFICATION


@given('a watchlist comment comes from an investor portal, classified "{classification}"')
def step_watchlist_comment(context, classification):
    clock.set_now(SEPTEMBER_TODAY)
    context.source = SourceText(
        text=WATCHLIST,
        classification=Classification(classification),
        subject=f"investor portal watchlist comment on loan 2 of {FIXTURE_DEAL}",
    )


@given("the model provider is configured without zero data retention")
def step_without_zdr(context):
    assert not in_session(in_force).values.zero_data_retention


@given(
    "the model provider is configured with zero data retention, approved in settings "
    "version {version:d}"
)
def step_with_zdr(context, version):
    _store_settings(version, zero_data_retention=True)
    settings = in_session(in_force)
    assert (settings.version, settings.values.zero_data_retention) == (version, True)


@when("the comment is extracted with no recording available")
def step_extracted_without_recording(context):
    source = getattr(context, "source", None)
    if source is None:
        _extract(context, replay_only=False)
        return
    provider = _provider(context)

    def run(db):
        client = ModelClient(DbRecordings(db), provider=provider, replay_only=False)
        return extract_text(db, user=LOADER, source=source, client=client)

    context.reading = attempt(context, lambda: in_session(run))


@then("the request is sent to the model provider")
def step_request_sent(context):
    assert context.refusal is None, f"refused: {context.refusal}"
    assert len(_provider(context).requests) == 1


@then("the recording notes settings version {version:d}")
def step_recording_notes_version(context, version):
    key = context.reading.answered.recording.call_id
    stored = in_session(lambda db: db.get(ModelCall, key))
    assert stored is not None and stored.settings_version == version, stored


# Comment extraction.


@given('loan "{loan}" of "{deal}" has this {period} special-servicing comment:')
def step_has_comment(context, loan, deal, period):
    assert (loan, deal, Period.parse(period)) == ("2", FIXTURE_DEAL, SEPTEMBER)
    stored = _september(context)
    assert stored == " ".join(context.text.split()), stored
    context.comment = stored


@given("the comment is split into {count:d} numbered sentences")
def step_split_into(context, count):
    context.sentences = split(context.comment)
    assert [s.number for s in context.sentences] == list(range(1, count + 1)), context.sentences


@given('model answers are replayed from calls recorded with model "{model}"')
def step_replayed_from(context, model):
    _import(context)
    assert context.imported > 0
    models = in_session(lambda db: set(db.scalars(select(ModelCall.model))))
    assert models == {model}, models


@when("the comment is extracted")
def step_comment_extracted(context):
    _extract(context, replay_only=True)
    assert context.refusal is None, f"refused: {context.refusal}"
    _read_view(context)


def _read_view(context) -> None:
    context.view = in_session(
        lambda db: comment_view(db, READER, deal=FIXTURE_DEAL, loan="2", period=SEPTEMBER)
    )
    assert context.view is not None, "nothing extracted"


def _field(context, label: str):
    spec = fields.by_label(label)
    context.field = spec.name
    return context.view.fields[spec.name]


def _supported(view_field, value: str, sentence: int) -> None:
    assert (view_field.display, view_field.sentence) == (value, sentence), view_field
    assert view_field.status == "auto-accepted", view_field


@then('the {label} is "{value}", supported by sentence {sentence:d}')
def step_field_is(context, label, value, sentence):
    _supported(_field(context, label), value, sentence)


@then("the transfer date is {day}, supported by sentence {sentence:d}")
def step_transfer_date(context, day, sentence):
    view_field = _field(context, "transfer date")
    _supported(view_field, day, sentence)
    assert view_field.typed == dt.date.fromisoformat(day)


@then("the comment's DSCR is {ratio}x as of {day}, supported by sentence {sentence:d}")
def step_dscr(context, ratio, day, sentence):
    _supported(_field(context, "DSCR"), f"{ratio}x", sentence)
    assert context.view.fields["dscr"].typed == Decimal(ratio)
    _supported(_field(context, "DSCR as-of date"), day, sentence)


@then("the comment's occupancy is {percent}% as of {day}, supported by sentence {sentence:d}")
def step_occupancy(context, percent, day, sentence):
    _supported(_field(context, "occupancy"), f"{percent}%", sentence)
    assert context.view.fields["occupancy"].typed == Decimal(percent)
    _supported(_field(context, "occupancy as-of date"), day, sentence)


@given('an extraction cites the evidence "{evidence}"')
def step_cites_evidence(context, evidence):
    context.evidence = evidence


@when("the evidence is checked against the comment")
def step_evidence_checked(context):
    attempt(context, lambda: find(context.sentences, context.evidence))


@then('the extraction is rejected with the reason "{reason}"')
def step_rejected_with(context, reason):
    assert context.refusal is not None, "the evidence was accepted"
    assert str(context.refusal) == reason, str(context.refusal)


# Abstention and review.


def _script(context, label: str) -> dict:
    spec = fields.by_label(label)
    scripts = getattr(context, "scripts", None)
    if scripts is None:
        scripts = context.scripts = {}
    context.field = spec.name
    return scripts.setdefault(spec.name, {"answer_p": SETTLED, "support_p": SETTLED, "sentence": 1})


def _scripted_answers(request: ModelRequest, scripts: dict, sentences) -> dict[str, Answer]:
    answers: dict[str, Answer] = {}
    for field, script in scripts.items():
        value_question = request.questions[value_name(field)]
        sentence_question = request.questions[sentence_name(field)]
        assert isinstance(value_question, Choice) and isinstance(sentence_question, Choice)
        answers[value_name(field)] = choice_answer(
            value_question, script["answer"], script["answer_p"]
        )
        answers[support_name(field)] = NoulAnswer(noul=script["support_p"])
        cited = sentences[script["sentence"] - 1].text
        answers[sentence_name(field)] = choice_answer(sentence_question, cited, SETTLED)
    return answers


@given('the model gives "{answer}" a probability of {p} as loan "{loan}"\'s {label}')
def step_model_gives_answer(context, answer, p, loan, label):
    assert loan == "2"
    _september(context)
    _script(context, label).update(answer=answer, answer_p=float(p))


@given("it gives a probability of {p} that sentence {sentence:d} supports that answer")
def step_sentence_supports(context, p, sentence):
    context.scripts[context.field].update(support_p=float(p), sentence=sentence)


@given("it gives a probability of {p} that any sentence supports that answer")
def step_any_sentence_supports(context, p):
    context.scripts[context.field].update(support_p=float(p))


@when("the decision rules run")
def step_decision_rules_run(context):
    _run_scripted(context)
    assert context.refusal is None, f"refused: {context.refusal}"
    _read_view(context)


def _run_scripted(context) -> None:
    sentences = split(_stored_comment(SEPTEMBER))
    scripts = dict(context.scripts)
    context.provider = ScriptedProvider(
        lambda request: respond(request, _scripted_answers(request, scripts, sentences))
    )
    _extract(context, replay_only=False)


@then('the panel records the {label} "{value}"')
def step_panel_records(context, label, value):
    assert _field(context, label).display == value, context.view.fields[context.field]


@then('the field is marked "{status}"')
def step_field_marked(context, status):
    assert context.view.fields[context.field].status == status, context.view.fields[context.field]


@then('loan "{loan}"\'s {label} is left empty in the panel')
def step_left_empty(context, loan, label):
    assert loan == "2"
    view_field = _field(context, label)
    assert (view_field.value, view_field.status) == (None, "needs review"), view_field


def _open_item(context):
    items = in_session(lambda db: open_review_items(db, READER))
    mine = [item for item in items if item.loan == "2" and item.field == context.field]
    assert len(mine) == 1, items
    return mine[0]


@then("a review item shows the comment, the question and the probability {p}")
def step_review_item_shows(context, p):
    item = _open_item(context)
    assert item.comment == _stored_comment(SEPTEMBER)
    # The item asks the question whose probability was unsettled.
    wording = WORDING[context.field]
    expected = wording.value if item.reason == "unsettled answer" else wording.support
    assert item.question == expected, item.question
    assert item.probability == Decimal(p), item.probability


@then('loan "{loan}"\'s {label} goes to review with the reason "{reason}"')
def step_goes_to_review(context, loan, label, reason):
    assert loan == "2"
    view_field = _field(context, label)
    assert (view_field.status, view_field.review_reason) == ("needs review", reason), view_field
    assert _open_item(context).reason == reason


@given('a review item for loan "{loan}"\'s {label}')
def step_review_item_for(context, loan, label):
    assert loan == "2"
    _september(context)
    _script(context, label).update(answer=fields.spec(context.field).options[0], answer_p=0.55)
    _run_scripted(context)
    context.item = _open_item(context)


@when('"{user}" chooses "{value}" and cites sentence {sentence:d}')
def step_reviewer_chooses(context, user, value, sentence):
    clock.set_now(REVIEW_TIME)
    context.reviewer = user
    attempt(
        context,
        lambda: in_session(
            lambda db: decide_review(db, user, context.item.id, value=value, sentence=sentence)
        ),
    )
    assert context.refusal is None, f"refused: {context.refusal}"
    _read_view(context)


@then('the panel records the {label} "{value}" with the reviewer and the review time')
def step_panel_records_reviewer(context, label, value):
    view_field = _field(context, label)
    assert view_field.display == value, view_field
    assert (view_field.decided_by, view_field.decided_at) == (context.reviewer, REVIEW_TIME)
