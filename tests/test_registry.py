from cmbs.db.models import Base
from cmbs.db.registry import all_route_modules, load_all_tables


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


def test_route_modules_expose_a_router():
    modules = all_route_modules()
    assert "cmbs.access.routes" in {module.__name__ for module in modules}
    assert all(hasattr(module, "router") for module in modules)
