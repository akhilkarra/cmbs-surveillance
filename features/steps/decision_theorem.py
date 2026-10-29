"""Steps for features/extraction/decision_table_theorem.feature.

Lean's decision table (`lean/Invariants/Decision.lean`) is proved total: every combination of
support band, evidence and answer band matches exactly one row, and Lean's `decide` follows it.
These steps compare that table with Python's `cmbs.extraction.decision.decide` and with the
table in its docstring, on all 18 combinations.

Lean's table is read from `lean/decision_table.json`, the oracle's own output, committed so the
scenario runs where Lean is not installed. Where `lake` is on PATH the steps also build the
oracle, check the committed table is still what it prints, and ask it for every combination.
"""

import shutil

from behave import given, then, when  # pyright: ignore[reportAttributeAccessIssue]

from cmbs.extraction.decision import Outcome
from cmbs.extraction.fields import spec
from cmbs.invariants import oracle, table


@given("the decision rules for the borrower request")
def step_decision_rules(context):
    assert spec("borrower_request").label == "borrower request"
    context.lean_rows = table.lean_rows(oracle.committed_table())
    context.doc_rows = table.docstring_rows()
    context.live = None
    if shutil.which("lake") is None:
        return
    executable, _ = oracle.build()
    assert executable is not None
    context.live = oracle.Oracle([str(executable)])
    live_rows = table.lean_rows(context.live.ask(oracle.TABLE_CASE))
    assert live_rows == context.lean_rows, "lean/decision_table.json is not what Lean prints"


@when("every combination of answer bands is enumerated")
def step_enumerate(context):
    enumerated = []
    for combination in table.combinations():
        live = None
        if context.live is not None:
            case = oracle.decision_case(
                answer=combination.answer,
                support=combination.support,
                evidence_found=combination.evidence_found,
            )
            answer = context.live.ask(case)
            live = (Outcome(answer["outcome"]), answer["reason"])
        enumerated.append(
            (
                combination,
                table.python_decision(combination),
                table.matching(context.lean_rows, combination),
                live,
            )
        )
    if context.live is not None:
        context.live.close()
    context.enumerated = enumerated


@then('each combination leads to exactly one of "{first}", "{second}" or "{third}"')
def step_exactly_one(context, first, second, third):
    outcomes = {first, second, third}
    assert outcomes == {outcome.value for outcome in Outcome}, outcomes
    assert context.lean_rows == context.doc_rows, "Lean's table is not decision.py's docstring"
    assert len(context.enumerated) == 18, len(context.enumerated)
    for combination, python, rows, live in context.enumerated:
        assert len(rows) == 1, f"{combination.label}: {len(rows)} rows of Lean's table match"
        lean = (rows[0].outcome, rows[0].reason)
        assert python == lean, f"{combination.label}: Python {python}, Lean's table {lean}"
        assert live is None or live == python, f"{combination.label}: Lean's decide {live}"
        assert python[0].value in outcomes, python
