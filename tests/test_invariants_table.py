"""decision.py's docstring table as rows, and Lean's committed copy of it."""

from cmbs.extraction.decision import (
    UNSETTLED_ANSWER,
    UNSETTLED_SUPPORT,
    Band,
    Outcome,
)
from cmbs.extraction.sentences import EVIDENCE_NOT_FOUND
from cmbs.invariants import oracle
from cmbs.invariants.table import (
    Combination,
    Row,
    combinations,
    docstring_rows,
    lean_rows,
    matching,
    parse_rows,
    python_decision,
)

LOW, UNSETTLED, HIGH = Band
ANY = (LOW, UNSETTLED, HIGH)
TABLE = [
    Row((LOW,), (True, False), ANY, Outcome.NOT_STATED, None),
    Row((UNSETTLED,), (False,), ANY, Outcome.REVIEW, EVIDENCE_NOT_FOUND),
    Row((HIGH,), (False,), ANY, Outcome.REVIEW, EVIDENCE_NOT_FOUND),
    Row((UNSETTLED,), (True,), ANY, Outcome.REVIEW, UNSETTLED_SUPPORT),
    Row((HIGH,), (True,), (LOW, UNSETTLED), Outcome.REVIEW, UNSETTLED_ANSWER),
    Row((HIGH,), (True,), (HIGH,), Outcome.AUTO_ACCEPT, None),
]


def test_the_docstring_table_is_read_row_by_row():
    assert docstring_rows() == TABLE


def test_lean_committed_table_is_the_docstring_table():
    assert lean_rows(oracle.committed_table()) == TABLE


def test_parsing_skips_prose_and_other_tables_and_stops_after_the_table():
    text = """
    | a | b |
    |---|---|
    | 1 | 2 |

    | support | evidence | answer | outcome |
    |---------|----------|--------|---------|
    | high    | found    | high   | auto-accept |
    | low     | not found | any   | review: evidence not found in source |

    | support | evidence | answer | outcome |
    | high    | found    | low    | not stated |
    """
    assert parse_rows(text) == [
        Row((HIGH,), (True,), (HIGH,), Outcome.AUTO_ACCEPT, None),
        Row((LOW,), (False,), ANY, Outcome.REVIEW, EVIDENCE_NOT_FOUND),
    ]
    assert parse_rows("no table here") == []


def test_eighteen_combinations_each_once():
    found = combinations()
    assert len(found) == 18 == len(set(found))
    assert found[0] == Combination(LOW, True, LOW)
    assert found[-1] == Combination(HIGH, False, HIGH)
    assert found[0].label == "support low, evidence found, answer low"
    assert found[-1].label == "support high, evidence not found, answer high"


def test_each_combination_matches_exactly_one_row_with_python_s_outcome():
    for combination in combinations():
        rows = matching(TABLE, combination)
        assert len(rows) == 1, combination
        assert python_decision(combination) == (rows[0].outcome, rows[0].reason)


def test_a_row_matches_only_on_all_three_columns():
    row = Row((HIGH,), (True,), (LOW,), Outcome.REVIEW, UNSETTLED_ANSWER)
    assert row.matches(Combination(HIGH, True, LOW))
    assert not row.matches(Combination(UNSETTLED, True, LOW))
    assert not row.matches(Combination(HIGH, False, LOW))
    assert not row.matches(Combination(HIGH, True, HIGH))
    assert python_decision(Combination(LOW, False, HIGH)) == (Outcome.NOT_STATED, None)
    assert python_decision(Combination(UNSETTLED, True, HIGH)) == (
        Outcome.REVIEW,
        UNSETTLED_SUPPORT,
    )
