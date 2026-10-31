"""Write the API's OpenAPI schema to web/openapi.json, the contract the web client is built from.

The schema comes from `cmbs.api.app.create_app()` and is written deterministically (sorted keys,
two-space indent, one trailing newline), so the same code always gives the same bytes. It is
committed; orval generates the dashboard's client from it (web/orval.config.ts), and CI fails
when either has drifted (grill log Q12).

  uv run python tools/export_openapi.py           # rewrite web/openapi.json
  uv run python tools/export_openapi.py --check   # fail if web/openapi.json is out of date

After rewriting it, regenerate the client in the web container:

  docker compose run --rm web npm run generate
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCHEMA = Path(__file__).resolve().parents[1] / "web" / "openapi.json"
REGENERATE = "uv run python tools/export_openapi.py"


def render(schema: dict[str, Any]) -> str:
    """The schema as committed: sorted keys, two-space indent, UTF-8 kept readable."""
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def current() -> str:
    """The schema the application serves now."""
    from cmbs.api.app import create_app

    return render(create_app().openapi())


def stale_message(path: Path) -> str:
    return (
        f"{path} does not match the API's OpenAPI schema. Regenerate it with `{REGENERATE}`, "
        "then the web client with `docker compose run --rm web npm run generate`, "
        "and commit both."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write or check web/openapi.json.")
    parser.add_argument("--check", action="store_true", help="fail if the file is out of date")
    parser.add_argument("--path", type=Path, default=SCHEMA, help="where the schema lives")
    args = parser.parse_args(argv)
    text = current()
    if args.check:
        committed = args.path.read_text(encoding="utf-8") if args.path.is_file() else None
        if committed != text:
            print(stale_message(args.path), file=sys.stderr)
            return 1
        print(f"{args.path} is up to date")
        return 0
    args.path.write_text(text, encoding="utf-8")
    print(f"wrote {args.path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
