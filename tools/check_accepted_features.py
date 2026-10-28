"""CI check: accepted feature files match the manifest, and changes to them carry approval.

Usage:
  python tools/check_accepted_features.py [--range BASE..HEAD]

With --range, every commit in the range that touches features/accepted/ must have an
`Approved-by:` trailer.
"""

import argparse
import subprocess
import sys

from accepted_features import ACCEPTED_DIR, current_hashes, manifest_problems, read_manifest

ZERO_SHA = "0" * 40


def unapproved_commits(rev_range: str) -> list[str]:
    base, _, _ = rev_range.partition("..")
    if not base or base == ZERO_SHA:
        rev_range = rev_range.partition("..")[2]  # first push: check the whole history
    shas = subprocess.run(
        ["git", "log", "--format=%H", rev_range, "--", str(ACCEPTED_DIR)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    missing = []
    for sha in shas:
        trailers = subprocess.run(
            ["git", "log", "-1", "--format=%(trailers:key=Approved-by,valueonly)", sha],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if not trailers:
            missing.append(sha)
    return missing


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--range", dest="rev_range")
    args = parser.parse_args(argv)
    problems = manifest_problems(read_manifest(), current_hashes())
    if args.rev_range:
        problems += [
            f"commit {sha[:12]} changes {ACCEPTED_DIR}/ without an Approved-by trailer"
            for sha in unapproved_commits(args.rev_range)
        ]
    for problem in problems:
        print(problem)
    if not problems:
        print(f"accepted features OK ({len(current_hashes())} files)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
