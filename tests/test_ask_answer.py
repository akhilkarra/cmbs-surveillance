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
    outside_in,
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
    table = Citation(
        "10-D", "R-2", FILED, "https://r/", report_table="Appraisal Reduction Detail",
        dated=dt.date(2026, 8, 15),
    )  # fmt: skip
    assert table.text() == "10-D R-2, Appraisal Reduction Detail, dated 2026-08-15"
    undated = Citation("10-D", "R-2", FILED, "https://r/", report_table="Appraisal")
    assert undated.text() == "10-D R-2, Appraisal"


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
    assert topics_in("Is it past due?") == ["payment status"]
    assert topics_in("Is it delinquent?") == ["payment status"]
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


def test_a_topic_awaiting_review_states_no_extracted_value_but_the_tape_s():
    """Grill log Q97: the extracted value stays unstated; the tape's value on the topic is
    stated, labelled as the tape's, and the filer's sentence is quoted as unreviewed."""
    answer = compose("What is the workout strategy?", deal="F1", loan="2", facts=FACTS)
    assert answer.kind is Kind.ANSWERED
    assert [f.name for f in answer.stated] == ["tape.workout_strategy"]
    assert answer.awaiting_review == ["workout strategy"]
    assert answer.unreviewed == []  # no sentence is known for it
    assert answer.text() == (
        "From the panel, for loan 2 of F1:\n- tape.workout strategy: full payoff (code 11) "
        "(September 2026, reported; ABS-EE T-1, workoutStrategyCode)\n"
        "- the workout strategy is awaiting human review"
    )
    both = compose("Balance and workout?", deal="F1", loan="2", facts=FACTS)
    assert [f.name for f in both.stated] == ["tape.workout_strategy", "tape.ending_balance"]


def test_a_field_awaiting_review_quotes_its_sentence_labelled_unreviewed():
    waiting = Fact(
        "workout_strategy",
        "workout strategy",
        "workout strategy",
        "September 2026",
        None,
        "needs review",
        Citation("10-D", "R-1", FILED, "https://r/", sentence=4, quote="It is reviewing."),
    )
    extracted = fact("transfer_reason", "workout strategy", "imminent default")
    answer = compose(
        "What is the workout strategy?", deal="F1", loan="2", facts=[waiting, extracted]
    )
    assert (answer.stated, answer.unreviewed) == ([], [waiting])
    quoted = (
        'the comment\'s sentence on it, unreviewed: not a panel fact: "It is reviewing." '
        "(10-D R-1, sentence 4)"
    )
    assert waiting.unreviewed_quote() == quoted
    assert waiting.text() == (
        f"workout strategy: awaiting human review (September 2026 comment); {quoted}"
    )
    assert answer.text().splitlines()[1] == (
        f"- the workout strategy is awaiting human review; {quoted}"
    )
    assert extracted.unreviewed_quote() is None
    assert not waiting.from_tape and not extracted.from_tape


def test_a_question_no_fact_covers_is_not_in_the_data():
    answer = compose("How much equity does the sponsor have?", deal="F1", loan="2", facts=FACTS)
    assert answer == Answer(
        "How much equity does the sponsor have?",
        "F1",
        "2",
        Kind.NOT_IN_DATA,
        not_in_data=["equity", "sponsor"],
    )
    assert answer.text() == (
        "not in the data: the panel does not contain that information (equity, sponsor) for "
        "loan 2 of F1, and nothing is estimated."
    )
    occupancy = compose("What is the occupancy?", deal="F1", loan="2", facts=FACTS)
    assert (occupancy.kind, occupancy.not_in_data) == (Kind.NOT_IN_DATA, ["occupancy"])
    plain = compose("Tell me a story", deal="F1", loan="2", facts=FACTS)
    assert plain.text() == (
        "not in the data: the panel does not contain that information for loan 2 of F1, and "
        "nothing is estimated."
    )


def test_a_request_to_change_data_is_refused():
    answer = compose("Mark loan 2 as resolved", deal="F1", loan="2", facts=FACTS)
    assert (answer.kind, answer.stated) == (Kind.READ_ONLY, [])
    assert answer.text() == (
        "asking is read-only: questions can read the panel but never change it. Make changes "
        "through the review queue, overrides or a refresh."
    )


def test_no_part_of_a_question_is_answered_by_substituting_another():
    """A concept the panel does not hold is "not in the data", even when another word of the
    question names a topic it does hold."""
    sponsor = compose("What does the sponsor want for loan 2?", deal="F1", loan="2", facts=FACTS)
    assert (sponsor.kind, sponsor.stated, sponsor.not_in_data) == (
        Kind.NOT_IN_DATA,
        [],
        ["sponsor"],
    )
    insured = compose(
        "What is the insurance coverage and the current LTV on loan 2?",
        deal="F1",
        loan="2",
        facts=FACTS,
    )
    assert (insured.kind, insured.stated, insured.not_in_data) == (
        Kind.NOT_IN_DATA,
        [],
        ["LTV", "insurance"],
    )
    both = compose(
        "What is the balance of loan 2 and what is the property worth?",
        deal="F1",
        loan="2",
        facts=FACTS,
    )
    assert both.kind is Kind.ANSWERED
    assert [f.name for f in both.stated] == ["tape.ending_balance"]
    assert both.not_in_data == ["valuation"]
    assert both.text().splitlines()[-1] == (
        "- valuation: not in the data; the panel does not contain it, and nothing is estimated"
    )
    partly = compose("What is the balance and occupancy?", deal="F1", loan="2", facts=FACTS)
    assert ([f.name for f in partly.stated], partly.not_in_data) == (
        ["tape.ending_balance"],
        ["occupancy"],
    )


def test_concepts_outside_the_panel():
    assert outside_in("What is the LTV, cap rate and appraised value?") == [
        "appraisal",
        "LTV",
        "cap rate",
    ]
    assert outside_in("Who is the guarantor? Any tenants or reserves?") == [
        "guarantor",
        "tenants and leases",
        "reserves",
    ]
    assert outside_in("When does it mature, and at what interest rate?") == [
        "interest rate",
        "maturity date",
    ]
    assert outside_in("Why is loan 2 in special servicing?") == []
    assert outside_in("What is the ASER and the latest appraised value?") == [
        "appraisal",
        "ASER",
    ]
    assert outside_in("What is the appraisal reduction?") == []
    assert outside_in("Any appraisal subordinate entitlement reduction?") == ["ASER"]


def test_loss_topics():
    assert topics_in("What are the advances outstanding?") == ["advances"]
    assert topics_in("Are they non-recoverable? Any P&I?") == ["advances"]
    assert topics_in("Any realized loss or losses?") == ["realized loss"]
    assert topics_in("When was it liquidated?") == ["liquidation"]
    assert topics_in("What is the ARA? The appraisal reduction?") == ["appraisal reduction"]
    assert topics_in("When did it return to the master servicer?") == ["special servicing transfer"]


@pytest.mark.parametrize(
    "question",
    [
        "I want you to mark loan 2 as resolved",
        "I'd like you to close the alert on loan 7",
        "Show me loan 2's balance, then delete the review item",
        "What is the balance? Also override the borrower request.",
        "Tell me the DSCR and set it to 1.2x",
    ],
)
def test_a_request_to_change_anywhere_in_the_question_is_refused(question):
    assert is_write_request(question)
    assert compose(question, deal="F1", loan="2", facts=FACTS).kind is Kind.READ_ONLY
