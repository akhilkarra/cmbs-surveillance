"""Steps for features/surfaces/ask.feature: the deterministic layer of the /cmbs-ask skill.

The skill reads the facts `cmbs ask` gives and composes its answer under the same rules as
`cmbs.ask.answer.compose`, which these steps run: every stated fact cites its filing (and, from
a comment, its sentence), a value awaiting review is not stated, a question no fact covers gets
"not in the data", and a request to change something is refused. The questions are asked on
2026-10-06, about the fixture panel (`features/steps/export.py`, `tests/test_routine_world.py`).
"""

import datetime as dt
import re

from behave import given, then, when  # pyright: ignore[reportAttributeAccessIssue]

from cmbs import clock
from cmbs.ask.answer import COMMENT_FORM, NOT_IN_DATA, READ_ONLY, UNREVIEWED, Kind
from cmbs.ask.service import ask, loan_facts
from cmbs.db.models import SCHEMAS
from cmbs.extraction.service import comment_view
from cmbs.testing.bdd import attempt, changed_tables, data_snapshot, in_session
from features.steps.published import publish
from tests.test_routine_world import SEPTEMBER, accession, backfill

ASKED = dt.datetime(2026, 10, 6, 12, 0, tzinfo=dt.UTC)


@given('loan "{loan}" of "{deal}" has its {month} comment extracted')
def step_comment_extracted(context, loan, deal, month):

    assert month == SEPTEMBER.label, "the fixture panel's latest month is September 2026"
    context.deal, context.loan = deal, loan
    backfill(in_session, SEPTEMBER.shift(-1), SEPTEMBER, deal=deal)
    view = in_session(lambda db: comment_view(db, "quinn", deal=deal, loan=loan, period=SEPTEMBER))
    assert view is not None, "the comment was not extracted"
    context.comment = view


# Registered before the step below, whose pattern would also match this one's text: behave
# takes the first step that matches.
@when('"{user}" asks "{question}" about "{deal}"')
def step_asks_about(context, user, question, deal):
    """A question about a deal whose months the scenario published (features/steps/published.py),
    asked at noon on the day they were loaded."""
    publish(context)
    context.deal = deal
    _ask(context, user, question)


@when('"{user}" asks "{question}"')
def step_asks(context, user, question):
    clock.set_now(ASKED)
    _ask(context, user, question)


def _ask(context, user, question):
    deal = getattr(context, "deal", None)
    assert deal is not None, "no step named the deal"
    context.question = question
    # Asking writes nothing anywhere: not the data layers ("no stored data changes" checks
    # those), and not the application state either (audit log, runs, review items, cache).
    before = data_snapshot(SCHEMAS)
    answer = attempt(
        context,
        lambda: in_session(
            lambda db: ask(db, user, question, deal=deal, loan=getattr(context, "loan", None))
        ),
    )
    assert answer is not None, f"asking was refused: {context.refusal}"
    changed = changed_tables(before, data_snapshot(SCHEMAS))
    assert changed == [], f"asking wrote to {changed}"
    context.answer = answer
    context.cited = []


def _stated(context, name: str):
    found = [fact for fact in context.answer.stated if fact.name == name]
    assert len(found) == 1, (name, context.answer.text())
    fact = found[0]
    assert fact.text() in context.answer.text(), context.answer.text()
    context.cited.append(fact)
    return fact


@then('the answer says loan "{loan}" transferred on {day} due to {reason}')
def step_says_transfer(context, loan, day, reason):
    assert context.answer.loan == loan, context.answer
    assert _stated(context, "transfer_date").value == day
    assert _stated(context, "transfer_reason").value == reason


@then("the answer says the borrower has requested a {request}")
def step_says_request(context, request):
    fact = _stated(context, "borrower_request")
    assert fact.value is not None and fact.value in request, fact
    assert fact.citation is not None and fact.citation.quote is not None, fact
    assert f"requested a {request}" in fact.citation.quote, fact.citation.quote


@then("each of those statements cites the 10-D accession and the sentence number it came from")
def step_statements_cite(context):
    assert context.cited, "no statement was checked"
    sentences = context.comment.sentences
    expected = accession(context.deal, SEPTEMBER, COMMENT_FORM)
    for fact in context.cited:
        cited = fact.citation
        assert cited is not None, fact
        assert (cited.form, cited.accession) == (COMMENT_FORM, expected), cited
        assert cited.sentence is not None and 1 <= cited.sentence <= len(sentences), cited
        assert cited.quote == sentences[cited.sentence - 1], cited
        assert f'sentence {cited.sentence}: "{cited.quote}"' in context.answer.text()


@then("the answer says the panel does not contain that information")
def step_not_in_data(context):
    assert context.answer.kind is Kind.NOT_IN_DATA, context.answer
    assert context.answer.text().startswith(
        f"{NOT_IN_DATA}: the panel does not contain that information"
    ), context.answer.text()


@then("no figure is estimated")
def step_no_estimate(context):
    assert context.answer.stated == [], context.answer.stated
    facts = in_session(
        lambda db: loan_facts(db, "pat", deal=context.deal, loan=context.answer.loan)
    )
    text = context.answer.text()
    # A figure is a value with a digit in it: "no" (advances non-recoverable) is not one.
    figures = [fact.value for fact in facts.facts if fact.value and re.search(r"\d", fact.value)]
    shown = [figure for figure in figures if figure in text]
    assert shown == [], shown
    assert not any(
        character.isdigit()
        for character in text.replace(context.deal, "").replace(f"loan {context.answer.loan}", "")
    ), text


@then("the answer says the {label} is awaiting human review")
def step_awaiting(context, label):
    assert context.answer.awaiting_review == [label], context.answer
    assert f"the {label} is awaiting human review" in context.answer.text()


def _waiting(context, label: str):
    found = [fact for fact in context.answer.unreviewed if fact.label == label]
    assert len(found) == 1, (label, context.answer)
    return found[0]


@then("no extracted {label} is stated")
def step_no_extracted(context, label):
    """No value from the comment is stated on the field's topic, and the model's reading
    appears nowhere, not even inside a line that cites the tape or quotes the filer."""
    waiting = _waiting(context, label)
    extracted = [
        fact for fact in context.answer.stated if fact.topic == waiting.topic and not fact.from_tape
    ]
    assert extracted == [], extracted
    model_value = context.comment.fields[waiting.name].model_value
    assert model_value, "the model gave no reading to keep out"
    assert model_value.lower() not in context.answer.text().lower(), context.answer.text()


@then('the answer quotes the comment\'s sentence on it, labelled "{label}"')
def step_quotes_sentence(context, label):
    assert label == UNREVIEWED, label
    [waiting_label] = context.answer.awaiting_review
    fact = _waiting(context, waiting_label)
    cited = fact.citation
    assert cited is not None and cited.form == COMMENT_FORM, fact
    sentences = context.comment.sentences
    assert cited.sentence is not None and cited.quote == sentences[cited.sentence - 1], cited
    assert cited.accession == accession(context.deal, SEPTEMBER, COMMENT_FORM), cited
    line = (
        f"- the {waiting_label} is awaiting human review; the comment's sentence on it, "
        f'{label}: "{cited.quote}" ({COMMENT_FORM} {cited.accession}, sentence {cited.sentence})'
    )
    assert line in context.answer.text().splitlines(), context.answer.text()


@then("the answer gives the tape's {label} \"{value}\", labelled as the tape's, citing {tag}")
def step_gives_tape(context, label, value, tag):
    found = [
        fact
        for fact in context.answer.stated
        if fact.from_tape and fact.label == f"{label} on the tape"
    ]
    assert len(found) == 1, context.answer
    fact = found[0]
    assert fact.value == value, fact
    assert fact.citation is not None and fact.citation.tape_field == tag, fact
    assert f"- {fact.text()}" in context.answer.text().splitlines(), context.answer.text()


@then("the answer says asking is read-only")
def step_read_only(context):
    assert context.answer.kind is Kind.READ_ONLY, context.answer
    assert context.answer.text().startswith(READ_ONLY), context.answer.text()
