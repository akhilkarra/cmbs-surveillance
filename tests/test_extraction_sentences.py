"""Sentence splitting and the exact evidence check."""

import pytest

from cmbs.extraction.sentences import (
    EVIDENCE_NOT_FOUND,
    EvidenceNotFound,
    Sentence,
    find,
    normalize,
    numbered,
    split,
)

LOAN_2 = (
    "The loan transferred to Special Servicing effective 3/6/2025 due to imminent monetary "
    "default. As of 12/31/2024, DSCR NOI was 0.71x and occupancy was 61.4%. The borrower has "
    "requested a 12-month maturity extension. The special servicer is evaluating the request."
)


def texts(comment: str) -> list[str]:
    return [sentence.text for sentence in split(comment)]


def test_the_fixture_comment_has_four_numbered_sentences():
    assert split(LOAN_2) == [
        Sentence(
            1,
            "The loan transferred to Special Servicing effective 3/6/2025 due to imminent "
            "monetary default.",
        ),
        Sentence(2, "As of 12/31/2024, DSCR NOI was 0.71x and occupancy was 61.4%."),
        Sentence(3, "The borrower has requested a 12-month maturity extension."),
        Sentence(4, "The special servicer is evaluating the request."),
    ]


def test_whitespace_is_normalised_and_does_not_change_the_split():
    spaced = LOAN_2.replace(". ", ".\n   ").replace(" was ", "  was\t")
    assert split(spaced) == split(LOAN_2)


@pytest.mark.parametrize(
    ("comment", "expected"),
    [
        ("Paid. Then late! Now current? Yes.", ["Paid.", "Then late!", "Now current?", "Yes."]),
        ("DSCR was 1.25x. 2025 NOI fell.", ["DSCR was 1.25x.", "2025 NOI fell."]),
        ('He said "paid." The loan is current.', ['He said "paid."', "The loan is current."]),
        (
            "Rents rose (as planned.) Occupancy fell.",
            ["Rents rose (as planned.)", "Occupancy fell."],
        ),
        ("Rates are 5.5% now. Fine.", ["Rates are 5.5% now.", "Fine."]),
        ("It ended. and then more", ["It ended. and then more"]),
        ("No trailing stop", ["No trailing stop"]),
    ],
)
def test_sentence_ends(comment, expected):
    assert texts(comment) == expected


@pytest.mark.parametrize(
    "comment",
    [
        "The trustee is U.S. Bank as of now.",
        "Space is approx. 5 percent vacant.",
        "See No. 2 above.",
        "The tenant is Acme Inc. Holdings.",
        "The asset is in St. Louis today.",
        "St. Louis is the market.",
        "As of Dec. 31, 2024 the DSCR was 1.10x.",
        "As of Sept. 30 occupancy fell.",
        "The guarantor John A. Smith signed.",
        "See No. 5 above.",
        "Loans Nos. 3 and 4 are cross-collateralized.",
        "Recoveries, e.g. Rents, continue.",
        "Mr. Smith signed.",
    ],
)
def test_abbreviations_do_not_end_sentences(comment):
    assert texts(comment) == [comment]


def test_an_abbreviation_is_only_skipped_once():
    assert texts("Wired by U.S. Bank. The loan is current.") == [
        "Wired by U.S. Bank.",
        "The loan is current.",
    ]


def test_empty_text_has_no_sentences():
    assert split("  \n ") == []


def test_normalize_collapses_runs_of_whitespace():
    assert normalize("  a \n\t b  ") == "a b"


def test_evidence_must_quote_a_whole_sentence_exactly():
    sentences = split(LOAN_2)
    assert find(sentences, "The borrower has requested a 12-month maturity extension.").number == 3
    assert find(sentences, "  The special servicer is\nevaluating the request. ").number == 4
    for quote in (
        "The borrower was granted a 12-month extension.",
        "The borrower has requested a 12-month maturity extension",
        "the borrower has requested a 12-month maturity extension.",
        "requested a 12-month maturity extension.",
    ):
        with pytest.raises(EvidenceNotFound) as refused:
            find(sentences, quote)
        assert str(refused.value) == EVIDENCE_NOT_FOUND == "evidence not found in source"


def test_numbered_finds_a_sentence_by_number():
    sentences = split(LOAN_2)
    found = numbered(sentences, 2)
    assert found is not None and found.text.startswith("As of 12/31/2024")
    assert numbered(sentences, 0) is None
    assert numbered(sentences, 5) is None


@pytest.mark.parametrize(
    ("comment", "expected"),
    [
        (
            "The servicer said no. The borrower agreed.",
            ["The servicer said no.", "The borrower agreed."],
        ),
        (
            "The asset is on Main St. The loan is current.",
            ["The asset is on Main St.", "The loan is current."],
        ),
        (
            "The asset is in St. Louis. The loan is current.",
            ["The asset is in St. Louis.", "The loan is current."],
        ),
        ("Rated A. B notes paid.", ["Rated A. B notes paid."]),
        ("Paid in Dec. The loan is current.", ["Paid in Dec. The loan is current."]),
    ],
)
def test_the_reviewers_boundary_cases(comment, expected):
    assert texts(comment) == expected
