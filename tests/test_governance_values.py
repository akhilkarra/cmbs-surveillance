"""The pure rules of the settings ledger and the audit log: no database."""

import datetime as dt
from decimal import Decimal

import pytest

from cmbs.access.roles import Role
from cmbs.access.service import RoleUnchanged, SelfRoleChange, role_change_problem
from cmbs.audit.service import BadRange, day_bounds, entry_view, loan_key
from cmbs.audit.tables import AuditEntry
from cmbs.settings.routes import proposal_view
from cmbs.settings.tables import SettingsProposal
from cmbs.settings.values import (
    FIELDS,
    LABELS,
    SHOWN,
    Approved,
    InvalidSetting,
    Settings,
    apply_changes,
    as_json,
    changed_fields,
    describe_changes,
    format_field,
    needs_quality_report,
    setting_lines,
    stale_fields,
)

BASE = Settings()


def test_every_field_has_a_label_and_a_line():
    assert set(LABELS) == set(FIELDS)
    assert sorted(field for shown in SHOWN for field in shown.fields) == sorted(FIELDS)


def test_applying_a_change_returns_new_settings_and_leaves_the_rest():
    changed = apply_changes(BASE, {"dscr_alert_threshold": "1.05"})
    assert changed.dscr_alert_threshold == Decimal("1.05")
    assert changed.model_dump(exclude={"dscr_alert_threshold"}) == BASE.model_dump(
        exclude={"dscr_alert_threshold"}
    )
    assert BASE.dscr_alert_threshold == Decimal("1.00")


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"dscr_threshold": "1.05"}, "there is no setting named 'dscr_threshold'"),
        ({"dscr_alert_threshold": "1.00"}, "the proposal changes nothing"),
        ({"dscr_alert_threshold": "1.0"}, "the proposal changes nothing"),
        ({}, "the proposal changes nothing"),
        ({"dscr_alert_threshold": "0"}, "the DSCR alert threshold must be above 0"),
        ({"dscr_alert_threshold": "-1"}, "the DSCR alert threshold must be above 0"),
        ({"dscr_alert_threshold": "abc"}, "the DSCR alert threshold cannot be 'abc'"),
        (
            {"abstention_low": "0.70"},
            "the abstention band must rise from low to high within 0 to 1",
        ),
        (
            {"abstention_low": "0.80"},
            "the abstention band must rise from low to high within 0 to 1",
        ),
        (
            {"abstention_low": "-0.01"},
            "the abstention band must rise from low to high within 0 to 1",
        ),
        (
            {"abstention_high": "1.01"},
            "the abstention band must rise from low to high within 0 to 1",
        ),
        ({"transfer_date_tolerance_periods": -1}, "the transfer-date tolerance cannot be negative"),
        ({"model": "  "}, "the model must be named"),
        ({"prompt_version": 0}, "the prompt version must be 1 or more"),
    ],
)
def test_impossible_changes_are_refused(changes, message):
    with pytest.raises(InvalidSetting) as refused:
        apply_changes(BASE, changes)
    assert str(refused.value) == message


@pytest.mark.parametrize(
    "changes",
    [
        {"abstention_low": "0", "abstention_high": "1"},
        {"abstention_low": "0.69"},
        {"transfer_date_tolerance_periods": 0},
        {"prompt_version": 1},
        {"dscr_alert_threshold": "0.01"},
    ],
)
def test_changes_at_the_edges_are_accepted(changes):
    assert apply_changes(BASE, changes) != BASE


def test_a_band_moved_in_one_change_is_checked_as_a_whole():
    moved = apply_changes(BASE, {"abstention_low": "0.75", "abstention_high": "0.90"})
    assert (moved.abstention_low, moved.abstention_high) == (Decimal("0.75"), Decimal("0.90"))


def test_changed_fields_in_declaration_order():
    after = apply_changes(BASE, {"prompt_version": 4, "dscr_alert_threshold": "1.05"})
    assert changed_fields(BASE, after) == ("dscr_alert_threshold", "prompt_version")
    assert changed_fields(BASE, BASE) == ()


def test_stored_values_are_json_safe():
    after = apply_changes(BASE, {"dscr_alert_threshold": "1.05", "prompt_version": 4})
    assert as_json(after, ("dscr_alert_threshold", "prompt_version")) == {
        "dscr_alert_threshold": "1.05",
        "prompt_version": 4,
    }


@pytest.mark.parametrize(
    ("fields", "needed"),
    [
        (("model",), True),
        (("prompt_version",), True),
        (("dscr_alert_threshold", "prompt_version"), True),
        (("dscr_alert_threshold",), False),
        (("abstention_low", "abstention_high"), False),
        (("zero_data_retention",), False),
        ((), False),
    ],
)
def test_model_and_prompt_changes_need_a_quality_report(fields, needed):
    assert needs_quality_report(fields) is needed


@pytest.mark.parametrize(
    ("field", "value", "text"),
    [
        ("dscr_alert_threshold", "1.05", "1.05x"),
        ("dscr_alert_threshold", "1", "1.00x"),
        ("dscr_alert_threshold", "1.125", "1.125x"),
        ("dscr_alert_threshold", "1.1", "1.10x"),
        ("abstention_low", "0.3", "0.30"),
        ("abstention_high", "0.725", "0.725"),
        ("transfer_date_tolerance_periods", 1, "1 period"),
        ("transfer_date_tolerance_periods", 2, "2 periods"),
        ("transfer_date_tolerance_periods", 0, "0 periods"),
        ("zero_data_retention", True, "on"),
        ("zero_data_retention", False, "off"),
        ("model", "jev-1.14.0", "jev-1.14.0"),
        ("prompt_version", 4, "4"),
    ],
)
def test_values_are_formatted_as_the_panel_shows_them(field, value, text):
    assert format_field(field, value) == text


def test_a_change_is_described_from_and_to():
    before = {"dscr_alert_threshold": "1.00", "prompt_version": 3}
    after = {"dscr_alert_threshold": "1.05", "prompt_version": 4}
    assert describe_changes(before, after) == (
        "DSCR alert threshold 1.00x to 1.05x; prompt version 3 to 4"
    )


def test_a_proposal_is_stale_only_where_its_fields_have_moved():
    latest = apply_changes(BASE, {"dscr_alert_threshold": "1.10"})
    assert stale_fields({"dscr_alert_threshold": "1.00"}, latest) == [
        ("dscr_alert_threshold", "1.00", "1.10")
    ]
    assert stale_fields({"dscr_alert_threshold": "1.10"}, latest) == []
    assert stale_fields({"dscr_alert_threshold": "1.1"}, latest) == []
    assert stale_fields({"prompt_version": 3, "model": "jev-1.14.0"}, latest) == []
    assert stale_fields({"prompt_version": 2}, latest) == [("prompt_version", 2, 3)]


AT = dt.datetime(2026, 9, 15, tzinfo=dt.UTC)


def approved(version: int, values: Settings, day: int, by: str = "quinn") -> Approved:
    return Approved(
        version=version,
        values=values,
        proposed_by=by,
        approved_by="rae",
        approved_at=AT + dt.timedelta(days=day),
    )


def test_each_setting_shows_the_version_that_set_it_and_its_history():
    v7 = approved(7, BASE, 0)
    v8 = approved(8, apply_changes(BASE, {"dscr_alert_threshold": "1.05"}), 16, by="morgan")
    v9 = approved(9, apply_changes(v8.values, {"prompt_version": 4}), 20)
    lines = {line.label: line for line in setting_lines([v7, v8, v9])}
    dscr = lines["DSCR alert threshold"]
    assert (dscr.value, dscr.since.version, dscr.since.proposed_by) == ("1.05x", 8, "morgan")
    assert [(c.version, c.value) for c in dscr.history] == [(8, "1.05x"), (7, "1.00x")]
    assert dscr.since.approved_at == AT + dt.timedelta(days=16)
    band = lines["abstention band"]
    assert (band.value, [c.version for c in band.history]) == ("0.30 to 0.70", [7])
    assert [(c.version, c.value) for c in lines["prompt version"].history] == [(9, "4"), (7, "3")]
    assert lines["transfer-date tolerance"].value == "2 periods"
    assert lines["model"].value == "jev-1.14.0"
    assert lines["zero data retention"].value == "off"


def test_a_value_set_back_counts_as_a_change():
    v7 = approved(7, BASE, 0)
    v8 = approved(8, apply_changes(BASE, {"dscr_alert_threshold": "1.05"}), 1)
    v9 = approved(9, BASE, 2)
    (dscr,) = [line for line in setting_lines([v7, v8, v9]) if line.label == "DSCR alert threshold"]
    assert [c.version for c in dscr.history] == [9, 8, 7]


def test_lines_need_a_version():
    with pytest.raises(ValueError, match="no approved versions"):
        setting_lines([])


def test_an_entry_is_shown_with_every_field():
    entry = AuditEntry(
        id=5,
        at=AT,
        user_name="alex",
        role="analyst",
        run_id=42,
        action="override value",
        subject="loan",
        subject_key="Fixture Trust 2026-F1/2",
        field="occupancy",
        before="0.91",
        after="0.88",
        reason="operating statement",
        outcome="done",
        detail={"sentence": 3},
    )
    assert entry_view(entry) == {
        "id": 5,
        "at": "2026-09-15T00:00:00+00:00",
        "user": "alex",
        "role": "analyst",
        "run": 42,
        "action": "override value",
        "subject": "loan",
        "subject_key": "Fixture Trust 2026-F1/2",
        "field": "occupancy",
        "before": "0.91",
        "after": "0.88",
        "reason": "operating statement",
        "outcome": "done",
        "detail": {"sentence": 3},
    }


def test_a_proposal_is_shown_with_its_decision():
    proposal = SettingsProposal(
        id=3,
        status="rejected",
        proposed_by="quinn",
        proposer_role="quant",
        proposed_at=AT,
        changes={"abstention_low": "0.40"},
        proposed_from={"abstention_low": "0.30"},
        reason="tighter band",
        quality_report=None,
        decided_by="rae",
        decider_role="risk",
        decided_at=AT + dt.timedelta(days=1),
        decision_reason="needs a quality report first",
        version=None,
    )
    assert proposal_view(proposal) == {
        "id": 3,
        "status": "rejected",
        "proposed_by": "quinn",
        "proposer_role": "quant",
        "proposed_at": "2026-09-15T00:00:00+00:00",
        "changes": {"abstention_low": "0.40"},
        "proposed_from": {"abstention_low": "0.30"},
        "reason": "tighter band",
        "quality_report": None,
        "decided_by": "rae",
        "decider_role": "risk",
        "decided_at": "2026-09-16T00:00:00+00:00",
        "decision_reason": "needs a quality report first",
        "version": None,
    }
    proposal.decided_at = None
    assert proposal_view(proposal)["decided_at"] is None


def test_loan_keys_join_deal_and_loan():
    assert loan_key("Fixture Trust 2026-F1", "2") == "Fixture Trust 2026-F1/2"


def test_day_bounds_cover_whole_utc_days():
    since, until = day_bounds(dt.date(2026, 9, 1), dt.date(2026, 9, 30))
    assert since == dt.datetime(2026, 9, 1, tzinfo=dt.UTC)
    assert until == dt.datetime(2026, 10, 1, tzinfo=dt.UTC)
    assert day_bounds(None, None) == (None, None)
    assert day_bounds(dt.date(2026, 9, 1), dt.date(2026, 9, 1))[1] == dt.datetime(
        2026, 9, 2, tzinfo=dt.UTC
    )


def test_a_range_cannot_end_before_it_starts():
    with pytest.raises(BadRange) as refused:
        day_bounds(dt.date(2026, 9, 30), dt.date(2026, 9, 1))
    assert str(refused.value) == "the range ends on 2026-09-01, before it starts on 2026-09-30"


QUANT = frozenset({Role.QUANT})


@pytest.mark.parametrize(
    ("by", "role", "grant", "error", "message"),
    [
        ("olly", Role.RISK, True, None, None),
        ("olly", Role.QUANT, False, None, None),
        ("quinn", Role.RISK, True, SelfRoleChange, "nobody changes their own roles"),
        ("quinn", Role.QUANT, False, SelfRoleChange, "nobody changes their own roles"),
        ("olly", Role.QUANT, True, RoleUnchanged, "quinn already holds the role quant"),
        ("olly", Role.RISK, False, RoleUnchanged, "quinn does not hold the role risk"),
    ],
)
def test_role_change_rules(by, role, grant, error, message):
    problem = role_change_problem(by=by, user="quinn", role=role, current=QUANT, grant=grant)
    if error is None:
        assert problem is None
    else:
        assert type(problem) is error and str(problem) == message
