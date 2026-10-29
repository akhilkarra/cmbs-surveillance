"""Facts, citations and composed answers (cmbs.ask.answer). No database."""

import datetime as dt

import pytest

from cmbs.ask.answer import (
    Answer,
    Citation,
    Fact,
    Kind,
    compose,
    is_write_request,
    loan_in,
    topics_in,
)

FILED = dt.date(2026, 9, 29)
REPORT = Citation("10-D", "R-1", FILED, "https://r/", sentence=3, quote="The borrower asked.")
TAPE = Citation("ABS-EE", "T-1", FILED, "https://t/", tape_field="workoutStrategyCode")


def fact(name: str, topic: str, value: str | None = "v", status: str = "auto-accepted", **extra):
    return Fact(
        name=name,
        label=name.replace("_", " "),
        topic=topic,
        period="September 2026",
        value=value,
        status=status,
        citation=None if value is None else extra.pop("citation", REPORT),
        **extra,
    )


def test_citations_read_with_their_sentence_or_tape_field():
    assert REPORT.text() == '10-D R-1, sentence 3: "The borrower asked."'
    assert TAPE.text() == "ABS-EE T-1, workoutStrategyCode"


def test_facts_read_with_status_who_and_citation():
    accepted = fact("borrower_request", "borrower request", "maturity extension")
    assert accepted.text() == (
        "borrower request: maturity extension (September 2026, auto-accepted; 10-D R-1, "
        'sentence 3: "The borrower asked.")'
    )
    overridden = fact("borrower_request", "b", "forbearance", "overridden", set_by="alex")
    assert overridden.text().startswith("borrower request: forbearance (September 2026, "
                                        "overridden by alex; 10-D")  # fmt: skip
    waiting = fact("workout_strategy", "workout strategy", None, "needs review")
    assert waiting.awaiting_review and not accepted.awaiting_review
    assert waiting.text() == "workout strategy: awaiting human review (September 2026 comment)"
    uncited = Fact("x", "x", "t", "September 2026", "1", "reported", None)
    assert uncited.text() == "x: 1 (September 2026, reported)"


@pytest.mark.parametrize(
    "question",
    [
        "Mark loan 2 as resolved",
        "mark loan 2 resolved.",
        "Please close the alert on loan 7",
        "Can you set the DSCR to 1.2x?",
        "could you, please, override the borrower request",
        "Kindly delete loan 6",
        "  Update loan 2's workout strategy",
        "Go ahead and refresh the deal",
    ],
)
def test_requests_to_change_data_are_recognised(question):
    assert is_write_request(question)


@pytest.mark.parametrize(
    "question",
    [
        "Why is loan 2 in special servicing?",
        "What is the workout strategy for loan 2?",
        "Was loan 2 marked as resolved?",
        "Can you tell me the balance?",
        "Please",
        "",
        "Is the alert closed?",
    ],
)
def test_questions_are_not_requests_to_change(question):
    assert not is_write_request(question)


def test_the_loan_a_question_names():
    assert loan_in("Why is loan 2 in special servicing?") == "2"
    assert loan_in("What about Loan 2-001?") == "2-001"
    assert loan_in("loan no. 7, please") == "7"
    assert loan_in("loan number 12") == "12"
    assert loan_in("loan #3") == "3"
    assert loan_in("How much equity does the sponsor have?") is None
    assert loan_in("the loans in the deal") is None


def test_topics_a_question_names():
    assert topics_in("Why is loan 2 in special servicing and what does the borrower want?") == [
        "special servicing transfer",
        "borrower request",
    ]
    assert topics_in("What is the workout strategy for loan 2?") == ["workout strategy"]
    assert topics_in("How much equity does the sponsor have in the property?") == []
    assert topics_in("What's the DSCR and occupancy?") == ["DSCR", "occupancy"]
    assert topics_in("Is it late?") == ["payment status"]
    assert topics_in("Was it translated later?") == []
    assert topics_in("What is the balance?") == ["balance"]
    assert topics_in("When are the financials from? NOI?") == ["financials"]
    assert topics_in("Was it modified?") == ["modification"]
    assert topics_in("What property type is it?") == ["property"]


FACTS = [
    fact("transfer_date", "special servicing transfer", "2025-03-06"),
    fact("borrower_request", "borrower request", "maturity extension"),
    fact("workout_strategy", "workout strategy", None, "needs review"),
    fact("tape.workout_strategy", "workout strategy", "full payoff (code 11)", "reported",
         citation=TAPE),
    fact("tape.ending_balance", "balance", "$30,000,000.00", "reported", citation=TAPE),
]  # fmt: skip


def test_an_answer_states_only_the_facts_the_question_asks_about():
    answer = compose("When did loan 2 transfer?", deal="F1", loan="2", facts=FACTS)
    assert answer.kind is Kind.ANSWERED
    assert [f.name for f in answer.stated] == ["transfer_date"]
    assert answer.awaiting_review == []
    assert answer.text() == (
        "From the panel, for loan 2 of F1:\n- transfer date: 2025-03-06 (September 2026, "
        'auto-accepted; 10-D R-1, sentence 3: "The borrower asked.")'
    )


def test_a_topic_awaiting_review_states_no_value_from_any_source():
    answer = compose("What is the workout strategy?", deal="F1", loan="2", facts=FACTS)
    assert answer.kind is Kind.ANSWERED
    assert answer.stated == []
    assert answer.awaiting_review == ["workout strategy"]
    assert answer.text() == (
        "From the panel, for loan 2 of F1:\n- the workout strategy is awaiting human review"
    )
    both = compose("Balance and workout?", deal="F1", loan="2", facts=FACTS)
    assert [f.name for f in both.stated] == ["tape.ending_balance"]


def test_a_question_no_fact_covers_is_not_in_the_data():
    answer = compose("How much equity does the sponsor have?", deal="F1", loan="2", facts=FACTS)
    assert answer == Answer("How much equity does the sponsor have?", "F1", "2", Kind.NOT_IN_DATA)
    assert answer.text() == (
        "not in the data: the panel does not contain that information for loan 2 of F1, and "
        "nothing is estimated."
    )
    occupancy = compose("What is the occupancy?", deal="F1", loan="2", facts=FACTS)
    assert occupancy.kind is Kind.NOT_IN_DATA


def test_a_request_to_change_data_is_refused():
    answer = compose("Mark loan 2 as resolved", deal="F1", loan="2", facts=FACTS)
    assert (answer.kind, answer.stated) == (Kind.READ_ONLY, [])
    assert answer.text() == (
        "asking is read-only: questions can read the panel but never change it. Make changes "
        "through the review queue, overrides or a refresh."
    )
