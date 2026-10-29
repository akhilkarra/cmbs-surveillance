"""Steps for features/extraction/review_queue.feature and overrides.feature.

The queue's items come from short synthetic comments, extracted with scripted model judgments
on the day each item starts waiting (`tests/test_review_world.py`), and the book of
features/README.md gives the exposure to order them by. Overrides start from loan 2's comment in
the fixture deal's September 2026 10-D, its borrower request auto-accepted on 2026-10-01; the
override is made on 2026-10-06.

Regular expressions, so a quoted argument never runs into the rest of a longer step.
"""

import datetime as dt

from behave import given, then, use_step_matcher, when  # pyright: ignore[reportAttributeAccessIssue]
from sqlalchemy import select

from cmbs import clock
from cmbs.audit.service import LOAN, entries_for_subject, loan_key
from cmbs.domain import money
from cmbs.extraction import fields
from cmbs.extraction.service import comment_view, decide_review, review_item
from cmbs.extraction.tables import ExtractedField
from cmbs.review.service import (
    DECLINE,
    decline,
    declined_reason,
    label_candidates,
    override,
    queue,
    queue_summary,
)
from cmbs.testing.bdd import attempt, in_session
from cmbs.testing.world import FIXTURE_DEAL
from tests.test_review_world import QUEUE, SEPTEMBER, loan_two, open_queue

use_step_matcher("re")

DATE = r"\d{4}-\d{2}-\d{2}"
OVERRIDE_TIME = dt.datetime(2026, 10, 6, 10, tzinfo=dt.UTC)
# An override needs a reason; a step that does not give one, but is not about the reason, uses
# this one.
UNSTATED_REASON = "reason not stated in the scenario"
READER = "pat"


def _day(text: str) -> dt.date:
    return dt.date.fromisoformat(text)


def _morning(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(9), dt.UTC)


# --- The review queue --------------------------------------------------------------------------


@given(rf"these review items are open on (?P<day>{DATE}):")
def step_review_items_open(context, day):
    context.items = in_session(open_queue)
    context.letters = {item_id: letter for letter, item_id in context.items.items()}
    context.today = _day(day)
    clock.set_now(_morning(context.today))
    entries = {entry.item.id: entry for entry in in_session(lambda db: queue(db, READER))}
    for row in context.table:
        letter = row["item"]
        planned = QUEUE[letter]
        entry = entries[context.items[letter]]
        stated = (row["loan"], row["deal"], _day(row["waiting since"]), money(row["exposure"]))
        built = (entry.item.loan, entry.item.deal, entry.waiting_since, entry.exposure)
        assert stated == built, f"item {letter}: the scenario says {stated}, the world {built}"
        assert (planned.loan, planned.deal) == (entry.item.loan, entry.item.deal)
    assert len(entries) == len(context.table.rows), entries


def _read_queue(context, user: str, on: dt.date | None = None) -> None:
    context.queue = attempt(context, lambda: in_session(lambda db: queue(db, user, on=on)))


@when(rf'"(?P<user>[^"]+)" opens the review queue(?: on (?P<day>{DATE}))?')
def step_opens_queue(context, user, day):
    _read_queue(context, user, _day(day) if day else None)
    assert context.refusal is None, f"refused: {context.refusal}"


@then(r"the items appear in the order (?P<order>[A-Z](?:, [A-Z])*)")
def step_items_in_order(context, order):
    shown = [context.letters[entry.item.id] for entry in context.queue]
    assert shown == order.split(", "), shown


def _entry(context, letter: str):
    found = [entry for entry in context.queue if entry.item.id == context.items[letter]]
    assert len(found) == 1, f"item {letter} is not in the queue once: {found}"
    return found[0]


@then(r'item (?P<letter>[A-Z]) is marked "(?P<mark>[^"]+)"')
def step_item_marked(context, letter, mark):
    entry = _entry(context, letter)
    assert entry.overdue, f"item {letter} is not overdue: {entry.mark}"
    assert entry.mark == mark, f"item {letter} is marked {entry.mark!r}"


@then(r"items (?P<first>[A-Z]) and (?P<second>[A-Z]) are not overdue")
def step_items_not_overdue(context, first, second):
    for letter in (first, second):
        entry = _entry(context, letter)
        assert not entry.overdue and not entry.mark.startswith("overdue"), entry.mark


@when(r'"(?P<user>[^"]+)" declines item (?P<letter>[A-Z]) because "(?P<reason>[^"]+)"')
def step_declines(context, user, letter, reason):
    context.deal = QUEUE[letter].deal
    context.decline = (letter, user, reason)
    item_id = context.items[letter]
    attempt(context, lambda: in_session(lambda db: decline(db, user, item_id, reason=reason)))
    assert context.refusal is None, f"refused: {context.refusal}"


@then(r'loan "(?P<loan>[^"]+)"\'s (?P<label>[a-z -]+?) is marked "(?P<status>[^"]+)"')
def step_loan_field_marked(context, loan, label, status):
    spec = fields.by_label(label)
    view = in_session(
        lambda db: comment_view(db, READER, deal=context.deal, loan=loan, period=SEPTEMBER)
    )
    assert view is not None, f"loan {loan} has no extracted comment"
    shown = view.fields[spec.name]
    assert (shown.status, shown.value, shown.sentence) == (status, None, None), shown


@then(r"item (?P<letter>[A-Z]) leaves the queue with the reason recorded")
def step_leaves_queue_with_reason(context, letter):
    declined_letter, user, reason = context.decline
    assert declined_letter == letter
    item_id = context.items[letter]
    for reader in ("alex", "morgan"):
        listed = in_session(lambda db, reader=reader: queue(db, reader))
        assert item_id not in [entry.item.id for entry in listed], f"{reader} still sees it"
    item = in_session(lambda db: review_item(db, READER, item_id))
    assert (item.status, item.decision, item.decided_by) == ("decided", "not determinable", user)
    assert in_session(lambda db: declined_reason(db, READER, item_id)) == reason
    key = loan_key(QUEUE[letter].deal, QUEUE[letter].loan)
    entries = [
        entry
        for entry in in_session(lambda db: entries_for_subject(db, LOAN, key))
        if entry.action == DECLINE
    ]
    assert [(e.user_name, e.after, e.reason) for e in entries] == [
        (user, "not determinable", reason)
    ]


@when(r'"(?P<user>[^"]+)" decides item (?P<letter>[A-Z])')
def step_decides_item(context, user, letter):
    planned = QUEUE[letter]
    item_id = context.items[letter]
    attempt(
        context,
        lambda: in_session(
            lambda db: decide_review(
                db, user, item_id, value=planned.answer, sentence=planned.sentence
            )
        ),
    )
    assert context.refusal is None, f"refused: {context.refusal}"


@then(r'"(?P<user>[^"]+)" no longer sees item (?P<letter>[A-Z]) in the queue')
def step_no_longer_sees(context, user, letter):
    _read_queue(context, user)
    assert context.refusal is None, f"refused: {context.refusal}"
    shown = sorted(context.letters[entry.item.id] for entry in context.queue)
    assert shown == sorted(set(context.items) - {letter}), shown


@when(rf'"(?P<user>[^"]+)" reads the review queue summary on (?P<day>{DATE})')
def step_reads_summary(context, user, day):
    context.summary = attempt(
        context, lambda: in_session(lambda db: queue_summary(db, user, on=_day(day)))
    )
    assert context.refusal is None, f"refused: {context.refusal}"


@then(
    rf"it shows (?P<count>\d+) open items, (?P<overdue>\d+) overdue, and the oldest waiting "
    rf"since (?P<since>{DATE})"
)
def step_summary_shows(context, count, overdue, since):
    summary = context.summary
    found = (summary.open, summary.overdue, summary.oldest_since)
    assert found == (int(count), int(overdue), _day(since)), found


# --- Overrides -----------------------------------------------------------------------------------


def _field(label: str):
    spec = fields.by_label(label)
    view = in_session(
        lambda db: comment_view(db, READER, deal=FIXTURE_DEAL, loan="2", period=SEPTEMBER)
    )
    assert view is not None, "loan 2 has no extracted comment"
    return view.fields[spec.name]


@given(
    r'loan "(?P<loan>[^"]+)"\'s (?P<label>[a-z -]+?) is "(?P<value>[^"]+)", auto-accepted with '
    r"sentence (?P<sentence>\d+) as evidence"
)
def step_auto_accepted(context, loan, label, value, sentence):
    assert (loan, label) == ("2", "borrower request"), "the world scripts loan 2's request"
    in_session(lambda db: loan_two(db, answer=value, answer_p=0.93, sentence=int(sentence)))
    context.deal = FIXTURE_DEAL
    context.evidence_sentence = int(sentence)
    shown = _field(label)
    assert (shown.status, shown.value, shown.sentence) == ("auto-accepted", value, int(sentence))


def _override(context, user, label, value, sentence, reason):
    clock.set_now(OVERRIDE_TIME)
    cited = int(sentence) if sentence else context.evidence_sentence
    spec = fields.by_label(label)
    context.field = spec.name
    context.override = attempt(
        context,
        lambda: in_session(
            lambda db: override(
                db,
                user,
                deal=FIXTURE_DEAL,
                loan="2",
                period=SEPTEMBER,
                field=spec.name,
                value=value,
                sentence=cited,
                reason=reason,
            )
        ),
    )


@when(
    r'"(?P<user>[^"]+)" (?:overrides|tries to override) the (?P<label>[a-z -]+?) to '
    r'"(?P<value>[^"]+)"(?: citing sentence (?P<sentence>\d+))?'
    r'(?: because "(?P<reason>[^"]*)"| (?P<none>with no reason))?'
)
def step_overrides(context, user, label, value, sentence, reason, none):
    if none:
        reason = ""
    _override(context, user, label, value, sentence, UNSTATED_REASON if reason is None else reason)


@given(r'"(?P<user>[^"]+)" overrode the (?P<label>[a-z -]+?) to "(?P<value>[^"]+)"')
def step_overrode(context, user, label, value):
    _override(context, user, label, value, None, UNSTATED_REASON)
    assert context.refusal is None, f"refused: {context.refusal}"


@then(r'the panel shows (?P<label>[a-z -]+?) "(?P<value>[^"]+)", marked "(?P<marked>[^"]+)"')
def step_panel_shows_marked(context, label, value, marked):
    assert context.refusal is None, f"refused: {context.refusal}"
    shown = _field(label)
    assert (shown.display, shown.marked, shown.status) == (value, marked, "overridden"), shown
    assert shown.sentence == context.evidence_sentence, shown


@then(r'the model\'s answer "(?P<value>[^"]+)" is still stored alongside it')
def step_model_answer_kept(context, value):
    shown = _field("borrower request")
    assert shown.model_value == value, shown

    def stored(db):
        return db.scalars(select(ExtractedField).where(ExtractedField.field == context.field)).one()

    row = in_session(stored)
    # The field's own row is untouched: the model's answer, its evidence and the accepted value.
    found = (row.status, row.value, row.sentence, row.model_value, row.model_sentence)
    expected = ("auto-accepted", value, context.evidence_sentence, value, context.evidence_sentence)
    assert found == expected, found


@then(r'the override is refused with "(?P<message>[^"]+)"')
def step_override_refused(context, message):
    assert context.refusal is not None, "the override was made"
    assert str(context.refusal) == message, str(context.refusal)


@then(r'the panel still shows "(?P<value>[^"]+)"')
def step_panel_still_shows(context, value):
    shown = _field("borrower request")
    assert (shown.display, shown.status) == (value, "auto-accepted"), shown


# "When <user> asks <question>" is the ask surface's step (features/steps/ask.py): it keeps the
# composed answer as `context.answer`.


@then(
    r'the answer says "(?P<value>[^"]+)", noting it was set by "(?P<user>[^"]+)" and citing '
    r"sentence (?P<sentence>\d+)"
)
def step_answer_says_overridden(context, value, user, sentence):
    answer = context.answer
    stated = [fact for fact in answer.stated if fact.name == "borrower_request"]
    assert len(stated) == 1, answer.text()
    fact = stated[0]
    assert fact.citation is not None, fact
    found = (fact.value, fact.status, fact.set_by, fact.citation.sentence)
    assert found == (value, "overridden", user, int(sentence)), found
    view = in_session(
        lambda db: comment_view(db, READER, deal=FIXTURE_DEAL, loan="2", period=SEPTEMBER)
    )
    assert view is not None
    assert fact.citation.quote == view.sentences[int(sentence) - 1], fact.citation
    assert fact.text() in answer.text(), answer.text()


@when(r"the next quality report is built")
def step_next_quality_report(context):
    context.candidates = in_session(lambda db: label_candidates(db, "quinn"))


@then(r"the override is listed as a label candidate")
def step_override_is_candidate(context):
    made = context.override
    found = [
        (c.deal, c.loan, c.field, c.value, c.sentence, c.source, c.by) for c in context.candidates
    ]
    expected = (FIXTURE_DEAL, "2", context.field, made.value, made.sentence, "override", "alex")
    assert found == [expected], found


# Leave the default matcher for the step modules loaded after this one.
use_step_matcher("parse")
