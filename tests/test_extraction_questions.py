"""The questions asked about a comment, and reading the answers into decided fields."""

from decimal import Decimal

import pytest

from cmbs.extraction.decision import (
    NO_SUPPORTING_SENTENCE,
    UNSETTLED_ANSWER,
    UNSETTLED_SUPPORT,
    Decision,
    Outcome,
)
from cmbs.extraction.fields import FIELDS
from cmbs.extraction.judgments import Judgment, decide_fields, read
from cmbs.extraction.questions import (
    NONE_OF_THESE,
    PROMPT_VERSION,
    WORDING,
    UnknownPromptVersion,
    check_prompt_version,
    plan,
    sentence_name,
    support_name,
    value_name,
)
from cmbs.extraction.sentences import EVIDENCE_NOT_FOUND, split
from cmbs.model.scripted import choice_answer, respond
from cmbs.model.types import Choice, ModelRequest, Noul, NoulAnswer, NoulCriteria
from tests.test_extraction_sentences import LOAN_2

SENTENCES = split(LOAN_2)
PLAN = plan(LOAN_2, SENTENCES)
LOW, HIGH = Decimal("0.30"), Decimal("0.70")


def test_every_field_has_wording():
    assert set(WORDING) == {f.name for f in FIELDS}


def test_the_prompt_version_is_3():
    assert PROMPT_VERSION == 3
    check_prompt_version(3)
    with pytest.raises(UnknownPromptVersion) as refused:
        check_prompt_version(4)
    assert str(refused.value) == (
        "prompt version 4 is not defined here; this code asks prompt version 3"
    )


def test_the_state_is_the_comment_with_whitespace_normalised():
    assert plan(LOAN_2.replace(". ", ".\n  "), SENTENCES).state == {"comment": LOAN_2}


def test_three_questions_per_field_the_comment_could_state():
    assert PLAN.asked == tuple(f.name for f in FIELDS)
    assert len(PLAN.questions) == 27
    assert list(PLAN.questions)[:3] == [
        "transfer_reason.value",
        "transfer_reason.support",
        "transfer_reason.sentence",
    ]


def test_a_category_offers_its_options_with_descriptions():
    question = PLAN.questions[value_name("borrower_request")]
    assert isinstance(question, Choice)
    assert question.instructions == WORDING["borrower_request"].value
    assert question.criteria["forbearance"] == ("A temporary pause in, or reduction of, payments.")
    assert NONE_OF_THESE not in question.criteria
    status = PLAN.questions[value_name("request_status")]
    assert isinstance(status, Choice) and list(status.criteria) == ["pending", "granted", "denied"]


def test_a_figure_offers_the_printed_figures_and_none_of_these():
    question = PLAN.questions[value_name("transfer_date")]
    assert isinstance(question, Choice)
    assert list(question.criteria) == ["3/6/2025", "12/31/2024", NONE_OF_THESE]
    assert question.criteria["3/6/2025"] is None
    assert PLAN.options["dscr"] == ("0.71x",)


def test_support_is_a_noul_with_concrete_criteria():
    question = PLAN.questions[support_name("workout_strategy")]
    wording = WORDING["workout_strategy"]
    assert question == Noul(
        instructions=wording.support,
        criteria=NoulCriteria(true=wording.support_true, false=wording.support_false),
    )


def test_the_sentence_options_are_the_sentences_verbatim():
    question = PLAN.questions[sentence_name("dscr")]
    assert isinstance(question, Choice)
    assert list(question.criteria) == [s.text for s in SENTENCES]
    assert question.instructions == WORDING["dscr"].sentence


def test_a_figure_the_comment_never_prints_is_not_asked():
    comment = "The borrower requested forbearance. The servicer denied it."
    asked = plan(comment, split(comment))
    assert asked.asked == (
        "transfer_reason",
        "borrower_request",
        "request_status",
        "workout_strategy",
    )
    assert value_name("dscr") not in asked.questions


def _response(answers):
    request = ModelRequest(model="jev-1.14.0", state=PLAN.state, questions=PLAN.questions)
    return respond(request, answers)


def _choice(name):
    question = PLAN.questions[name]
    assert isinstance(question, Choice)
    return question


def test_answers_are_read_into_judgments_with_their_sentence():
    response = _response(
        {
            value_name("transfer_date"): choice_answer(
                _choice(value_name("transfer_date")), "3/6/2025", 0.9
            ),
            support_name("transfer_date"): NoulAnswer(noul=0.95),
            sentence_name("transfer_date"): choice_answer(
                _choice(sentence_name("transfer_date")), SENTENCES[0].text, 0.9
            ),
        }
    )
    judgment = read(PLAN, SENTENCES, response)["transfer_date"]
    assert judgment == Judgment(
        field="transfer_date",
        printed="3/6/2025",
        value="2025-03-06",
        answer_probability=Decimal("0.9"),
        support_probability=Decimal("0.95"),
        evidence=SENTENCES[0].text,
        sentence=1,
        value_question=WORDING["transfer_date"].value,
        support_question=WORDING["transfer_date"].support,
    )
    assert judgment.evidence_found


def test_none_of_these_only_absorbs_probability():
    response = _response(
        {
            value_name("transfer_date"): choice_answer(
                _choice(value_name("transfer_date")), NONE_OF_THESE, 0.6
            )
        }
    )
    judgment = read(PLAN, SENTENCES, response)["transfer_date"]
    assert (judgment.printed, judgment.answer_probability) == ("3/6/2025", Decimal("0.2"))


def test_a_cited_sentence_that_is_not_in_the_comment_is_not_evidence():
    request = ModelRequest(model="jev-1.14.0", state=PLAN.state, questions=PLAN.questions)
    response = respond(request, {})
    forged = response.answers | {
        sentence_name("dscr"): choice_answer(
            Choice(instructions="x", criteria={"The DSCR improved.": None}),
            "The DSCR improved.",
            0.9,
        )
    }
    judgment = read(PLAN, SENTENCES, response.model_copy(update={"answers": forged}))["dscr"]
    assert (judgment.sentence, judgment.evidence_found) == (None, False)
    assert judgment.evidence == "The DSCR improved."


def test_unanswered_fields_read_as_low_and_are_not_stated():
    results = decide_fields(read(PLAN, SENTENCES, _response({})), low=LOW, high=HIGH)
    assert list(results) == [f.name for f in FIELDS]
    assert {r.decision for r in results.values()} == {Decision(Outcome.NOT_STATED)}


def test_fields_without_a_judgment_are_not_stated():
    results = decide_fields({}, low=LOW, high=HIGH)
    assert all(
        r.judgment is None and r.decision.outcome is Outcome.NOT_STATED for r in results.values()
    )


def _judgment(**changes) -> Judgment:
    base = Judgment(
        field="borrower_request",
        printed="forbearance",
        value="forbearance",
        answer_probability=Decimal("0.9"),
        support_probability=Decimal("0.9"),
        evidence=SENTENCES[2].text,
        sentence=3,
        value_question="v",
        support_question="s",
    )
    return Judgment(**{**base.__dict__, **changes})


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({}, Decision(Outcome.AUTO_ACCEPT)),
        ({"answer_probability": Decimal("0.70")}, Decision(Outcome.REVIEW, UNSETTLED_ANSWER)),
        (
            {"support_probability": Decimal("0.29")},
            Decision(Outcome.REVIEW, NO_SUPPORTING_SENTENCE),
        ),
        ({"sentence": None}, Decision(Outcome.REVIEW, EVIDENCE_NOT_FOUND)),
        (
            {"support_probability": Decimal("0.1"), "answer_probability": Decimal("0.5")},
            Decision(Outcome.NOT_STATED),
        ),
    ],
)
def test_judgments_are_decided_against_the_band(changes, expected):
    results = decide_fields({"borrower_request": _judgment(**changes)}, low=LOW, high=HIGH)
    assert results["borrower_request"].decision == expected


def test_a_wider_band_sends_the_same_answer_to_review():
    results = decide_fields({"borrower_request": _judgment()}, low=LOW, high=Decimal("0.95"))
    assert results["borrower_request"].decision == Decision(Outcome.REVIEW, UNSETTLED_SUPPORT)
