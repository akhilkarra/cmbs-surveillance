"""CI check for feature approval.

1. Every feature file has exactly one status tag (@draft or @approved) on its Feature line.
2. The approved files match features/APPROVED.sha256.
3. With --range BASE..HEAD, every commit that changes an approved feature file, or tags one
   @approved, carries an `Approved-by:` trailer.
"""

import argparse
import subprocess
import sys

from feature_status import (
    approved_hashes,
    feature_files,
    is_approved,
    manifest_problems,
    read_manifest,
    status_problems,
)

ZERO_SHA = "0" * 40
FEATURE_GLOB = ":(glob)features/**/*.feature"


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def _version(rev: str, path: str) -> str:
    result = subprocess.run(["git", "show", f"{rev}:{path}"], capture_output=True, text=True)
    return result.stdout if result.returncode == 0 else ""


def unapproved_commits(rev_range: str) -> list[str]:
    base, _, head = rev_range.partition("..")
    if not base or base == ZERO_SHA:
        rev_range = head  # first push of a branch: check its whole history
    missing = []
    for sha in _git("log", "--format=%H", rev_range, "--", FEATURE_GLOB).split():
        touched = _git("diff-tree", "--no-commit-id", "--name-only", "-r", "--root", sha, "--")
        needs_approval = any(
            is_approved(_version(sha, path)) or is_approved(_version(f"{sha}^", path))
            for path in touched.split()
            if path.endswith(".feature")
        )
        trailer = _git("log", "-1", "--format=%(trailers:key=Approved-by,valueonly)", sha)
        if needs_approval and not trailer.strip():
            missing.append(sha)
    return missing


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--range", dest="rev_range")
    args = parser.parse_args(argv)
    problems = []
    for path in feature_files():
        problems += status_problems(path.as_posix(), path.read_text())
    problems += manifest_problems(read_manifest(), approved_hashes())
    if args.rev_range:
        problems += [
            f"commit {sha[:12]} changes an approved feature without an Approved-by trailer"
            for sha in unapproved_commits(args.rev_range)
        ]
    for problem in problems:
        print(problem)
    if not problems:
        files = feature_files()
        approved = len(approved_hashes())
        print(f"features OK: {approved} approved, {len(files) - approved} draft")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
