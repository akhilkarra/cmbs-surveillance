"""Approval status of feature files, and the manifest of approved ones.

Every feature file carries exactly one status tag on its Feature line: @draft or @approved.
Scenarios inherit it; status tags on individual scenarios are not allowed. A draft that
revises an approved feature is a sibling file named <name>.revision.feature.
"""

import hashlib
from pathlib import Path

FEATURES_DIR = Path("features")
MANIFEST = FEATURES_DIR / "APPROVED.sha256"
DRAFT, APPROVED = "@draft", "@approved"
STATUS_TAGS = (DRAFT, APPROVED)
REVISION_SUFFIX = ".revision.feature"
HEADER = (
    "# SHA-256 of every feature file tagged @approved. Updated only by tools/approve_feature.py.\n"
    "# CI fails if an approved file does not match this list.\n"
)


def _tags(line: str) -> list[str]:
    return [token for token in line.split("#", 1)[0].split() if token.startswith("@")]


def feature_tags(text: str) -> list[str]:
    """Tags on the lines before the Feature: line."""
    tags = []
    for line in text.splitlines():
        if line.strip().startswith("Feature:"):
            return tags
        tags += _tags(line)
    return tags


def is_approved(text: str) -> bool:
    return APPROVED in feature_tags(text)


def status_problems(name: str, text: str) -> list[str]:
    found = [tag for tag in feature_tags(text) if tag in STATUS_TAGS]
    problems = []
    if len(found) != 1:
        problems.append(f"{name}: needs exactly one of {DRAFT} or {APPROVED} on the Feature line")
    after_feature = text.split("Feature:", 1)[1] if "Feature:" in text else ""
    for line in after_feature.splitlines():
        if any(tag in STATUS_TAGS for tag in _tags(line)):
            problems.append(f"{name}: status tags belong on the Feature line, not on scenarios")
            break
    if name.endswith(REVISION_SUFFIX) and APPROVED in found:
        problems.append(f"{name}: a revision file is always a draft")
    return problems


def feature_files(root: Path = FEATURES_DIR) -> list[Path]:
    return sorted(root.rglob("*.feature"))


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def approved_hashes(root: Path = FEATURES_DIR) -> dict[str, str]:
    return {
        path.as_posix(): file_hash(path)
        for path in feature_files(root)
        if is_approved(path.read_text())
    }


def read_manifest(manifest: Path = MANIFEST) -> dict[str, str]:
    entries = {}
    if manifest.is_file():
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
        problems.append(f"{name}: in the manifest but not an approved feature file")
    for name in sorted(actual.keys() - recorded.keys()):
        problems.append(f"{name}: tagged {APPROVED} but not in the manifest")
    for name in sorted(recorded.keys() & actual.keys()):
        if recorded[name] != actual[name]:
            problems.append(f"{name}: changed since it was approved")
    return problems
