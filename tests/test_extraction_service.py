"""Extraction against the database: stored fields, review items, decisions, refusals, audit."""

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select

from cmbs import clock
from cmbs.access.service import PermissionRefused
from cmbs.audit.tables import AuditEntry
from cmbs.distribution.service import load_report
from cmbs.distribution.tables import ReportComment
from cmbs.domain import Period
from cmbs.extraction.fields import InvalidValue
from cmbs.extraction.questions import (
    WORDING,
    UnknownPromptVersion,
    sentence_name,
    support_name,
    value_name,
)
from cmbs.extraction.sentences import split
from cmbs.extraction.service import (
    AlreadyDecided,
    ChangedComment,
    EmptyComment,
    NoComment,
    NoSuchSentence,
    SourceText,
    UnknownDeal,
    UnknownItem,
    call_key,
    comment_view,
    decide_review,
    export_recording_files,
    extract_comments,
    extract_text,
    import_recording_files,
    open_review_items,
    read_text,
    render_summary,
    render_view,
    review_item,
)
from cmbs.extraction.tables import ExtractedField, Extraction, ReviewItem
from cmbs.model.client import ModelClient, NoRecording
from cmbs.model.policy import Classification, RestrictedText, UnpinnedModel
from cmbs.model.recordings import BadRecording, DbRecordings, MemoryRecordings
from cmbs.model.scripted import ScriptedProvider, choice_answer, respond
from cmbs.model.tables import ModelCall
from cmbs.model.types import Choice, ModelRequest, NoulAnswer
from cmbs.settings.service import InForce, in_force, store_version
from cmbs.testing.world import FIXTURE_CIK, FIXTURE_DEAL
from tests.conftest import FIXTURES, needs_db

pytestmark = needs_db

REPORT = FIXTURES / "distribution" / "fixture-trust-2026-f1_10d_2026-09.htm"
RECORDINGS = FIXTURES / "model"
SEPTEMBER = Period(2026, 9)
TODAY = dt.datetime(2026, 10, 1, 12, tzinfo=dt.UTC)
REVIEWED = dt.datetime(2026, 10, 2, 9, 30, tzinfo=dt.UTC)
ACCESSION = "0009900001-26-000009"


@pytest.fixture
def loaded(seeded):
    clock.set_now(TODAY)
    load_report(
        seeded,
        user="quinn",
        deal=FIXTURE_DEAL,
        accession=ACCESSION,
        cik=FIXTURE_CIK,
        filing_date=dt.date(2026, 9, 29),
        file_name=REPORT.name,
        content=REPORT.read_bytes(),
        period=SEPTEMBER,
    )
    seeded.commit()
    return seeded


@pytest.fixture
def recorded(loaded):
    import_recording_files(loaded, "quinn", RECORDINGS)
    loaded.commit()
    return loaded


def _comment(db) -> str:
    return db.scalars(select(ReportComment.comment).where(ReportComment.loan_id == "2")).one()


def _extract(db, *, user="quinn", provider=None, replay_only=True, **options):
    client = ModelClient(DbRecordings(db), provider=provider, replay_only=replay_only)
    options.setdefault("loan", "2")
    return extract_comments(
        db, user=user, deal=FIXTURE_DEAL, period=SEPTEMBER, client=client, **options
    )


def _scripted(db, field: str, answer: str, answer_p: float, support_p: float, sentence: int):
    sentences = split(_comment(db))

    def answer_request(request: ModelRequest):
        value = request.questions[value_name(field)]
        cited = request.questions[sentence_name(field)]
        assert isinstance(value, Choice) and isinstance(cited, Choice)
        return respond(
            request,
            {
                value_name(field): choice_answer(value, answer, answer_p),
                support_name(field): NoulAnswer(noul=support_p),
                sentence_name(field): choice_answer(cited, sentences[sentence - 1].text, 0.9),
            },
        )

    return ScriptedProvider(answer_request)


def _refusals(db) -> list[AuditEntry]:
    return list(db.scalars(select(AuditEntry).where(AuditEntry.outcome == "refused")))


def _count(db, table) -> int:
    return db.scalar(select(func.count()).select_from(table)) or 0


def test_a_replayed_extraction_fills_the_panel(recorded):
    summary = _extract(recorded, run_id=None)
    recorded.commit()
    [item] = summary.comments
    assert (item.loan, item.accession, item.replayed) == ("2", ACCESSION, True)
    assert item.model_call == call_key(_comment(recorded), model="jev-1.14.0", prompt_version=3)
    assert item.outcomes == {
        "transfer_reason": "auto-accept",
        "transfer_date": "auto-accept",
        "dscr": "auto-accept",
        "dscr_as_of": "auto-accept",
        "occupancy": "auto-accept",
        "occupancy_as_of": "auto-accept",
        "borrower_request": "auto-accept",
        "request_status": "auto-accept",
        "workout_strategy": "not stated",
    }
    view = comment_view(recorded, "pat", deal=FIXTURE_DEAL, loan="2", period=SEPTEMBER)
    assert view is not None
    assert (view.accession, view.filing_date, view.model, view.prompt_version) == (
        ACCESSION,
        dt.date(2026, 9, 29),
        "jev-1.14.0",
        3,
    )
    assert view.settings_version == 7 and len(view.sentences) == 4
    dscr = view.fields["dscr"]
    assert (dscr.value, dscr.display, dscr.typed, dscr.sentence) == (
        "0.71",
        "0.71x",
        Decimal("0.71"),
        2,
    )
    assert dscr.evidence == "As of 12/31/2024, DSCR NOI was 0.71x and occupancy was 61.4%."
    assert (dscr.answer_probability, dscr.support_probability) == (
        Decimal("0.97000"),
        Decimal("0.95000"),
    )
    assert view.fields["transfer_date"].typed == dt.date(2025, 3, 6)
    strategy = view.fields["workout_strategy"]
    assert (strategy.status, strategy.value, strategy.model_value) == (
        "not stated",
        None,
        "modification",
    )
    assert _count(recorded, ReviewItem) == 0
    [entry] = recorded.scalars(select(AuditEntry).where(AuditEntry.action == "extract comment"))
    assert (entry.user_name, entry.role, entry.subject, entry.subject_key) == (
        "quinn",
        "quant",
        "loan",
        f"{FIXTURE_DEAL}/2",
    )
    assert entry.detail is not None
    assert (entry.detail["accession"], entry.detail["replayed"]) == (ACCESSION, True)
    assert entry.detail["outcomes"]["workout_strategy"] == "not stated"


def test_a_comment_is_read_once_per_model_and_prompt_version(recorded):
    first = _extract(recorded)
    provider = ScriptedProvider()
    again = _extract(recorded, provider=provider, replay_only=False)
    assert again.comments[0].extraction_id == first.comments[0].extraction_id
    assert again.comments[0].outcomes == first.comments[0].outcomes
    assert provider.requests == []
    assert _count(recorded, Extraction) == 1


def test_a_changed_comment_is_not_silently_kept(recorded):
    _extract(recorded)
    stored = recorded.scalars(select(ReportComment).where(ReportComment.loan_id == "2")).one()
    stored.comment = stored.comment.replace("evaluating", "reviewing")
    recorded.flush()
    with pytest.raises(ChangedComment) as refused:
        _extract(recorded)
    assert str(refused.value) == (f"loan 2's comment in {ACCESSION} changed since it was extracted")


def test_replay_without_a_recording_stops_names_the_comment_and_is_audited(loaded):
    provider = ScriptedProvider()
    with pytest.raises(NoRecording) as refused:
        _extract(loaded, provider=provider)
    assert str(refused.value) == (
        f"no recorded answer for this input: loan 2 of {FIXTURE_DEAL}, September 2026 comment "
        f"({ACCESSION}) (model jev-1.14.0, prompt version 3)"
    )
    loaded.rollback()
    assert provider.requests == []
    [entry] = _refusals(loaded)
    assert (entry.action, entry.subject, entry.subject_key, entry.reason) == (
        "extract comment",
        "deal",
        FIXTURE_DEAL,
        str(refused.value),
    )
    assert _count(loaded, Extraction) == 0


def test_a_live_extraction_records_the_call(loaded):
    provider = ScriptedProvider()
    summary = _extract(loaded, provider=provider, replay_only=False)
    assert len(provider.requests) == 1 and not summary.comments[0].replayed
    call = loaded.get(ModelCall, summary.comments[0].model_call)
    assert call is not None
    assert (call.settings_version, call.classification, call.synthetic) == (7, "public", False)


def test_extraction_needs_a_role_that_runs_refreshes(recorded):
    with pytest.raises(PermissionRefused) as refused:
        _extract(recorded, user="pat")
    assert str(refused.value) == "pm may not run refreshes and backfills"


def test_an_unknown_deal_or_loan_is_refused(recorded):
    client = ModelClient(DbRecordings(recorded))
    with pytest.raises(UnknownDeal) as unknown:
        extract_comments(
            recorded, user="quinn", deal="No Such Trust", period=SEPTEMBER, client=client
        )
    assert str(unknown.value) == "no deal named 'No Such Trust'"
    with pytest.raises(NoComment) as missing:
        _extract(recorded, loan="9")
    assert str(missing.value) == (
        f"no special-servicing comment for loan 9 of {FIXTURE_DEAL} in September 2026"
    )


def test_a_month_without_comments_extracts_nothing(recorded):
    client = ModelClient(DbRecordings(recorded))
    summary = extract_comments(
        recorded, user="quinn", deal=FIXTURE_DEAL, period=Period(2026, 8), client=client
    )
    assert summary.comments == []
    assert (
        render_summary(summary) == f"{FIXTURE_DEAL}, August 2026: 0 comments, 0 sent to the model\n"
    )


def test_a_comment_filed_after_today_is_not_extracted(recorded):
    clock.set_now(dt.datetime(2026, 9, 28, tzinfo=dt.UTC))
    client = ModelClient(DbRecordings(recorded))
    summary = extract_comments(
        recorded, user="quinn", deal=FIXTURE_DEAL, period=SEPTEMBER, client=client
    )
    assert summary.comments == []


def _store_settings(db, version: int, **changes) -> None:
    store_version(
        db,
        version=version,
        values=in_force(db).values.model_copy(update=changes),
        proposed_by="quinn",
        approved_by="rae",
        approved_at=TODAY - dt.timedelta(hours=1),
    )


def test_a_floating_model_alias_stops_the_run_before_anything_is_read(recorded):
    _store_settings(recorded, 8, model="jev-latest")
    client = ModelClient(DbRecordings(recorded))
    with pytest.raises(UnpinnedModel) as refused:
        extract_comments(
            recorded, user="quinn", deal="No Such Trust", period=SEPTEMBER, client=client
        )
    assert str(refused.value) == "pin an exact model version, not jev-latest"


def test_a_prompt_version_this_code_does_not_ask_is_refused(recorded):
    _store_settings(recorded, 8, prompt_version=4)
    with pytest.raises(UnknownPromptVersion):
        _extract(recorded)


def test_restricted_text_is_refused_without_zero_data_retention_and_audited(seeded):
    clock.set_now(TODAY)
    provider = ScriptedProvider()
    client = ModelClient(DbRecordings(seeded), provider=provider, replay_only=False)
    source = SourceText("The tenant may leave.", Classification.RESTRICTED, "portal note")
    with pytest.raises(RestrictedText):
        extract_text(seeded, user="quinn", source=source, client=client)
    seeded.rollback()
    assert provider.requests == []
    [entry] = _refusals(seeded)
    assert (entry.subject, entry.subject_key) == ("text", "portal note")


def test_restricted_text_is_read_once_zero_data_retention_is_approved(seeded):
    clock.set_now(TODAY)
    _store_settings(seeded, 9, zero_data_retention=True)
    provider = ScriptedProvider()
    client = ModelClient(DbRecordings(seeded), provider=provider, replay_only=False)
    source = SourceText("The tenant may leave.", Classification.RESTRICTED, "portal note")
    reading = extract_text(seeded, user="quinn", source=source, client=client)
    assert len(provider.requests) == 1
    assert reading.settings_version == 9
    assert reading.answered.recording.classification is Classification.RESTRICTED
    assert [s.text for s in reading.sentences] == ["The tenant may leave."]
    assert reading.results["borrower_request"].decision.outcome.value == "not stated"


def test_an_empty_comment_is_refused():
    source = SourceText("  ", Classification.PUBLIC, "loan 2's comment")
    settings = InForce(version=7, values=in_force_defaults())
    with pytest.raises(EmptyComment) as refused:
        read_text(source, ModelClient(MemoryRecordings()), settings, now=TODAY)
    assert str(refused.value) == "loan 2's comment is empty"


def in_force_defaults():
    from cmbs.testing.world import BASELINE_SETTINGS

    return BASELINE_SETTINGS


@pytest.fixture
def waiting(loaded):
    """Loan 2's borrower request waiting for review: forbearance at an unsettled 0.55."""
    provider = _scripted(loaded, "borrower_request", "forbearance", 0.55, 0.9, 3)
    _extract(loaded, provider=provider, replay_only=False)
    loaded.commit()
    [item] = open_review_items(loaded, "alex")
    return item


def test_an_unsettled_answer_waits_for_review_with_its_question(waiting, loaded):
    assert (waiting.loan, waiting.deal, waiting.field, waiting.label) == (
        "2",
        FIXTURE_DEAL,
        "borrower_request",
        "borrower request",
    )
    assert (waiting.reason, waiting.question, waiting.probability) == (
        "unsettled answer",
        WORDING["borrower_request"].value,
        Decimal("0.55000"),
    )
    assert (waiting.model_value, waiting.status, waiting.created_at) == (
        "forbearance",
        "open",
        TODAY,
    )
    assert waiting.comment == _comment(loaded) and len(waiting.sentences) == 4
    assert review_item(loaded, "pat", waiting.id) == waiting
    view = comment_view(loaded, "pat", deal=FIXTURE_DEAL, loan="2", period=SEPTEMBER)
    assert view is not None
    request = view.fields["borrower_request"]
    assert (request.status, request.value, request.review_reason) == (
        "needs review",
        None,
        "unsettled answer",
    )


def test_unsupported_answers_wait_with_the_support_question(loaded):
    provider = _scripted(loaded, "borrower_request", "forbearance", 0.95, 0.2, 3)
    _extract(loaded, provider=provider, replay_only=False)
    [item] = open_review_items(loaded, "alex")
    assert (item.reason, item.question, item.probability) == (
        "no supporting sentence",
        WORDING["borrower_request"].support,
        Decimal("0.20000"),
    )


def test_a_reviewer_decides_the_value_attributed_and_audited(waiting, loaded):
    clock.set_now(REVIEWED)
    decided = decide_review(loaded, "alex", waiting.id, value="maturity extension", sentence=3)
    loaded.commit()
    assert (decided.status, decided.value, decided.sentence) == (
        "human-reviewed",
        "maturity extension",
        3,
    )
    assert (decided.decided_by, decided.decided_at) == ("alex", REVIEWED)
    assert decided.evidence == "The borrower has requested a 12-month maturity extension."
    assert decided.model_value == "forbearance"
    assert open_review_items(loaded, "alex") == []
    item = review_item(loaded, "alex", waiting.id)
    assert (item.status, item.decided_by, item.decision, item.decision_sentence) == (
        "decided",
        "alex",
        "maturity extension",
        3,
    )
    [entry] = loaded.scalars(select(AuditEntry).where(AuditEntry.action == "decide review item"))
    assert (entry.user_name, entry.role, entry.subject_key, entry.field) == (
        "alex",
        "analyst",
        f"{FIXTURE_DEAL}/2",
        "borrower request",
    )
    assert (entry.before, entry.after, entry.reason) == ("", "maturity extension", "sentence 3")
    assert entry.detail == {
        "review_item": waiting.id,
        "accession": ACCESSION,
        "period": "2026-09-01",
        "review_reason": "unsettled answer",
    }


def test_a_decision_is_visible_only_from_when_it_was_made(waiting, loaded):
    clock.set_now(REVIEWED)
    decide_review(loaded, "alex", waiting.id, value="forbearance", sentence=3)

    def request_on(day):
        view = comment_view(loaded, "pat", deal=FIXTURE_DEAL, loan="2", period=SEPTEMBER, as_of=day)
        return None if view is None else view.fields["borrower_request"]

    before = request_on(dt.date(2026, 10, 1))
    assert before is not None
    assert (before.status, before.value, before.decided_by, before.decided_at) == (
        "needs review",
        None,
        None,
        None,
    )
    after = request_on(dt.date(2026, 10, 2))
    assert after is not None and (after.status, after.value) == ("human-reviewed", "forbearance")
    assert request_on(dt.date(2026, 9, 28)) is None


def test_a_decided_item_cannot_be_decided_again(waiting, loaded):
    decide_review(loaded, "alex", waiting.id, value="forbearance", sentence=3)
    with pytest.raises(AlreadyDecided) as refused:
        decide_review(loaded, "morgan", waiting.id, value="forbearance", sentence=3)
    assert str(refused.value) == f"review item {waiting.id} was already decided by alex"


@pytest.mark.parametrize(
    ("user", "value", "sentence", "error", "message"),
    [
        ("pat", "forbearance", 3, PermissionRefused, "pm may not decide review items"),
        ("alex", "forbearance", 9, NoSuchSentence, "sentence 9 does not exist in this comment"),
        ("alex", "forbearance", 0, NoSuchSentence, "sentence 0 does not exist in this comment"),
        ("alex", "a gift", 3, InvalidValue, None),
    ],
)
def test_bad_decisions_are_refused_audited_and_leave_the_item_open(
    waiting, loaded, user, value, sentence, error, message
):
    with pytest.raises(error) as refused:
        decide_review(loaded, user, waiting.id, value=value, sentence=sentence)
    if message is not None:
        assert str(refused.value) == message
    loaded.rollback()
    assert [item.id for item in open_review_items(loaded, "alex")] == [waiting.id]
    assert _refusals(loaded)[-1].reason == str(refused.value)


def test_an_unknown_review_item_is_refused(recorded):
    with pytest.raises(UnknownItem) as refused:
        decide_review(recorded, "alex", 999, value="forbearance", sentence=1)
    assert str(refused.value) == "there is no review item 999"
    with pytest.raises(UnknownItem):
        review_item(recorded, "alex", 999)


def test_the_field_constraints_back_up_the_service(waiting, loaded):
    row = loaded.scalars(
        select(ExtractedField).where(ExtractedField.field == "borrower_request")
    ).one()
    row.status = "human-reviewed"
    with pytest.raises(Exception, match="reviewed_is_attributed|value_when_accepted"):
        loaded.flush()
    loaded.rollback()


def test_rendered_views(recorded):
    summary = _extract(recorded)
    assert render_summary(summary).splitlines() == [
        f"{FIXTURE_DEAL}, September 2026: 1 comment, 0 sent to the model",
        f"loan 2 ({ACCESSION}): transfer reason auto-accept, transfer date auto-accept, DSCR "
        "auto-accept, DSCR as-of date auto-accept, occupancy auto-accept, occupancy as-of date "
        "auto-accept, borrower request auto-accept, request status auto-accept, workout "
        "strategy not stated",
    ]
    view = comment_view(recorded, "pat", deal=FIXTURE_DEAL, loan="2", period=SEPTEMBER)
    assert view is not None
    lines = render_view(view).splitlines()
    assert lines[0] == (
        f"{FIXTURE_DEAL} loan 2, September 2026 ({ACCESSION}, filed 2026-09-29); jev-1.14.0, "
        "prompt version 3, settings version 7"
    )
    assert lines[1].startswith("  [1] The loan transferred")
    assert lines[5:] == [
        "transfer reason: imminent monetary default (auto-accepted, sentence 1)",
        "transfer date: 2025-03-06 (auto-accepted, sentence 1)",
        "DSCR: 0.71x (auto-accepted, sentence 2)",
        "DSCR as-of date: 2024-12-31 (auto-accepted, sentence 2)",
        "occupancy: 61.4% (auto-accepted, sentence 2)",
        "occupancy as-of date: 2024-12-31 (auto-accepted, sentence 2)",
        "borrower request: maturity extension (auto-accepted, sentence 3)",
        "request status: pending (auto-accepted, sentence 4)",
        "workout strategy: - (not stated)",
    ]


def test_render_view_shows_review_reasons_and_reviewers(waiting, loaded):
    view = comment_view(loaded, "pat", deal=FIXTURE_DEAL, loan="2", period=SEPTEMBER)
    assert view is not None
    assert "borrower request: - (needs review: unsettled answer)" in render_view(view)
    decide_review(loaded, "alex", waiting.id, value="forbearance", sentence=3)
    view = comment_view(loaded, "pat", deal=FIXTURE_DEAL, loan="2", period=SEPTEMBER)
    assert view is not None
    assert "borrower request: forbearance (human-reviewed, sentence 3 by alex)" in render_view(view)


def test_recording_files_need_the_right_roles(recorded, tmp_path):
    with pytest.raises(PermissionRefused):
        import_recording_files(recorded, "pat", RECORDINGS)
    with pytest.raises(PermissionRefused) as refused:
        export_recording_files(recorded, "olly", tmp_path)
    assert str(refused.value) == "operator may not export"
    [path] = export_recording_files(recorded, "quinn", tmp_path)
    assert json.loads(path.read_text())["synthetic"] is True


def test_a_bad_recording_file_is_refused_and_audited(seeded, tmp_path: Path):
    data = json.loads((RECORDINGS / "fixture-f1-loan-2-2026-09.json").read_text())
    data["prompt_version"] = 2
    (tmp_path / "bad.json").write_text(json.dumps(data))
    with pytest.raises(BadRecording):
        import_recording_files(seeded, "quinn", tmp_path)
    seeded.rollback()
    [entry] = _refusals(seeded)
    assert (entry.action, entry.subject) == ("import model recordings", "recordings")


def test_a_reading_stored_by_another_run_first_is_reused(recorded):
    from cmbs.access.roles import Role
    from cmbs.access.service import Actor
    from cmbs.extraction.service import _store

    first = _extract(recorded)
    comment = recorded.scalars(select(ReportComment).where(ReportComment.loan_id == "2")).one()
    settings = in_force(recorded)
    source = SourceText(comment.comment, Classification.PUBLIC, "loan 2")
    reading = read_text(source, ModelClient(DbRecordings(recorded)), settings, now=TODAY)
    again = _store(
        recorded,
        actor=Actor("quinn", Role.QUANT),
        deal=FIXTURE_DEAL,
        comment=comment,
        reading=reading,
        settings=settings,
        run_id=None,
    )
    assert again == first.comments[0]
    assert _count(recorded, Extraction) == 1


def test_reading_a_comment_view_of_an_unknown_deal_is_refused(seeded):
    with pytest.raises(UnknownDeal):
        comment_view(seeded, "pat", deal="No Such Trust", loan="2", period=SEPTEMBER)
