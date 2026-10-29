"""The synthetic recordings match what the code asks today, and say they are synthetic."""

from pathlib import Path

import pytest

from cmbs.model.recordings import read_file
from tests.fixtures.model.synthetic import FILES, render

HERE = Path(__file__).parent / "fixtures" / "model"


@pytest.mark.parametrize("name", sorted(FILES))
def test_each_fixture_is_what_the_generator_writes(name):
    stored = (HERE / name).read_text()
    assert stored == render(FILES[name]()), (
        f"{name} no longer matches the questions; regenerate it with "
        "`uv run python -m tests.fixtures.model.synthetic` (new wording needs a new prompt version)"
    )


def test_every_fixture_is_marked_synthetic_and_checks_out():
    paths = sorted(HERE.glob("*.json"))
    assert [path.name for path in paths] == sorted(FILES)
    for path in paths:
        recording = read_file(path)
        assert recording.synthetic, path.name
