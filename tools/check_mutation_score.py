"""Fail if the mutation score drops below a floor.

Run after `uv run mutmut run && uv run mutmut export-cicd-stats`. The score is the share of
mutants the tests kill (timeouts count as killed), out of all mutants, so code that no test
reaches counts against it. Raise --min as tests improve; never lower it to make CI pass.

With sharded runs (tools/mutation_shards.py), pass every shard's mutants/shard-stats.json. The
check requires each shard exactly once, each having checked the mutants it planned, and all of
them together having checked every mutant; then it sums their results.
"""

import argparse
import json
import sys
from pathlib import Path

STATS = Path("mutants/mutmut-cicd-stats.json")
# Every outcome mutmut exports. A mutant that was checked has exactly one; one that was not has
# none (mutmut does not export a "not checked" count).
OUTCOMES = (
    "killed",
    "survived",
    "timeout",
    "no_tests",
    "skipped",
    "suspicious",
    "check_was_interrupted_by_user",
    "segfault",
)


def checked(stats: dict) -> int:
    return sum(stats.get(outcome, 0) for outcome in OUTCOMES)


class ShardMismatch(ValueError):
    pass


def combine(shards: list[dict]) -> dict:
    """One stats record from per-shard records that each ran a disjoint part of the mutants.

    Each record carries its shard number, the shard count and how many mutants it planned to run
    (tools/mutation_shards.py). Every shard must appear exactly once and must have checked what
    it planned; otherwise a duplicated shard could hide a missing one.
    """
    if not shards:
        raise ShardMismatch("no stats files given")
    counts = {shard.get("of") for shard in shards}
    if len(counts) != 1 or not all(isinstance(shard.get("shard"), int) for shard in shards):
        raise ShardMismatch("every stats file must say how many shards there are")
    expected = counts.pop()
    if not isinstance(expected, int):
        raise ShardMismatch("every stats file must say how many shards there are")
    seen = sorted(int(shard["shard"]) for shard in shards)
    if seen != list(range(expected)):
        raise ShardMismatch(f"expected shards 0 to {expected - 1} once each, got {seen}")
    for shard in shards:
        ran = checked(shard)
        if ran != shard["planned"]:
            raise ShardMismatch(
                f"shard {shard['shard']} checked {ran} mutants but planned {shard['planned']}"
            )
    totals = {shard["total"] for shard in shards}
    if len(totals) != 1:
        raise ShardMismatch(f"shards saw different numbers of mutants: {sorted(totals)}")
    total = totals.pop()
    together = sum(checked(shard) for shard in shards)
    if together != total:
        raise ShardMismatch(f"the shards checked {together} mutants, but there are {total}")
    combined = {key: sum(shard.get(key, 0) for shard in shards) for key in OUTCOMES}
    return {**combined, "total": total}


def score(stats: dict) -> float:
    tested = stats["total"] - stats["skipped"]
    return 100.0 * (stats["killed"] + stats["timeout"]) / tested if tested else 0.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--min", type=float, required=True, help="Lowest passing score, in %%.")
    parser.add_argument("stats", nargs="*", type=Path, help="Stats files (default: one run).")
    args = parser.parse_args(argv)
    files = args.stats or [STATS]
    records = [json.loads(path.read_text()) for path in files]
    try:
        stats = combine(records) if len(records) > 1 else records[0]
    except ShardMismatch as mismatch:
        print(f"mutation stats do not add up: {mismatch}")
        return 1
    got = score(stats)
    print(
        f"mutation score {got:.1f}% (floor {args.min:.1f}%): killed {stats['killed']}, "
        f"survived {stats['survived']}, no tests {stats['no_tests']}, of {stats['total']}"
        + (f", from {len(records)} shards" if len(records) > 1 else "")
    )
    if got < args.min:
        print("Survivors: `uv run mutmut results`, then `uv run mutmut show <name>`.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
