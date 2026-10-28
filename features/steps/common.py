"""Steps shared by more than one feature. Feature-specific steps live in their own modules.

Before adding a step to a feature module, search here: behave refuses two steps with the same
text, and the run fails for every feature.
"""

from behave import given, then  # pyright: ignore[reportAttributeAccessIssue]

from cmbs.access.roles import Role
from cmbs.access.service import ensure_user
from cmbs.testing.bdd import data_snapshot, in_session


@given("these users and roles:")
def step_users_and_roles(context):
    def create(db):
        for row in context.table:
            roles = {Role(name.strip()) for name in row["roles"].split(",")}
            ensure_user(db, row["user"], roles)

    in_session(create)


@then('the action is refused with "{message}"')
def step_refused_with(context, message):
    assert context.refusal is not None, "the action was not refused"
    assert str(context.refusal) == message, f"refused with {str(context.refusal)!r}"


@then('the report says "{text}"')
def step_report_says(context, text):
    report = getattr(context, "report", None)
    assert report is not None, "no report was produced"
    assert text in report, f"{text!r} not in report:\n{report}"


@then("no stored data changes")
def step_no_stored_data_changes(context):
    assert context.snapshot is not None, "no When step ran before this check"
    after = data_snapshot()
    changed = sorted(k for k in after if after[k] != context.snapshot.get(k))
    assert not changed, f"stored data changed in {changed}"


@then('loan "{loan}" is flagged "{message}"')
def step_loan_flagged(context, loan, message):
    """Steps that name a deal set `context.deal` to its name; flags are read for that deal."""
    from cmbs.checks.service import flags_for
    from cmbs.deals.service import by_name

    def read(db):
        deal = by_name(db, context.deal)
        assert deal is not None, f"no deal named {context.deal!r}"
        return [flag.message for flag in flags_for(db, deal_id=deal.id, loan=loan)]

    messages = in_session(read)
    assert message in messages, f"loan {loan} flags: {messages}"
