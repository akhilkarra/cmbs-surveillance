"""Steps for features/governance/extraction_quality.feature.

The golden set is tests/fixtures/quality/golden_set.json (synthetic, see its README), imported
by "quinn". Predictions (what extraction found in each golden comment) are built from the counts
a scenario states, over the comments in key order: the first comments that state a borrower
request are found correctly, then comments that state none are given a request ("other") as
false finds. A scenario that states no predictions measures extraction that found every label.

The override scenario extracts loan 2's comment from the synthetic September 2026 10-D of
"Fixture Trust 2026-F1" with the recorded (synthetic) model answers, then replaces the panel
value as an analyst's override does: the value, its sentence and who decided it change, and
the model's answer stays stored.
"""

import datetime as dt
import json
from pathlib import Path

import parse
from behave import given, register_type, then, when  # pyright: ignore[reportAttributeAccessIssue]
from sqlalchemy import select

from cmbs import clock
from cmbs.audit.service import LOAN, loan_key, record
from cmbs.distribution.service import load_report
from cmbs.domain import Period
from cmbs.extraction import fields
from cmbs.extraction.service import extract_comments
from cmbs.extraction.tables import HUMAN_REVIEWED, ExtractedField, Extraction
from cmbs.model.client import ModelClient
from cmbs.model.recordings import DbRecordings
from cmbs.quality import measure
from cmbs.quality.files import PredictionSet, read_golden
from cmbs.quality.measure import Prediction
from cmbs.quality.render import report_text
from cmbs.quality.service import (
    SameAnalyst,
    build_report,
    candidates,
    confirm_candidate,
    extend_golden_set,
    golden_set,
    import_golden_set,
)
from cmbs.settings.service import in_force
from cmbs.testing.bdd import attempt, in_session
from cmbs.testing.world import FIXTURE_CIK, FIXTURE_DEAL
from tests.fixtures.model.synthetic import load as load_recordings

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "tests" / "fixtures" / "quality" / "golden_set.json"
REPORT_10D = ROOT / "tests" / "fixtures" / "distribution" / "fixture-trust-2026-f1_10d_2026-09.htm"
SEPTEMBER = Period(2026, 9)
QUANT = "quinn"
READER = "rae"  # a risk officer reads the report before approving a change
FIELD = "borrower_request"
TODAY = dt.datetime(2026, 10, 20, 12, 0, tzinfo=dt.UTC)


@parse.with_pattern(r"\d\.\d{2,3}")
def _rate(string: str) -> str:
    return string


register_type(QRate=_rate)


def _golden(context) -> list[measure.GoldenComment]:
    return [comment.golden() for comment in context.golden]


def _positives(context) -> list[str]:
    return [
        item.key
        for item in _golden(context)
        if measure.reference(item, FIELD) not in (None, measure.UNLABELLED, measure.DISPUTED)
    ]


def _negatives(context) -> list[str]:
    return [item.key for item in _golden(context) if measure.reference(item, FIELD) is None]


def _truth(context, key: str) -> str | None:
    item = next(item for item in _golden(context) if item.key == key)
    found = measure.reference(item, FIELD)
    assert found is None or isinstance(found, str), (key, found)
    return found


def _predictions(
    context,
    *,
    model: str,
    version: int,
    correct: int,
    false_finds: int = 0,
    automatic: int = 0,
    automatic_wrong: int = 0,
) -> PredictionSet:
    """Found correctly in the first `correct` comments stating a request; "other" found in the
    first `false_finds` stating none; every other comment's own label when `automatic` is set
    (the first `automatic` comments settled without review, the first `automatic_wrong` of
    those wrongly), or nothing found otherwise."""
    positives, negatives = _positives(context), _negatives(context)
    values: dict[str, str | None] = {}
    if automatic:
        values = {item.key: _truth(context, item.key) for item in _golden(context)}
    for key in positives:
        values[key] = _truth(context, key) if key in positives[:correct] else values.get(key)
    for key in negatives[:false_finds]:
        values[key] = "other"
    keys = [item.key for item in _golden(context)]
    settled = keys[:automatic]
    for key in settled[:automatic_wrong]:
        values[key] = "forbearance" if values.get(key) != "forbearance" else "other"
    return PredictionSet(
        model=model,
        prompt_version=version,
        predictions={key: {FIELD: Prediction(values.get(key), key in settled)} for key in keys},
    )


def _build(context, predictions: PredictionSet) -> None:
    clock.set_now(TODAY)
    context.report = in_session(lambda db: build_report(db, user=QUANT, predictions=predictions))


def _shown(measured: dict, rate: str, low: str, high: str) -> None:
    assert (measured["rate"], measured["low"], measured["high"]) == (rate, low, high), measured


# Background.


@given('a golden set of {count:d} special-servicing comments labelled by "{first}" and "{second}"')
def step_golden_set(context, count, first, second):
    context.golden = read_golden(json.loads(GOLDEN.read_text()))
    clock.set_now(TODAY - dt.timedelta(days=1))
    new = in_session(
        lambda db: import_golden_set(
            db, user=QUANT, comments=context.golden, source_name=GOLDEN.name
        )
    )
    assert new == count == len(in_session(golden_set)), (new, count)
    labellers = {name for comment in context.golden for name in comment.labels}
    assert labellers == {first, second}, labellers
    context.predictions = None


@given("{count:d} of them were labelled by both, who agreed on {agreed:d}")
def step_double_labelled(context, count, agreed):
    found = measure.agreement(_golden(context), [FIELD, "request_status"]).overall
    assert (found.trials, found.successes) == (count, agreed), found


@given("{count:d} of the comments contain a borrower request")
def step_contain_request(context, count):
    assert len(_positives(context)) == count, len(_positives(context))


# Precision and recall.


@given(
    'extraction with model "{model}" and prompt version {version:d} finds a borrower request '
    "in {found:d} comments, {right:d} of them correct"
)
def step_finds(context, model, version, found, right):
    context.predictions = _predictions(
        context, model=model, version=version, correct=right, false_finds=found - right
    )


@when("the quality report is built")
def step_report_built(context):
    predictions = context.predictions
    if predictions is None:
        settings = in_session(in_force).values
        everything = len(_positives(context))
        predictions = _predictions(
            context, model=settings.model, version=settings.prompt_version, correct=everything
        )
    context.predictions = predictions
    _build(context, predictions)


@then(
    "the borrower request field shows precision {rate:QRate} with a 95% interval of "
    "{low:QRate} to {high:QRate}"
)
def step_precision(context, rate, low, high):
    _shown(context.report.field(FIELD)["precision"], rate, low, high)


@then("recall {rate:QRate} with a 95% interval of {low:QRate} to {high:QRate}")
def step_recall_and(context, rate, low, high):
    _shown(context.report.field(FIELD)["recall"], rate, low, high)


@then("the report names the model, prompt version and settings version it measured")
def step_names_versions(context):
    report = context.report
    assert (report.model, report.prompt_version, report.settings_version) == (
        context.predictions.model,
        context.predictions.prompt_version,
        7,
    ), report
    heading = report_text(report).splitlines()[0]
    assert "model jev-1.14.0, prompt version 3, settings version 7" in heading, heading


# Auto-accepted values.


@given(
    "{automatic:d} of the {total:d} comments had their borrower request auto-accepted and "
    "{right:d} of those were correct"
)
def step_auto_accepted(context, automatic, total, right):
    assert total == len(context.golden)
    settings = in_session(in_force).values
    context.predictions = _predictions(
        context,
        model=settings.model,
        version=settings.prompt_version,
        correct=len(_positives(context)),
        automatic=automatic,
        automatic_wrong=automatic - right,
    )


@then(
    "auto-accepted accuracy for the borrower request shows {rate:QRate} with a 95% interval of "
    "{low:QRate} to {high:QRate}"
)
def step_auto_accuracy(context, rate, low, high):
    _shown(context.report.field(FIELD)["automatic"], rate, low, high)


@then(
    'the report says the abstention band is "{status}" because the lower bound is above '
    "{floor:QRate}"
)
def step_band(context, status, floor):
    measured = context.report.field(FIELD)
    assert measured["band"] == status == context.report.band, measured
    assert str(measure.AUTO_ACCEPT_FLOOR) == floor
    said = context.report.results["bands"][FIELD]
    assert said == (
        f'the abstention band is "{status}": auto-accepted borrower request accuracy '
        f"{measured['automatic']['rate']}, lower bound {measured['automatic']['low']} is above "
        f"{floor}"
    ), said


# Agreement.


@then("labeller agreement is {agreed:d} of {total:d} double-labelled comments")
def step_agreement(context, agreed, total):
    found = context.report.results["agreement"]
    assert (found["agreed"], found["double_labelled"]) == (agreed, total), found
    assert f"labeller agreement: {agreed} of {total} double-labelled comments" in report_text(
        context.report
    )


# Against the previous report.


@given(
    "the previous report, for prompt version {version:d}, showed borrower request recall of "
    "{recall:QRate}"
)
def step_previous_report(context, version, recall):
    settings = in_session(in_force).values
    positives = len(_positives(context))
    right = round(float(recall) * positives)
    _build(
        context,
        _predictions(context, model=settings.model, version=version, correct=right),
    )
    assert context.report.field(FIELD)["recall"]["rate"] == recall, context.report.results
    context.previous = context.report


@given("prompt version {version:d} finds {found:d} of the {total:d} borrower requests")
def step_new_prompt_finds(context, version, found, total):
    assert total == len(_positives(context))
    settings = in_session(in_force).values
    context.predictions = _predictions(
        context, model=settings.model, version=version, correct=found
    )


@when("the quality report for prompt version {version:d} is built")
def step_report_for_version(context, version):
    assert context.predictions.prompt_version == version
    _build(context, context.predictions)


@then("recall shows {rate:QRate} with a 95% interval of {low:QRate} to {high:QRate}")
def step_recall_shows(context, rate, low, high):
    _shown(context.report.field(FIELD)["recall"], rate, low, high)


@then('the report flags "{text}"')
def step_report_flags(context, text):
    assert text in context.report.flags, context.report.flags
    assert context.report.previous_id == context.previous.id


# Label candidates from overrides.


@given(
    '"{user}" overrode loan "{loan}"\'s {label} to "{value}" with sentence {sentence:d} as evidence'
)
def step_overrode(context, user, loan, label, value, sentence):
    assert loan == "2", "the fixture 10-D's comment is loan 2's"
    spec = fields.by_label(label)
    clock.set_now(dt.datetime(2026, 10, 1, 12, tzinfo=dt.UTC))
    in_session(
        lambda db: load_report(
            db,
            user=QUANT,
            deal=FIXTURE_DEAL,
            accession="0009900001-26-000009",
            cik=FIXTURE_CIK,
            filing_date=dt.date(2026, 9, 29),
            file_name=REPORT_10D.name,
            content=REPORT_10D.read_bytes(),
            period=SEPTEMBER,
        )
    )
    in_session(lambda db: load_recordings(DbRecordings(db)))

    def extract(db):
        client = ModelClient(DbRecordings(db), replay_only=True, allow_synthetic=True)
        return extract_comments(
            db, user=QUANT, deal=FIXTURE_DEAL, period=SEPTEMBER, loan=loan, client=client
        )

    in_session(extract)
    clock.set_now(dt.datetime(2026, 10, 5, 10, tzinfo=dt.UTC))

    def override(db):
        row = db.scalars(
            select(ExtractedField)
            .join(Extraction, Extraction.id == ExtractedField.extraction_id)
            .where(Extraction.loan_id == loan, ExtractedField.field == spec.name)
        ).one()
        before = fields.display(spec, row.value) if row.value is not None else ""
        row.value = fields.canonical(spec, value)
        row.sentence = sentence
        row.status = HUMAN_REVIEWED
        row.decided_by = user
        row.decided_at = clock.now()
        record(
            db,
            user=user,
            role="analyst",
            action="override value",
            subject=LOAN,
            subject_key=loan_key(FIXTURE_DEAL, loan),
            field=spec.label,
            before=before,
            after=fields.display(spec, row.value),
            reason=f"sentence {sentence}",
        )
        return row.model_value

    context.model_value = in_session(override)
    assert context.model_value not in (None, value), context.model_value
    context.overrider, context.field, context.label_value = user, spec.name, value


@when("the golden set is extended")
def step_extended(context):
    context.proposed = in_session(lambda db: extend_golden_set(db, user=QUANT))


@then('loan "{loan}"\'s comment is proposed as a new golden item with label "{value}"')
def step_proposed(context, loan, value):
    assert len(context.proposed) == 1, context.proposed
    (candidate,) = context.proposed
    assert (candidate.deal, candidate.loan, candidate.period) == (FIXTURE_DEAL, loan, SEPTEMBER)
    assert candidate.status == "proposed"
    assert candidate.labels == {context.overrider: {context.field: value}}, candidate.labels
    waiting = in_session(lambda db: candidates(db, reader=READER))
    assert [item.id for item in waiting] == [candidate.id]
    context.candidate = candidate


@then("it joins the golden set only after a second analyst confirms it")
def step_joins_after_confirmation(context):
    def keys():
        return {item.key for item in in_session(golden_set)}

    key = context.candidate.key
    assert key not in keys()
    attempt(
        context,
        lambda: in_session(
            lambda db: confirm_candidate(db, user=context.overrider, item_id=context.candidate.id)
        ),
    )
    assert isinstance(context.refusal, SameAnalyst), context.refusal
    assert str(context.refusal) == (
        f"{context.overrider} decided this label; a second analyst must confirm it"
    )
    assert key not in keys()
    confirmed = in_session(
        lambda db: confirm_candidate(db, user="morgan", item_id=context.candidate.id)
    )
    assert confirmed.status == "golden"
    assert confirmed.labels == {
        context.overrider: {context.field: context.label_value},
        "morgan": {context.field: context.label_value},
    }, confirmed.labels
    assert key in keys()
    assert len(keys()) == len(context.golden) + 1
