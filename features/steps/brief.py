"""Steps for features/surfaces/committee_brief.feature: `cmbs brief deal`, the committee brief on
a deal's special-servicing book (grill log Q98), driven through `cmbs.brief.service` in process.

The Background is the deal roster's (features/steps/roster.py): the September 2026 tape with
exactly the table's loans, loaded by run 42 on 2026-10-01 and read at noon that day. Loan 2's
comment is extracted by that run, replaying the synthetic model recordings (the ask step that
names the comment, features/steps/ask.py). The brief is checked against what asking gives on its
own: the roster, each loan's facts and its history, and the filings the panel stored.
"""

import re

from behave import then, when  # pyright: ignore[reportAttributeAccessIssue]
from sqlalchemy import select

from cmbs.ask.answer import COMMENT_FORM, NO_SENTENCE, NOT_IN_DATA, TAPE_FORM, UNREVIEWED
from cmbs.ask.history import history
from cmbs.ask.roster import ROSTER_FIELDS, loans
from cmbs.ask.service import ASSET_TAGS, TAPE_FIELDS, loan_facts
from cmbs.brief.service import NOT_IN_PANEL, brief, month_line, other_line
from cmbs.db.models import SCHEMAS
from cmbs.deals.service import by_name
from cmbs.domain import Period
from cmbs.extraction.service import comment_view
from cmbs.filings.tables import Filing
from cmbs.testing.bdd import changed_tables, data_snapshot, in_session
from features.steps.published import publish

SEPTEMBER = Period(2026, 9)
SCHEDULE_AL_TAGS = {spec.tag for spec in TAPE_FIELDS} | {spec.tag for spec in ASSET_TAGS}
ROSTER_TAGS = set(ROSTER_FIELDS)


@when('"{user}" asks for the committee brief of "{deal}"')
def step_asks_brief(context, user, deal):
    publish(context)
    context.deal = deal
    context.comment = in_session(
        lambda db: comment_view(db, "quinn", deal=deal, loan="2", period=SEPTEMBER)
    )
    assert context.comment is not None, "loan 2's September 2026 comment was not extracted"
    # The brief writes nothing at all: not the data layers, which "no stored data changes"
    # checks, and not the application state (audit log, runs, review items, cache).
    context.snapshot = data_snapshot()
    before = data_snapshot(SCHEMAS)
    context.brief = in_session(lambda db: brief(db, user, deal=deal))
    changed = changed_tables(before, data_snapshot(SCHEMAS))
    assert changed == [], f"the brief wrote to {changed}"
    context.user = user


def _text(context) -> str:
    return context.brief.text()


def _section(context, loan: str):
    found = [section for section in context.brief.sections if section.loan.loan == loan]
    assert len(found) == 1, f"{len(found)} sections for loan {loan}:\n{_text(context)}"
    section = found[0]
    lines = section.lines()
    text = _text(context)
    assert "\n".join(lines) in text, text
    return section


def _accession(context, form: str, period: Period = SEPTEMBER) -> str:
    def read(db):
        deal = by_name(db, context.deal)
        assert deal is not None
        return db.scalars(
            select(Filing.accession).where(
                Filing.deal_id == deal.id, Filing.form == form, Filing.period == period.first_day
            )
        ).one()

    return in_session(read)


# ---------------------------------------------------------------------------- what it covers


@then('the brief opens with "{text}"')
def step_brief_opens(context, text):
    assert _text(context).splitlines()[0] == text, _text(context)


@then('the brief has one section, for loan "{loan}", {name}')
def step_one_section(context, loan, name):
    sections = context.brief.sections
    assert [section.loan.loan for section in sections] == [loan], _text(context)
    section = _section(context, loan)
    assert section.loan.property_name == name, section.loan
    assert section.lines()[0].startswith(f"loan {loan}, {name}, "), section.lines()[0]


@then(
    'the brief notes that loan "{returned}" returned to the master servicer on {day} and loan '
    '"{never}" was never transferred'
)
def step_notes_others(context, returned, day, never):
    others = {row.loan: row for row in context.brief.others}
    assert sorted(others) == sorted([returned, never]), _text(context)
    lines = _text(context).splitlines()
    back = other_line(others[returned])
    assert f"- {back}" in lines, _text(context)
    assert f"loan {returned}, " in back and f"returned to the master servicer on {day}" in back
    assert f"{TAPE_FORM} {_accession(context, TAPE_FORM)}" in back, back
    stayed = other_line(others[never])
    assert f"- {stayed}" in lines, _text(context)
    assert f"loan {never}, " in stayed and "never transferred" in stayed, stayed


# ---------------------------------------------------------------------------- a loan's section


def _cites_tape(fact, tape: str) -> None:
    cited = fact.citation
    assert cited is not None and (cited.form, cited.accession) == (TAPE_FORM, tape), fact
    assert cited.tape_field in SCHEDULE_AL_TAGS, fact


def _cites_sentence(context, fact, report: str) -> None:
    cited = fact.citation
    assert cited is not None and (cited.form, cited.accession) == (COMMENT_FORM, report), fact
    sentences = context.comment.sentences
    assert cited.sentence is not None and 1 <= cited.sentence <= len(sentences), cited
    assert cited.quote == sentences[cited.sentence - 1], cited


def _cited(context, fact) -> None:
    """A stated fact cites the September tape's field or a sentence of the September 10-D."""
    if fact.from_tape:
        _cites_tape(fact, _accession(context, TAPE_FORM))
    else:
        _cites_sentence(context, fact, _accession(context, COMMENT_FORM))


def _item(section, heading: str):
    found = [item for item in section.items if item.heading == heading]
    assert len(found) == 1, (heading, [item.heading for item in section.items])
    return found[0]


def _names(item) -> set[str]:
    return {fact.name for fact in item.stated}


@then(
    'loan "{loan}"\'s section gives, each with its citation, the transfer date and reason, the '
    "payment status, the latest DSCR and occupancy with their dates, the servicer's stated "
    "plan, the borrower's request, the advances outstanding and the months of history the "
    "panel holds"
)
def step_section_gives(context, loan):
    section = _section(context, loan)
    lines = section.lines()

    def shown(item):
        for fact in item.stated:
            _cited(context, fact)
            assert f"- {fact.text()}" in lines, (fact.text(), lines)
        return _names(item)

    transfer = shown(_item(section, "Transfer date and reason"))
    assert {"transfer_date", "transfer_reason"} <= transfer, transfer
    assert "tape.special_servicer_transfer_date" in transfer, transfer
    assert "tape.payment_status" in shown(_item(section, "Payment status"))
    performance = shown(_item(section, "DSCR and occupancy, with their dates"))
    assert {"dscr", "dscr_as_of", "occupancy", "occupancy_as_of"} <= performance, performance
    plan = shown(_item(section, "The servicer's stated plan"))
    assert "tape.workout_strategy" in plan, plan
    request = shown(_item(section, "The borrower's request"))
    assert {"borrower_request", "request_status"} <= request, request
    # The tape reports no advances for the loan: the section says so, citing the fields the
    # tape left empty, and states no amount.
    advances = _item(section, "Advances outstanding")
    assert advances.stated == [], advances.stated
    tags = [spec.tag for spec in ASSET_TAGS if spec.topic == "advances"]
    tape = _accession(context, TAPE_FORM)
    held = [line for line in advances.lines() if line.startswith("- advances: ")]
    assert held == [f"- {line}" for line in advances.not_reported], advances.lines()
    assert len(held) == 1, held
    assert f"{TAPE_FORM} {tape}: {', '.join(tags)}" in held[0], held[0]
    # The months, each as asking gives it, citing its own tape and the fields it read.
    months = in_session(lambda db: history(db, context.user, deal=context.deal, loan=loan))
    assert section.history == months.months, (section.history, months.text())
    assert [month.period for month in section.history] == [SEPTEMBER.label], section.history
    for month in section.history:
        assert month.citation.accession == _accession(
            context, TAPE_FORM, Period.parse(month.period)
        )
        line = f"- {month_line(month)}"
        assert line in lines, lines
        assert line.endswith(f": {', '.join(value.tape_field for value in month.values)})"), line


# A citation as a line gives it: an ABS-EE and the field (or fields) read, or a 10-D and the
# number of the comment's sentence.
_ACCESSION = r"\d{10}-\d{2}-\d{6}"
_TAGS = r"[a-zA-Z]+(?:, [a-zA-Z]+)*"
CITATION = re.compile(
    rf"{TAPE_FORM} (?P<tape>{_ACCESSION})(?:, (?P<field>[a-zA-Z]+)\)|: (?P<fields>{_TAGS})\))"
    rf"|{COMMENT_FORM} (?P<report>{_ACCESSION}), sentence (?P<sentence>\d+)"
)
# The lines that state no value, and so cite nothing: a field awaiting review whose comment has
# no sentence on it, and a topic the panel holds nothing on that no tape field reports.
NO_VALUE = re.compile(
    rf"- the [a-z ]+ is awaiting human review(?:; {re.escape(NO_SENTENCE)})?$"
    rf"|- [a-zA-Z ]+: {NOT_IN_DATA}; the panel holds none for this loan$"
)


@then("every line of the section that states a value cites an ABS-EE field or a 10-D sentence")
def step_every_line_cites(context):
    """A line with a value cites a Schedule AL field of the month's ABS-EE, or a sentence of
    the comment in the month's 10-D, quoted as the comment has it. A line without a citation is
    a heading or the list of what the panel does not hold, and states no value."""
    section = _section(context, context.loan)
    tape = _accession(context, TAPE_FORM)
    report = _accession(context, COMMENT_FORM)
    sentences = context.comment.sentences
    checked = 0
    for line in section.lines():
        if line.endswith(":") or line.startswith("Not in the panel:"):
            assert not re.search(r"\d", line), f"a line states a value uncited: {line}"
            continue
        found = list(CITATION.finditer(line))
        if not found:
            assert NO_VALUE.match(line), f"no citation in: {line}"
            continue
        checked += 1
        for cited in found:
            if cited.group("tape"):
                assert cited.group("tape") == tape, line
                named = (cited.group("field") or cited.group("fields")).split(", ")
                assert set(named) <= SCHEDULE_AL_TAGS | ROSTER_TAGS, line
            elif cited.group("report"):
                number = int(cited.group("sentence"))
                assert cited.group("report") == report, line
                quoted = f'sentence {number}: "{sentences[number - 1]}"'
                assert quoted in line or (UNREVIEWED in line and f"sentence {number})" in line), (
                    line
                )
    assert checked >= 10, section.lines()


# ---------------------------------------------------------------------------- the gaps


@then('loan "{loan}"\'s section ends with "Not in the panel:" followed by "{first}" and "{second}"')
def step_ends_with_gaps(context, loan, first, second):
    last = _section(context, loan).lines()[-1]
    assert last.startswith("Not in the panel: "), last
    listed = last.removeprefix("Not in the panel: ").rstrip(".").split(", ")
    assert listed[:2] == [first, second], listed
    assert listed == list(NOT_IN_PANEL), listed


_FIGURE = re.compile(r"\$?\d[\d,]*(?:\.\d+)?[x%]?")


@then("no figure in the brief is computed from others")
def step_no_computed_figure(context):
    """Every figure the brief gives is one asking gives, as written there: in the roster, a
    loan's facts or its history (with their citations), or the brief's own date."""

    def sources(db):
        found = loans(db, context.user, deal=context.deal)
        texts = [found.text()]
        for row in found.loans:
            texts.append(loan_facts(db, context.user, deal=context.deal, loan=row.loan).text())
            texts.append(history(db, context.user, deal=context.deal, loan=row.loan).text())
        return "\n".join(texts)

    given = in_session(sources) + f"\n{context.brief.as_of.isoformat()}"
    figures = set(_FIGURE.findall(_text(context)))
    computed = sorted(figure for figure in figures if figure not in given)
    assert computed == [], computed
    assert any(figure.startswith("$") for figure in figures), figures


# ---------------------------------------------------------------------------- awaiting review


def _waiting_line(context, label: str) -> str:
    section = _section(context, context.loan)
    head = f"- the {label} is awaiting human review"
    found = [line for line in section.lines() if line.startswith(head)]
    assert len(found) == 1, section.lines()
    return found[0]


@then('loan "{loan}"\'s section says the {label} is awaiting human review')
def step_section_awaiting(context, loan, label):
    section = _section(context, loan)
    plan = _item(section, "The servicer's stated plan")
    assert plan.answer is not None and plan.answer.awaiting_review == [label], plan.answer
    _waiting_line(context, label)
    # The model's reading is stated nowhere: no line on the field gives it, and no fact has it.
    field = next(fact.name for fact in plan.answer.unreviewed if fact.label == label)
    model_value = context.comment.fields[field].model_value
    assert model_value, "the model gave no reading to keep out"
    on_it = [line for line in _text(context).splitlines() if label in line]
    assert on_it and not any(model_value.lower() in line.lower() for line in on_it), on_it
    stated = [fact for item in section.items for fact in item.stated]
    assert all(fact.value != model_value for fact in stated), stated
    context.waiting_label = label


@then('it quotes the comment\'s sentence on it, labelled "{label}"')
def step_it_quotes(context, label):
    assert label == UNREVIEWED, label
    line = _waiting_line(context, context.waiting_label)
    section = _section(context, context.loan)
    plan = _item(section, "The servicer's stated plan")
    [fact] = plan.answer.unreviewed
    cited = fact.citation
    _cites_sentence(context, fact, _accession(context, COMMENT_FORM))
    assert line.endswith(
        f'{label}: "{cited.quote}" ({COMMENT_FORM} {cited.accession}, sentence {cited.sentence})'
    ), line


@then("it gives the tape's {label} \"{value}\", labelled as the tape's")
def step_it_gives_tape(context, label, value):
    section = _section(context, context.loan)
    plan = _item(section, "The servicer's stated plan")
    found = [
        fact for fact in plan.stated if fact.from_tape and fact.label == f"{label} on the tape"
    ]
    assert len(found) == 1, plan.stated
    fact = found[0]
    assert fact.value == value, fact
    _cites_tape(fact, _accession(context, TAPE_FORM))
    assert f"- {fact.text()}" in section.lines(), section.lines()
    # No value from the comment is stated on the topic while the field waits.
    assert all(fact.from_tape for fact in plan.stated), plan.stated
