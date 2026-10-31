"""The committed OpenAPI schema (web/openapi.json) is the API's own, byte for byte (grill log Q12).

The web client is generated from that file, so a route changed without regenerating it would
leave the dashboard calling an API that no longer exists. This test and the CI step
`tools/export_openapi.py --check` both fail then, naming the command that fixes it.
"""

import importlib.util
import json
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


export = load("export_openapi")


def test_the_committed_schema_is_the_apps():
    committed = export.SCHEMA.read_text(encoding="utf-8")
    assert committed == export.current(), export.stale_message(export.SCHEMA)


def test_the_schema_is_written_the_same_way_every_time():
    assert export.current() == export.current()
    assert export.render({"b": [2, 1], "a": {"d": "é", "c": None}}) == (
        '{\n  "a": {\n    "c": null,\n    "d": "é"\n  },\n  "b": [\n    2,\n    1\n  ]\n}\n'
    )


def test_the_schema_names_the_dashboards_routes_and_types():
    schema = json.loads(export.current())
    assert schema["info"] == {"title": "CMBS surveillance", "version": "0.1.0"}
    book = schema["paths"]["/dashboard/book"]["get"]
    assert book["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/BookPage"
    }
    loan = schema["paths"]["/dashboard/loan"]["get"]
    assert [p["name"] for p in loan["parameters"]] == [
        "deal",
        "loan",
        "month",
        "as_of",
        "x-cmbs-user",
    ]
    # Money travels as a decimal string, never a float, so no digit is lost on the way.
    row = schema["components"]["schemas"]["BookRow"]["properties"]
    assert row["exposure"] == {"title": "Exposure", "type": "string"}


def test_the_message_says_how_to_regenerate(tmp_path):
    message = export.stale_message(tmp_path / "openapi.json")
    assert "`uv run python tools/export_openapi.py`" in message
    assert "`docker compose run --rm web npm run generate`" in message


def test_check_fails_on_a_stale_or_missing_file_and_passes_on_a_current_one(tmp_path, capsys):
    path = tmp_path / "openapi.json"
    assert export.main(["--check", "--path", str(path)]) == 1
    assert "does not match the API's OpenAPI schema" in capsys.readouterr().err
    path.write_text("{}\n", encoding="utf-8")
    assert export.main(["--check", "--path", str(path)]) == 1
    assert export.main(["--path", str(path)]) == 0
    assert path.read_text(encoding="utf-8") == export.current()
    assert export.main(["--check", "--path", str(path)]) == 0
    assert capsys.readouterr().out.endswith("is up to date\n")


def test_check_reads_the_committed_schema_by_default(capsys):
    assert Path(__file__).resolve().parents[1] / "web" / "openapi.json" == export.SCHEMA
    assert export.main(["--check"]) == 0
    assert capsys.readouterr().out == f"{export.SCHEMA} is up to date\n"
