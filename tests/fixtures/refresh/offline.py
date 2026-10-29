"""Keep refresh tests off the network, whatever the code under test does.

The refresh replays EDGAR only when it is handed recordings or the environment names them. A
mutant (or a bug) that falls back to the live sources would otherwise call SEC from a test and
wait on the network; with this fixture it fails at once instead. Import it into a test module:

    from tests.fixtures.refresh.offline import offline_fixture  # noqa: F401
"""

from collections.abc import Iterator

import pytest

from cmbs.edgar import client


def _no_network(*args: object, **kwargs: object) -> bytes:
    raise OSError("tests never call SEC: the network is blocked")


@pytest.fixture(name="offline", autouse=True)
def offline_fixture(monkeypatch) -> Iterator[None]:
    monkeypatch.setattr(client, "get_bytes", _no_network)
    monkeypatch.setattr(client, "pause", lambda seconds: None)
    yield
