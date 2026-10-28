"""Shared helpers for the accepted-feature manifest (used by the accept and check tools)."""

import hashlib
from pathlib import Path

ACCEPTED_DIR = Path("features/accepted")
PROPOSED_DIR = Path("features/proposed")
MANIFEST = ACCEPTED_DIR / "MANIFEST.sha256"
HEADER = (
    "# SHA-256 of every accepted feature file. Updated only by tools/accept_feature.py.\n"
    "# CI fails if a file under features/accepted/ does not match this list.\n"
)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def current_hashes(accepted_dir: Path = ACCEPTED_DIR) -> dict[str, str]:
    return {path.as_posix(): file_hash(path) for path in sorted(accepted_dir.rglob("*.feature"))}


def read_manifest(manifest: Path = MANIFEST) -> dict[str, str]:
    entries = {}
    if not manifest.is_file():
        return entries
    for line in manifest.read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            digest, name = line.split(maxsplit=1)
            entries[name] = digest
    return entries


def write_manifest(hashes: dict[str, str], manifest: Path = MANIFEST) -> None:
    body = "".join(f"{digest}  {name}\n" for name, digest in sorted(hashes.items()))
    manifest.write_text(HEADER + body)


def manifest_problems(recorded: dict[str, str], actual: dict[str, str]) -> list[str]:
    problems = []
    for name in sorted(recorded.keys() - actual.keys()):
        problems.append(f"{name}: listed in the manifest but missing")
    for name in sorted(actual.keys() - recorded.keys()):
        problems.append(f"{name}: accepted file not in the manifest")
    for name in sorted(recorded.keys() & actual.keys()):
        if recorded[name] != actual[name]:
            problems.append(f"{name}: changed since it was accepted")
    return problems
