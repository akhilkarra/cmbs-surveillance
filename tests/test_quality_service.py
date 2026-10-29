"""The golden set and quality reports on a real database: permissions, audit, candidates."""

import datetime as dt
import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from cmbs import clock
from cmbs.access.roles import Role
from cmbs.access.service import PermissionRefused, ensure_user
from cmbs.audit.tables import AuditEntry
from cmbs.distribution.service import load_report
from cmbs.domain import Period
from cmbs.extraction.service import decide_review, extract_comments, open_review_items
from cmbs.model.client import ModelClient
from cmbs.model.recordings import DbRecordings
from cmbs.model.scripted import ScriptedProvider, choice_answer, respond
from cmbs.model.types import Choice, ModelRequest, NoulAnswer
from cmbs.quality import service
from cmbs.quality.files import LabelledComment, PredictionSet
from cmbs.quality.measure import Prediction
from cmbs.quality.tables import GoldenItem, QualityReport
from cmbs.testing.world import FIXTURE_CIK, FIXTURE_DEAL
from tests.conftest import needs_db

pytestmark = needs_db

BR = "borrower_request"
REPORT_10D = (
    Path(__file__).parent / "fixtures" / "distribution" / "fixture-trust-2026-f1_10d_2026-09.htm"
)
SEPTEMBER = Period(2026, 9)
NOW = dt.datetime(2026, 10, 20, 12, tzinfo=dt.UTC)


def comments() -> list[LabelledComment]:
    return [
        LabelledComment("G1", "The borrower asked for forbearance.", {"alex": {BR: "forbearance"}}),
        LabelledComment(
            "G2", "A receiver was appointed.", {"alex": {BR: None}, "morgan": {BR: None}}
        ),
    ]


def predictions(version: int = 3, found: str | None = "forbearance") -> PredictionSet:
    return PredictionSet(
        "jev-1.14.0",
        version,
        {"G1": {BR: Prediction(found, True, 7)}, "G2": {BR: Prediction(None, True, 7)}},
    )


def audited(db: Session, action: str) -> list[AuditEntry]:
    return list(
        db.scalars(
            select(AuditEntry)
            .where(AuditEntry.action == action, AuditEntry.outcome == "done")
            .order_by(AuditEntry.id)
        )
    )


def test_import_stores_the_golden_set_and_is_idempotent(seeded):
    clock.set_now(NOW)
    assert (
        service.import_golden_set(seeded, user="quinn", comments=comments(), source_name="g.json")
        == 2
    )
    assert (
        service.import_golden_set(seeded, user="quinn", comments=comments(), source_name="g.json")
        == 0
    )
    seeded.commit()
    golden = service.golden_set(seeded)
    assert [(g.key, g.labels) for g in golden] == [
        ("G1", {"alex": {BR: "forbearance"}}),
        ("G2", {"alex": {BR: None}, "morgan": {BR: None}}),
    ]
    entries = audited(seeded, "import golden set")
    assert [(e.user_name, e.subject_key, e.detail) for e in entries] == [
        ("quinn", "g.json", {"items": 2, "new": 2}),
        ("quinn", "g.json", {"items": 2, "new": 0}),
    ]


def test_import_never_overwrites_a_label(seeded):
    service.import_golden_set(seeded, user="quinn", comments=comments(), source_name="g.json")
    seeded.commit()
    changed = [
        LabelledComment("G1", "The borrower asked for forbearance.", {"alex": {BR: "other"}})
    ]
    with pytest.raises(
        service.ChangedLabel,
        match="^golden item G1 is already stored with other labels; labels are never overwritten$",
    ):
        service.import_golden_set(seeded, user="quinn", comments=changed, source_name="h.json")
    seeded.rollback()
    refusal = seeded.scalars(select(AuditEntry).where(AuditEntry.outcome == "refused")).one()
    assert (refusal.action, refusal.subject_key) == ("import golden set", "h.json")


def test_labellers_must_be_analysts_and_importers_quants(seeded):
    bad = [LabelledComment("G9", "c", {"pat": {BR: None}})]
    with pytest.raises(
        service.NotAnAnalyst, match="^pat is not an analyst, so cannot label the golden set$"
    ):
        service.import_golden_set(seeded, user="quinn", comments=bad, source_name="x")
    with pytest.raises(PermissionRefused, match="^analyst may not propose settings$"):
        service.import_golden_set(seeded, user="alex", comments=comments(), source_name="x")


def test_build_report_stores_measures_and_compares_with_the_previous(seeded):
    clock.set_now(NOW)
    service.import_golden_set(seeded, user="quinn", comments=comments(), source_name="g.json")
    first = service.build_report(seeded, user="quinn", predictions=predictions())
    second = service.build_report(seeded, user="quinn", predictions=predictions(4, None))
    seeded.commit()
    assert (first.previous_id, first.flags, first.items, first.settings_version) == (None, [], 2, 7)
    assert first.field(BR)["recall"] == {
        "successes": 1,
        "trials": 1,
        "rate": "1.000",
        "low": "0.207",
        "high": "1.000",
    }
    assert second.previous_id == first.id
    assert second.flags == [
        "borrower request recall fell from 1.000 to 0.000",
        "borrower request auto-accepted accuracy fell from 1.000 to 0.500",
    ]
    assert second.results["agreement"]["double_labelled"] == 1
    stored = seeded.get(QualityReport, second.id)
    assert stored is not None and stored.predictions == predictions(4, None).as_json()
    entries = audited(seeded, "build quality report")
    assert entries[1].detail == {
        "model": "jev-1.14.0",
        "prompt_version": 4,
        "settings_versions": [7],
        "items": 2,
        "flags": second.flags,
    }
    assert service.report(seeded, reader="rae").id == second.id
    assert service.report(seeded, reader="rae", report_id=first.id).prompt_version == 3


def test_reports_need_a_golden_set_and_matching_predictions(seeded):
    with pytest.raises(service.EmptyGoldenSet, match="^the golden set is empty$"):
        service.build_report(seeded, user="quinn", predictions=predictions())
    seeded.rollback()
    service.import_golden_set(seeded, user="quinn", comments=comments(), source_name="g.json")
    seeded.commit()
    extra = PredictionSet(
        "m", 3, {**predictions().predictions, "G7": {BR: Prediction(None, False, 7)}}
    )
    with pytest.raises(
        service.PredictionsDoNotMatch, match="^predictions for comments not in the golden set: G7$"
    ):
        service.build_report(seeded, user="quinn", predictions=extra)
    short = PredictionSet("m", 3, {"G1": {BR: Prediction(None, False, 7)}})
    with pytest.raises(
        service.PredictionsDoNotMatch, match="^no prediction for G2 borrower request$"
    ):
        service.build_report(seeded, user="quinn", predictions=short)
    with pytest.raises(service.PredictionsDoNotMatch, match="^the predictions name no field$"):
        service.build_report(seeded, user="quinn", predictions=PredictionSet("m", 3, {}))
    with pytest.raises(PermissionRefused, match="^risk may not propose settings$"):
        service.build_report(seeded, user="rae", predictions=predictions())


def test_many_missing_predictions_are_summarised(seeded):
    many = [LabelledComment(f"G{n}", "c", {"alex": {BR: None}}) for n in range(1, 9)]
    service.import_golden_set(seeded, user="quinn", comments=many, source_name="g.json")
    with pytest.raises(service.PredictionsDoNotMatch) as refused:
        service.build_report(
            seeded,
            user="quinn",
            predictions=PredictionSet("m", 3, {"G1": {BR: Prediction(None, False, 7)}}),
        )
    assert str(refused.value) == (
        "no prediction for G2 borrower request, G3 borrower request, G4 borrower request, "
        "G5 borrower request, G6 borrower request and 2 more"
    )


def test_no_report_yet(seeded):
    with pytest.raises(service.UnknownReport, match="^there is no quality report$"):
        service.report(seeded, reader="rae")
    with pytest.raises(service.UnknownReport, match="^there is no quality report 9$"):
        service.report(seeded, reader="rae", report_id=9)


def decided_comment(db: Session) -> None:
    """Loan 2's September comment extracted, with its transfer reason decided by alex."""
    clock.set_now(dt.datetime(2026, 10, 1, 12, tzinfo=dt.UTC))
    load_report(
        db,
        user="quinn",
        deal=FIXTURE_DEAL,
        accession="0009900001-26-000009",
        cik=FIXTURE_CIK,
        filing_date=dt.date(2026, 9, 29),
        file_name=REPORT_10D.name,
        content=REPORT_10D.read_bytes(),
        period=SEPTEMBER,
    )

    # The transfer reason's answer is unsettled, so it goes to review.
    def answer(request: ModelRequest):
        value = request.questions["transfer_reason.value"]
        cited = request.questions["transfer_reason.sentence"]
        assert isinstance(value, Choice) and isinstance(cited, Choice)
        first = next(iter(cited.criteria))
        return respond(
            request,
            {
                "transfer_reason.value": choice_answer(value, "imminent monetary default", 0.55),
                "transfer_reason.support": NoulAnswer(noul=0.95),
                "transfer_reason.sentence": choice_answer(cited, first, 0.95),
            },
        )

    client = ModelClient(
        DbRecordings(db), provider=ScriptedProvider(answer), replay_only=False, allow_synthetic=True
    )
    extract_comments(db, user="quinn", deal=FIXTURE_DEAL, period=SEPTEMBER, loan="2", client=client)
    items = [i for i in open_review_items(db, "alex") if i.field == "transfer_reason"]
    clock.set_now(dt.datetime(2026, 10, 2, 9, tzinfo=dt.UTC))
    decide_review(db, "alex", items[0].id, value="imminent monetary default", sentence=1)
    db.commit()


def test_review_decisions_become_candidates_that_need_a_second_analyst(seeded):
    decided_comment(seeded)
    clock.set_now(NOW)
    extended = service.extend_golden_set(seeded, user="quinn")
    seeded.commit()
    (candidate,) = extended.proposed
    assert extended.skipped == 0
    assert candidate.key == f"{FIXTURE_DEAL}/2/2026-09/transfer_reason"
    assert (candidate.status, candidate.labels, candidate.sentences) == (
        "proposed",
        {"alex": {"transfer_reason": "imminent monetary default"}},
        {"transfer_reason": 1},
    )
    assert candidate.comment.startswith("The loan transferred to Special Servicing")
    assert service.extend_golden_set(seeded, user="quinn") == service.Extended([], 1)
    assert [c.id for c in service.candidates(seeded, reader="pat")] == [candidate.id]

    with pytest.raises(PermissionRefused, match="^quant may not decide review items$"):
        service.confirm_candidate(seeded, user="quinn", item_id=candidate.id)
    seeded.rollback()
    with pytest.raises(
        service.SameAnalyst, match="^alex decided this label; a second analyst must confirm it$"
    ):
        service.confirm_candidate(seeded, user="alex", item_id=candidate.id)
    seeded.rollback()
    confirmed = service.confirm_candidate(seeded, user="morgan", item_id=candidate.id)
    seeded.commit()
    assert confirmed.status == "golden"
    assert confirmed.decided_by == "morgan"
    assert confirmed.labels["morgan"] == {"transfer_reason": "imminent monetary default"}
    assert candidate.key in {g.key for g in service.golden_set(seeded)}
    assert service.candidates(seeded, reader="pat") == []
    with pytest.raises(
        service.NotProposed, match=f"^golden item {candidate.id} is already golden$"
    ):
        service.confirm_candidate(seeded, user="morgan", item_id=candidate.id)
    seeded.rollback()
    entry = audited(seeded, "confirm golden item")[0]
    assert (entry.user_name, entry.before, entry.after) == ("morgan", "proposed", "golden")


def test_a_second_analyst_may_reject_a_candidate(seeded):
    decided_comment(seeded)
    ensure_user(seeded, "ana", {Role.ANALYST})
    (candidate,) = service.extend_golden_set(seeded, user="quinn").proposed
    seeded.commit()
    with pytest.raises(service.MissingReason, match="^a rejection needs a reason$"):
        service.reject_candidate(seeded, user="ana", item_id=candidate.id, reason="  ")
    seeded.rollback()
    rejected = service.reject_candidate(
        seeded, user="ana", item_id=candidate.id, reason=" the  comment says monetary default "
    )
    seeded.commit()
    assert (rejected.status, rejected.decided_by, rejected.reason) == (
        "rejected",
        "ana",
        "the comment says monetary default",
    )
    assert candidate.key not in {g.key for g in service.golden_set(seeded)}
    # Never proposed again.
    assert service.extend_golden_set(seeded, user="quinn") == service.Extended([], 1)
    entry = audited(seeded, "reject golden item")[0]
    assert (entry.reason, entry.after) == ("the comment says monetary default", "rejected")


def test_only_panel_candidates_can_be_confirmed(seeded):
    service.import_golden_set(seeded, user="quinn", comments=comments(), source_name="g.json")
    seeded.commit()
    imported = seeded.scalars(select(GoldenItem).where(GoldenItem.key == "G1")).one()
    with pytest.raises(
        service.UnknownCandidate, match=f"^there is no golden item candidate {imported.id}$"
    ):
        service.confirm_candidate(seeded, user="morgan", item_id=imported.id)
    seeded.rollback()
    with pytest.raises(service.UnknownCandidate, match="^there is no golden item candidate 999$"):
        service.reject_candidate(seeded, user="morgan", item_id=999, reason="r")


def test_the_fixture_golden_set_loads(seeded):
    from cmbs.quality.files import read_golden

    data = json.loads(
        (Path(__file__).parent / "fixtures" / "quality" / "golden_set.json").read_text()
    )
    assert (
        service.import_golden_set(
            seeded, user="morgan", comments=read_golden(data), source_name="golden_set.json"
        )
        == 100
    )


def test_a_comment_extracted_and_decided_again_is_one_candidate_with_the_latest_decision(seeded):
    """Another model reads the same comment and alex decides its transfer reason again: one
    candidate, labelled with the later decision, and no second label for alex."""
    from cmbs.extraction.tables import ExtractedField, Extraction

    decided_comment(seeded)
    first = seeded.scalars(select(Extraction)).one()
    again = Extraction(
        **{
            column: getattr(first, column)
            for column in (
                "deal_id",
                "loan_id",
                "period",
                "accession",
                "source_table",
                "comment",
                "sentences",
                "prompt_version",
                "settings_version",
                "model_call",
                "extracted_by",
            )
        },
        model="jev-1.15.0",
        extracted_at=dt.datetime(2026, 10, 3, tzinfo=dt.UTC),
    )
    seeded.add(again)
    seeded.flush()
    seeded.add(
        ExtractedField(
            extraction_id=again.id,
            field="transfer_reason",
            outcome="review",
            status="human-reviewed",
            value="monetary default",
            sentence=1,
            decided_by="alex",
            decided_at=dt.datetime(2026, 10, 4, tzinfo=dt.UTC),
        )
    )
    seeded.commit()
    clock.set_now(NOW)
    extended = service.extend_golden_set(seeded, user="quinn")
    seeded.commit()
    (candidate,) = extended.proposed
    assert extended.skipped == 1  # the earlier decision
    assert candidate.labels == {"alex": {"transfer_reason": "monetary default"}}
    assert candidate.accession == first.accession


def test_a_confirmed_candidate_does_not_count_toward_labeller_agreement(seeded):
    from cmbs.quality.files import read_golden

    golden = Path(__file__).parent / "fixtures" / "quality" / "golden_set.json"
    comments = read_golden(json.loads(golden.read_text()))
    service.import_golden_set(seeded, user="quinn", comments=comments, source_name="g.json")
    decided_comment(seeded)
    clock.set_now(NOW)
    (candidate,) = service.extend_golden_set(seeded, user="quinn").proposed
    service.confirm_candidate(seeded, user="morgan", item_id=candidate.id)
    seeded.commit()
    keys = [g.key for g in service.golden_set(seeded)]
    assert len(keys) == 101
    found = PredictionSet("jev-1.14.0", 3, {key: {BR: Prediction(None, False, 7)} for key in keys})
    report = service.build_report(seeded, user="quinn", predictions=found)
    agreement = report.results["agreement"]
    assert (agreement["agreed"], agreement["double_labelled"]) == (19, 20)


def test_reports_name_the_settings_versions_that_produced_the_predictions(seeded):
    from cmbs.quality.render import report_text
    from cmbs.settings.service import store_version
    from cmbs.testing.world import BASELINE_SETTINGS

    clock.set_now(NOW)
    store_version(
        seeded,
        version=8,
        values=BASELINE_SETTINGS,
        proposed_by="quinn",
        approved_by="rae",
        approved_at=NOW - dt.timedelta(days=1),
    )
    service.import_golden_set(seeded, user="quinn", comments=comments(), source_name="g.json")
    mixed = PredictionSet(
        "jev-1.14.0",
        3,
        {"G1": {BR: Prediction("forbearance", True, 7)}, "G2": {BR: Prediction(None, True, 8)}},
    )
    report = service.build_report(seeded, user="quinn", predictions=mixed)
    assert (report.settings_version, report.settings_versions) == (None, [7, 8])
    assert "prompt version 3, settings versions 7 and 8;" in report_text(report)
    # Settings version 8 is in force, but the predictions were made under 7: 7 is named.
    single = service.build_report(seeded, user="quinn", predictions=predictions())
    assert (single.settings_version, single.settings_versions) == (7, [7])


def test_predictions_must_name_known_settings_versions(seeded):
    service.import_golden_set(seeded, user="quinn", comments=comments(), source_name="g.json")
    seeded.commit()
    unsaid = PredictionSet(
        "m", 3, {"G1": {BR: Prediction(None, False)}, "G2": {BR: Prediction(None, False, 7)}}
    )
    with pytest.raises(
        service.PredictionsDoNotMatch,
        match="^every prediction must name the settings version that produced it$",
    ):
        service.build_report(seeded, user="quinn", predictions=unsaid)
    seeded.rollback()
    unknown = PredictionSet(
        "m", 3, {"G1": {BR: Prediction(None, False, 9)}, "G2": {BR: Prediction(None, False, 7)}}
    )
    with pytest.raises(service.UnknownSettingsVersion, match="^there is no settings version 9$"):
        service.build_report(seeded, user="quinn", predictions=unknown)
