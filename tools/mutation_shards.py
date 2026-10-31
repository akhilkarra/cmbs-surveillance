"""Split mutation testing into shards that together run every mutant exactly once.

Every shard first has mutmut generate all mutants (cheap), lists their names, and keeps the
ones whose CRC-32 falls in its slot: `crc32(name) % shards == shard`. The split is
deterministic, disjoint and complete by construction, and balances to within a few mutants.
Each shard then runs mutmut on its own names and writes mutants/shard-stats.json: mutmut's CI
stats (which count the other shards' mutants as not checked), stamped with the shard number,
the shard count and how many mutants it planned to run. tools/check_mutation_score.py combines
the shards' files and proves that every shard ran once and that together they checked every
mutant.

Usage:
  uv run python tools/mutation_shards.py run --shard 0 --of 16 [--max-children 4]
  uv run python tools/mutation_shards.py list --shard 0 --of 16     # names only, after a run
"""

import argparse
import json
import subprocess
import sys
import zlib
from pathlib import Path

NOTHING = "__generate_mutants_only__"
CICD_STATS = Path("mutants/mutmut-cicd-stats.json")
SHARD_STATS = Path("mutants/shard-stats.json")


def in_shard(name: str, shard: int, shards: int) -> bool:
    if shards < 1 or not 0 <= shard < shards:
        raise ValueError(f"shard {shard} is not between 0 and {shards - 1}")
    return zlib.crc32(name.encode()) % shards == shard


def split(names: list[str], shard: int, shards: int) -> list[str]:
    return sorted(name for name in names if in_shard(name, shard, shards))


def parse_results(output: str) -> list[str]:
    """Mutant names from `mutmut results --all true`, whose lines read like
    '    cmbs.access.service.x_authorize__mutmut_9: killed'."""
    names = []
    for line in output.splitlines():
        name, separator, _ = line.strip().rpartition(":")
        if separator and name and "__mutmut_" in name:
            names.append(name)
    return names


def all_mutant_names() -> list[str]:
    """Generate every mutant without testing any, and list their names."""
    # mutmut generates all mutants before filtering by name; a filter that matches nothing then
    # stops it before any test runs.
    subprocess.run(["mutmut", "run", NOTHING], check=False, capture_output=True)
    listing = subprocess.run(
        ["mutmut", "results", "--all", "true"], check=True, capture_output=True, text=True
    ).stdout
    names = parse_results(listing)
    if not names:
        raise SystemExit("mutmut generated no mutants; check [tool.mutmut] in pyproject.toml")
    return names


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("command", choices=["run", "list"])
    parser.add_argument("--shard", type=int, required=True)
    parser.add_argument("--of", type=int, required=True, dest="shards")
    parser.add_argument("--max-children", type=int, default=4)
    args = parser.parse_args(argv)
    names = split(all_mutant_names(), args.shard, args.shards)
    if args.command == "list":
        print("\n".join(names))
        return 0
    print(f"shard {args.shard} of {args.shards}: {len(names)} mutants")
    if names:
        command = ["mutmut", "run", "--max-children", str(args.max_children), *names]
        result = subprocess.run(command, check=False, capture_output=True, text=True)
        if result.returncode != 0:
            # mutmut reports failures on stdout; show the end of it so CI says what went wrong.
            print(result.stdout[-4000:], result.stderr[-2000:], sep="\n", file=sys.stderr)
            return result.returncode
    subprocess.run(["mutmut", "export-cicd-stats"], check=True)
    stamped = {
        **json.loads(CICD_STATS.read_text()),
        "shard": args.shard,
        "of": args.shards,
        "planned": len(names),
    }
    SHARD_STATS.write_text(json.dumps(stamped, indent=2))
    print(f"wrote {SHARD_STATS}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
