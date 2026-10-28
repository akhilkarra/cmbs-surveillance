"""Fail if the mutation score drops below a floor.

Run after `uv run mutmut run && uv run mutmut export-cicd-stats`. The score is the share of
mutants the tests kill (timeouts count as killed), out of all mutants, so code that no test
reaches counts against it. Raise --min as tests improve; never lower it to make CI pass.
"""

import argparse
import json
import sys
from pathlib import Path

STATS = Path("mutants/mutmut-cicd-stats.json")


def score(stats: dict) -> float:
    tested = stats["total"] - stats["skipped"]
    return 100.0 * (stats["killed"] + stats["timeout"]) / tested if tested else 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--min", type=float, required=True, help="Lowest passing score, in %%.")
    args = parser.parse_args()
    stats = json.loads(STATS.read_text())
    got = score(stats)
    print(
        f"mutation score {got:.1f}% (floor {args.min:.1f}%): killed {stats['killed']}, "
        f"survived {stats['survived']}, no tests {stats['no_tests']}, of {stats['total']}"
    )
    if got < args.min:
        print("Survivors: `uv run mutmut results`, then `uv run mutmut show <name>`.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
