"""The mutation-shard planner and the score check that combines shards."""

import importlib.util
import json
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shards = load("mutation_shards")
check = load("check_mutation_score")

NAMES = [f"cmbs.mod{i % 7}.x_func{i % 13}__mutmut_{i}" for i in range(400)]


def test_shards_are_disjoint_and_cover_every_mutant():
    parts = [shards.split(NAMES, i, 4) for i in range(4)]
    assert sorted(name for part in parts for name in part) == sorted(NAMES)
    assert all(len(part) > 60 for part in parts)


def test_the_split_is_deterministic():
    assert shards.split(NAMES, 2, 4) == shards.split(list(reversed(NAMES)), 2, 4)


@pytest.mark.parametrize(("shard", "of"), [(4, 4), (-1, 4), (0, 0)])
def test_impossible_shards_are_refused(shard, of):
    with pytest.raises(ValueError, match="is not between"):
        shards.in_shard("cmbs.a.x_f__mutmut_1", shard, of)


def test_results_listing_is_parsed_to_names():
    listing = (
        "    cmbs.access.service.x_authorize__mutmut_9: killed\n"
        "    cmbs.settings.service.xǁProposalǁdecide__mutmut_2: not checked\n"
        "\n"
        "some other line\n"
    )
    assert shards.parse_results(listing) == [
        "cmbs.access.service.x_authorize__mutmut_9",
        "cmbs.settings.service.xǁProposalǁdecide__mutmut_2",
    ]


def stats(shard: int = 0, of: int = 2, planned: int = 5, **counts) -> dict:
    base = dict.fromkeys(("killed", "survived", "timeout", "no_tests", "skipped", "suspicious"), 0)
    return {
        **base,
        "total": 10,
        "not_checked": 10,
        "shard": shard,
        "of": of,
        "planned": planned,
        **counts,
    }


def test_shards_combine_into_one_record():
    first = stats(shard=0, killed=4, survived=1, not_checked=5)
    second = stats(shard=1, killed=3, timeout=1, no_tests=1, not_checked=5)
    combined = check.combine([first, second])
    assert combined == {
        "killed": 7,
        "survived": 1,
        "timeout": 1,
        "no_tests": 1,
        "skipped": 0,
        "suspicious": 0,
        "total": 10,
        "not_checked": 0,
    }
    assert check.score(combined) == 80.0


def test_a_duplicated_shard_cannot_hide_a_missing_one():
    same = stats(shard=0, killed=5, not_checked=5)
    with pytest.raises(
        check.ShardMismatch, match=r"expected shards 0 to 1 once each, got \[0, 0\]"
    ):
        check.combine([same, same])


def test_a_shard_that_ran_less_than_it_planned_is_caught():
    short = stats(shard=0, planned=5, killed=4, not_checked=6)
    other = stats(shard=1, planned=5, killed=5, not_checked=5)
    with pytest.raises(check.ShardMismatch, match="shard 0 checked 4 mutants but planned 5"):
        check.combine([short, other])


def test_files_without_shard_stamps_are_refused():
    unstamped = {k: v for k, v in stats(killed=5, not_checked=5).items() if k != "of"}
    with pytest.raises(check.ShardMismatch, match="how many shards there are"):
        check.combine([unstamped, unstamped])


def test_shards_that_together_miss_mutants_are_caught():
    first = stats(shard=0, planned=4, killed=4, not_checked=6)
    second = stats(shard=1, planned=4, killed=4, not_checked=6)
    with pytest.raises(check.ShardMismatch, match="checked 8 mutants, but there are 10"):
        check.combine([first, second])


def test_shards_from_different_code_are_caught():
    with pytest.raises(check.ShardMismatch, match="different numbers of mutants"):
        check.combine(
            [
                stats(shard=0, killed=5, not_checked=5),
                {**stats(shard=1, killed=5), "total": 12, "not_checked": 7},
            ]
        )


def test_no_files_is_caught():
    with pytest.raises(check.ShardMismatch, match="no stats files"):
        check.combine([])


def test_the_check_fails_below_the_floor_and_passes_above(tmp_path, capsys):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(stats(shard=0, killed=4, survived=1, not_checked=5)))
    b.write_text(json.dumps(stats(shard=1, killed=3, survived=2, not_checked=5)))
    assert check.main(["--min", "70", str(a), str(b)]) == 0
    assert "mutation score 70.0% (floor 70.0%)" in capsys.readouterr().out
    assert check.main(["--min", "71", str(a), str(b)]) == 1


def test_mismatched_shards_fail_the_check(tmp_path, capsys):
    a = tmp_path / "a.json"
    a.write_text(json.dumps(stats(shard=0, killed=5, not_checked=5)))
    assert check.main(["--min", "0", str(a), str(a)]) == 1
    assert "do not add up" in capsys.readouterr().out
