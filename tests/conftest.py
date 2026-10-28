from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def schedule_al_path() -> Path:
    return FIXTURES / "schedule_al_minimal.xml"
