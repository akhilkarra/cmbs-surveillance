from cmbs.db.models import Base
from cmbs.db.registry import load_all_tables


def test_every_feature_tables_module_is_loaded():
    names = {module.__name__ for module in load_all_tables()}
    assert {
        "cmbs.access.tables",
        "cmbs.audit.tables",
        "cmbs.settings.tables",
        "cmbs.runs.tables",
        "cmbs.deals.tables",
        "cmbs.checks.tables",
    } <= names
    assert all(".migrations" not in name for name in names)
    assert {"app.user", "app.audit_entry", "silver.deal"} <= set(Base.metadata.tables)
