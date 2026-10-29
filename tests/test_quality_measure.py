"""Measuring predictions against golden labels, reading the files, and the report text. Pure."""

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

import pytest

from cmbs.domain import Period
from cmbs.quality import measure
from cmbs.quality.files import BadFile, PredictionSet, read_golden, read_predictions
from cmbs.quality.measure import (
    DISPUTED,
    UNLABELLED,
    GoldenComment,
    Prediction,
    agreement,
    band_text,
    labelled_fields,
    measure_field,
    missing_predictions,
    reference,
    worse,
)
from cmbs.quality.render import candidate_text, measure_text, report_text, versions_text
from cmbs.quality.service import CandidateView, ReportView, overall_band, results_of
from cmbs.quality.stats import Proportion

GOLDEN = Path(__file__).parent / "fixtures" / "quality" / "golden_set.json"
BR = "borrower_request"
RS = "request_status"


def item(key: str, **labellers: dict[str, str | None]) -> GoldenComment:
    return GoldenComment(key, labellers)


def test_reference_is_the_agreed_label():
    assert reference(item("1", alex={BR: "forbearance"}), BR) == "forbearance"
    assert reference(item("1", alex={BR: None}, morgan={BR: None}), BR) is None
    assert reference(item("1", alex={BR: "forbearance"}, morgan={BR: "other"}), BR) is DISPUTED
    assert reference(item("1", alex={RS: "pending"}), BR) is UNLABELLED


def test_measure_field_counts_each_kind_of_answer():
    items = [
        item("hit", alex={BR: "forbearance"}),
        item("wrong", alex={BR: "forbearance"}),
        item("miss", alex={BR: "loan modification"}),
        item("false", alex={BR: None}),
        item("quiet", alex={BR: None}),
        item("disputed", alex={BR: "other"}, morgan={BR: None}),
        item("unlabelled", alex={RS: "pending"}),
    ]
    predictions = {
        "hit": {BR: Prediction("forbearance", True)},
        "wrong": {BR: Prediction("other", True)},
        "miss": {BR: Prediction(None, True)},
        "false": {BR: Prediction("other", False)},
        "quiet": {BR: Prediction(None, True)},
    }
    quality = measure_field(items, predictions, BR)
    assert (quality.compared, quality.disputed) == (5, 1)
    assert quality.precision == Proportion(1, 3)  # found in hit, wrong, false
    assert quality.recall == Proportion(1, 3)  # stated in hit, wrong, miss
    assert quality.automatic == Proportion(2, 4)  # hit and quiet right; wrong and miss not
    assert quality.label == "borrower request"
    assert quality.band == measure.NOT_HOLDING


def test_the_band_holds_only_when_the_lower_bound_is_above_the_floor():
    def band(right: int, total: int) -> str:
        items = [item(str(n), alex={BR: None}) for n in range(total)]
        predictions = {
            str(n): {BR: Prediction(None if n < right else "other", True)} for n in range(total)
        }
        return measure_field(items, predictions, BR).band

    assert band(58, 60) == measure.HOLDING  # lower bound 0.886
    assert band(56, 60) == measure.NOT_HOLDING  # lower bound 0.841
    assert band(0, 0) == measure.NOT_MEASURED


def test_band_text():
    def quality(automatic: Proportion) -> measure.FieldQuality:
        return measure.FieldQuality(BR, 60, 0, Proportion(0, 0), Proportion(0, 0), automatic)

    assert band_text(quality(Proportion(58, 60))) == (
        'the abstention band is "holding": auto-accepted borrower request accuracy 0.967, '
        "lower bound 0.886 is above 0.85"
    )
    assert band_text(quality(Proportion(50, 60))) == (
        'the abstention band is "not holding": auto-accepted borrower request accuracy 0.833, '
        "lower bound 0.720 is not above 0.85"
    )
    assert band_text(quality(Proportion(0, 0))) == (
        'the abstention band is "not measured": no borrower request was auto-accepted'
    )


def test_agreement_counts_comments_agreeing_on_every_shared_field():
    items = [
        item(
            "a", alex={BR: "forbearance", RS: "pending"}, morgan={BR: "forbearance", RS: "pending"}
        ),
        item("b", alex={BR: None, RS: None}, morgan={BR: None, RS: None}),
        item("c", alex={BR: "other", RS: "granted"}, morgan={BR: "other", RS: "denied"}),
        item("d", alex={BR: None}, morgan={RS: None}),  # nothing shared: agrees vacuously
        item("single", alex={BR: "other"}),
    ]
    found = agreement(items, [BR, RS])
    assert found.labellers == ("alex", "morgan")
    assert found.overall == Proportion(3, 4)
    assert found.kappa[BR] == 1
    # request status pairs: (pending, pending), (None, None), (granted, denied)
    # observed 2/3; chance (1/3)(1/3) + (1/3)(1/3) = 2/9; kappa = (2/3 - 2/9)/(7/9) = 4/7
    kappa = found.kappa[RS]
    assert kappa is not None
    assert measure.rounded(kappa) == Decimal("0.571")


def test_labelled_fields_and_missing_predictions():
    items = [item("1", alex={RS: None, BR: None}), item("2", morgan={BR: "other"})]
    assert labelled_fields(items) == [BR, RS]
    assert missing_predictions(items, {"1": {BR: Prediction(None, False)}}, [BR]) == [
        "2 borrower request"
    ]


def previous_results(recall: str | None, precision: str = "0.905") -> dict:
    def shown(rate):
        return {"rate": rate}

    return {BR: {"precision": shown(precision), "recall": shown(recall), "automatic": shown(None)}}


def test_worse_flags_each_measure_that_fell():
    items = [item(str(n), alex={BR: "forbearance" if n < 40 else None}) for n in range(100)]
    predictions = {
        str(n): {BR: Prediction("forbearance" if n < 32 else None, False)} for n in range(100)
    }
    current = [measure_field(items, predictions, BR)]
    assert worse(previous_results("0.950"), current) == [
        "borrower request recall fell from 0.950 to 0.800"
    ]
    assert worse(previous_results("0.800"), current) == []  # equal is not worse
    assert worse(previous_results("0.700"), current) == []  # better is not worse
    assert worse(previous_results(None), current) == []  # not measured before
    assert worse({}, current) == []  # field not measured before
    assert worse(previous_results("0.950", precision="1.000"), current) == [
        "borrower request recall fell from 0.950 to 0.800"
    ]


def test_the_fixture_golden_set_has_the_shape_the_feature_states():
    comments = read_golden(json.loads(GOLDEN.read_text()))
    golden = [comment.golden() for comment in comments]
    assert len(golden) == 100
    stated = [g for g in golden if reference(g, BR) not in (None, UNLABELLED, DISPUTED)]
    assert len(stated) == 40
    assert agreement(golden, labelled_fields(golden)).overall == Proportion(19, 20)
    assert {name for c in comments for name in c.labels} == {"alex", "morgan"}


def test_read_golden_refuses_what_it_cannot_read():
    def refused(data, message):
        with pytest.raises(BadFile, match=f"^{message}$"):
            read_golden(data)

    refused([], "the labels file must be an object")
    refused({"items": []}, "the labels file has no items")
    refused({"items": ["x"]}, "item 1 must be an object")
    refused({"items": [{"key": " ", "comment": "c"}]}, "item 1's key must be non-empty text")
    refused({"items": [{"key": "G1"}]}, "G1's comment must be non-empty text")
    refused({"items": [{"key": "G1", "comment": "c", "labels": {}}]}, "G1 has no labels")
    refused(
        {"items": [{"key": "G1", "comment": "c", "labels": {"alex": {"nope": None}}}]},
        "G1, alex: there is no field 'nope'",
    )
    refused(
        {"items": [{"key": "G1", "comment": "c", "labels": {"alex": {BR: 3}}}]},
        "G1, alex: borrower_request must be text or null",
    )
    refused(
        {"items": [{"key": "G1", "comment": "c", "labels": {"alex": {BR: "a loan"}}}]},
        "G1, alex: 'a loan' is not a borrower request; choose one of: maturity extension, "
        "forbearance, loan modification, discounted payoff, loan assumption, collateral "
        "release, other",
    )
    good = {"key": "G1", "comment": "c", "labels": {"alex": {BR: None}}}
    refused({"items": [good, good]}, "golden item G1 appears twice")


def test_read_golden_keeps_values_canonical():
    (comment,) = read_golden(
        {"items": [{"key": " G1 ", "comment": " c ", "labels": {"alex": {BR: " forbearance "}}}]}
    )
    assert (comment.key, comment.comment, comment.labels) == (
        "G1",
        "c",
        {"alex": {BR: "forbearance"}},
    )


def test_read_predictions():
    data = {
        "model": "jev-1.14.0",
        "prompt_version": 4,
        "settings_version": 7,
        "predictions": {
            "G1": {
                RS: {"value": None, "automatic": False},
                BR: {"value": "other", "automatic": True, "settings_version": 6},
            }
        },
    }
    found = read_predictions(data)
    assert found == PredictionSet(
        "jev-1.14.0",
        4,
        {"G1": {RS: Prediction(None, False, 7), BR: Prediction("other", True, 6)}},
    )
    assert found.fields == [BR, RS]
    assert found.settings_versions == [6, 7]
    assert found.as_json() == {
        "model": "jev-1.14.0",
        "prompt_version": 4,
        "predictions": {
            "G1": {
                RS: {"value": None, "automatic": False, "settings_version": 7},
                BR: {"value": "other", "automatic": True, "settings_version": 6},
            }
        },
    }


def test_settings_versions_list_a_prediction_that_does_not_say_last():
    found = PredictionSet(
        "m", 3, {"1": {BR: Prediction(None, False)}, "2": {BR: Prediction(None, False, 7)}}
    )
    assert found.settings_versions == [7, None]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"model": ""}, "the model must be non-empty text"),
        ({"prompt_version": "3"}, "the prompt version must be a whole number from 1"),
        ({"prompt_version": True}, "the prompt version must be a whole number from 1"),
        ({"prompt_version": 0}, "the prompt version must be a whole number from 1"),
        ({"predictions": []}, "the predictions must be an object"),
        ({"settings_version": 0}, "the settings version must be a whole number from 1"),
        ({"settings_version": True}, "the settings version must be a whole number from 1"),
        (
            {
                "settings_version": None,
                "predictions": {"G1": {BR: {"value": None, "automatic": True}}},
            },
            "G1, borrower_request: no settings version says what produced it",
        ),
        (
            {"predictions": {"G1": {BR: {"value": None}}}},
            "G1, borrower_request: automatic must be true or false",
        ),
        (
            {"predictions": {"G1": {BR: {"value": "x", "automatic": True}}}},
            "G1: 'x' is not a borrower request; choose one of: maturity extension, forbearance, "
            "loan modification, discounted payoff, loan assumption, collateral release, other",
        ),
    ],
)
def test_read_predictions_refuses(change, message):
    data = {
        "model": "jev-1.14.0",
        "prompt_version": 3,
        "settings_version": 7,
        "predictions": {},
    } | change
    with pytest.raises(BadFile) as refused:
        read_predictions(data)
    assert str(refused.value) == message


def built(golden, predictions) -> ReportView:
    _, results = results_of(golden, predictions)
    results["settings_versions"] = [7]
    return ReportView(
        id=2,
        built_by="quinn",
        built_at=dt.datetime(2026, 10, 20, 12, tzinfo=dt.UTC),
        model=predictions.model,
        prompt_version=predictions.prompt_version,
        settings_version=7,
        items=len(golden),
        results=results,
        flags=["borrower request recall fell from 0.950 to 0.800"],
        previous_id=1,
    )


def test_report_text_opens_with_flags_and_the_band():
    golden = [
        item("1", alex={BR: "forbearance"}, morgan={BR: "forbearance"}),
        item("2", alex={BR: None}),
    ]
    predictions = PredictionSet(
        "jev-1.14.0",
        3,
        {"1": {BR: Prediction("forbearance", True)}, "2": {BR: Prediction(None, False)}},
    )
    assert report_text(built(golden, predictions)) == (
        "quality report 2: model jev-1.14.0, prompt version 3, settings version 7; 2 golden "
        "comments; built 2026-10-20 by quinn\n"
        "flag: borrower request recall fell from 0.950 to 0.800\n"
        'the abstention band is "not holding": auto-accepted borrower request accuracy 1.000, '
        "lower bound 0.207 is not above 0.85\n"
        "borrower request: precision 1.000 (95% interval 0.207 to 1.000; 1 of 1); recall "
        "1.000 (95% interval 0.207 to 1.000; 1 of 1); auto-accepted accuracy 1.000 (95% "
        "interval 0.207 to 1.000; 1 of 1)\n"
        "labeller agreement: 1 of 1 double-labelled comments (1.000, 95% interval 0.207 to "
        "1.000); Cohen's kappa: borrower request undefined\n"
    )


def test_report_text_without_flags_or_double_labels():
    golden = [item("1", alex={BR: None}), item("2", alex={BR: "other"}, morgan={BR: None})]
    predictions = PredictionSet(
        "jev-1.14.0", 3, {"1": {BR: Prediction(None, False)}, "2": {BR: Prediction(None, False)}}
    )
    view = built(golden, predictions)
    view = ReportView(**{**view.__dict__, "flags": []})
    text = report_text(view)
    assert "no measure fell since quality report 1\n" in text
    assert "; 1 disputed labels left out\n" in text
    assert "precision not measured (0 of 0)" in text
    single = ReportView(**{**view.__dict__, "previous_id": None})
    assert "no measure fell" not in report_text(single)
    solo = [item("1", alex={BR: None})]
    alone = built(solo, PredictionSet("m", 3, {"1": {BR: Prediction(None, False)}}))
    assert report_text(alone).endswith(
        "labeller agreement: not measured (no double-labelled comments)\n"
    )


def test_overall_band():
    def quality(automatic: Proportion) -> measure.FieldQuality:
        return measure.FieldQuality(BR, 1, 0, Proportion(0, 0), Proportion(0, 0), automatic)

    holding, failing, unmeasured = (
        quality(Proportion(60, 60)),
        quality(Proportion(1, 2)),
        quality(Proportion(0, 0)),
    )
    assert overall_band([holding, unmeasured]) == measure.HOLDING
    assert overall_band([holding, failing]) == measure.NOT_HOLDING
    assert overall_band([unmeasured]) == measure.NOT_MEASURED


def test_measure_text():
    assert measure_text({"rate": None, "successes": 0, "trials": 0}) == "not measured (0 of 0)"
    assert (
        measure_text(
            {"rate": "0.905", "low": "0.779", "high": "0.962", "successes": 38, "trials": 42}
        )
        == "0.905 (95% interval 0.779 to 0.962; 38 of 42)"
    )


def test_candidate_text():
    candidate = CandidateView(
        id=101,
        key="k",
        deal="Fixture Trust 2026-F1",
        loan="2",
        period=Period(2026, 9),
        accession="0009900001-26-000009",
        status="proposed",
        labels={"alex": {BR: "forbearance", RS: None}},
        sentences={BR: 3, RS: None},
        comment="c",
        decided_by=None,
        reason=None,
    )
    assert candidate_text(candidate) == (
        "#101 proposed: Fixture Trust 2026-F1 loan 2, September 2026: borrower request "
        "forbearance (sentence 3) by alex; request status not stated by alex"
    )


def test_versions_text():
    assert versions_text([7]) == "settings version 7"
    assert versions_text([6, 7]) == "settings versions 6 and 7"
    assert versions_text([5, 6, 7]) == "settings versions 5, 6 and 7"


def test_agreement_leaves_out_comments_not_labelled_independently():
    confirmed = GoldenComment("p", {"alex": {BR: "other"}, "morgan": {BR: "other"}}, False)
    independent = item("i", alex={BR: None}, morgan={BR: "other"})
    found = agreement([confirmed, independent], [BR])
    assert found.overall == Proportion(0, 1)
    assert found.kappa[BR] == 0  # only the independent pair: no agreement beyond chance
