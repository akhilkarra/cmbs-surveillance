"""Read the local `.env` file into the environment when the `cmbs` command starts.

`.env` is gitignored and holds what differs per machine (`DATABASE_URL`, `SEC_USER_AGENT`,
`TYPESAFE_API_KEY`; `.env.example` lists them). A variable already set in the environment wins,
so a shell export or a CI setting is never overridden. Lines are `NAME=value`, optionally
prefixed with `export`; blank lines and lines starting with `#` are skipped; one pair of
surrounding single or double quotes is removed from the value.
"""

import os
from collections.abc import MutableMapping
from pathlib import Path


def parse(text: str) -> dict[str, str]:
    """The variables a `.env` text sets, in order."""
    found: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").lstrip()
        name, sep, value = line.partition("=")
        name, value = name.strip(), value.strip()
        if not sep or not name:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        found[name] = value
    return found


def load(path: Path, environ: MutableMapping[str, str] = os.environ) -> list[str]:
    """Set every variable of the file that the environment does not already have.

    Returns the names it set. A missing file sets nothing.
    """
    if not path.is_file():
        return []
    values = parse(path.read_text())
    added = [name for name in values if name not in environ]
    for name in added:
        environ[name] = values[name]
    return added
