"""The dashboard's closed vocabularies reach the API schema as enums, so the generated web client
types them as unions rather than plain strings."""

import pytest

from cmbs.alerts.rules import Severity
from cmbs.api.app import create_app
from cmbs.dashboard.service import severity_name


def test_every_severity_has_its_name():
    assert [severity_name(severity) for severity in Severity] == ["high", "medium", "low"]
    assert severity_name("medium") == "medium"


def test_an_unknown_severity_is_refused():
    with pytest.raises(ValueError, match="'urgent' is not a valid Severity"):
        severity_name("urgent")


@pytest.mark.parametrize(
    ("schema", "field", "values"),
    [
        ("BookRow", "severity", ["high", "medium", "low"]),
        ("LoanAlert", "severity", ["high", "medium", "low"]),
        ("TapeValue", "kind", ["money", "date", "ratio", "text"]),
        ("Check", "outcome", ["passed", "failed", "not checked", "decided"]),
    ],
)
def test_the_schema_names_each_value(schema, field, values):
    schemas = create_app().openapi()["components"]["schemas"]
    assert schemas[schema]["properties"][field]["enum"] == values
