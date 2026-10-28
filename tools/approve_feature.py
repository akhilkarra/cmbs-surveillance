"""Approve reviewed feature files. Run by the human reviewer only; the agent is blocked from it.

Usage:
  uv run python tools/approve_feature.py features/<group>/<name>.feature [...]

A draft is approved in place: its @draft tag becomes @approved. A revision draft
(<name>.revision.feature) replaces the approved <name>.feature and is then removed.
The manifest is refreshed and everything is staged. Commit with an Approved-by trailer,
which CI requires:
  git commit -m "Approve ..." --trailer "Approved-by: <your name>"
"""

import subprocess
import sys
from pathlib import Path

from feature_status import (
    APPROVED,
    DRAFT,
    MANIFEST,
    REVISION_SUFFIX,
    approved_hashes,
    feature_tags,
    is_approved,
    write_manifest,
)


def approve(path: Path) -> Path:
    text = path.read_text()
    if DRAFT not in feature_tags(text):
        raise SystemExit(f"{path}: not tagged {DRAFT}")
    feature_line = text.index("Feature:")
    approved_text = text[:feature_line].replace(DRAFT, APPROVED, 1) + text[feature_line:]
    if path.name.endswith(REVISION_SUFFIX):
        target = path.with_name(path.name.removesuffix(REVISION_SUFFIX) + ".feature")
        if not target.is_file() or not is_approved(target.read_text()):
            raise SystemExit(f"{path}: revises {target}, which is not an approved feature")
        target.write_text(approved_text)
        subprocess.run(["git", "rm", "-q", "-f", str(path)], check=True)
    else:
        target = path
        target.write_text(approved_text)
    subprocess.run(["git", "add", str(target)], check=True)
    return target


def main(paths: list[str]) -> int:
    if not paths:
        print(__doc__)
        return 2
    for name in paths:
        print(f"approved {approve(Path(name))}")
    write_manifest(approved_hashes())
    subprocess.run(["git", "add", str(MANIFEST)], check=True)
    print('Now commit with: git commit -m "Approve ..." --trailer "Approved-by: <your name>"')
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
