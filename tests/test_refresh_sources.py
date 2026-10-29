"""Where a refresh reads EDGAR from (cmbs.refresh.sources). No network, no database."""

import json
from pathlib import Path

import pytest

from cmbs.edgar import client
from cmbs.edgar.replay import Live, MissingRecording, Replay
from cmbs.refresh import sources
from cmbs.refresh.sources import (
    AmbiguousExhibit,
    ArchiveReplay,
    FirstRecorded,
    LiveArchive,
    default_sources,
    fetch_exhibit,
    pick_exhibit,
)
from tests.fixtures.refresh.offline import offline_fixture  # noqa: F401

FIXTURES = Path(__file__).parent / "fixtures"
ARCHIVE = FIXTURES / "refresh" / "archive"
EDGAR = FIXTURES / "refresh" / "edgar"
F1_SEPTEMBER_TAPE = "0009900001-26-000419"
FOLDER = "https://www.sec.gov/Archives/edgar/data/9900001/000990000126000419/"


def write_recordings(directory: Path, files: dict[str, tuple[str, bytes]]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    responses = {}
    for url, (name, content) in files.items():
        (directory / name).write_bytes(content)
        responses[url] = name
    (directory / "recordings.json").write_text(
        json.dumps({"recorded_on": "2026-10-28", "responses": responses})
    )


@pytest.mark.parametrize(
    "names",
    [
        ["absee.htm", "exh_102.xml", "exh_103.xml"],
        ["x_absee-202609.htm", "x_ex103-202609.xml", "x_ex102-202609.xml"],
        ["EX102.XML", "ex1020.xml"],
    ],
)
def test_the_ex102_is_the_xml_file_named_102(names):
    picked = pick_exhibit(names, "EX-102")
    assert picked in names
    assert "102" in picked and "1020" not in picked


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        (["10d.htm", "ex99_1.htm"], "ex99_1.htm"),
        (["wcm18b13_10d-202609.htm", "wcm18b13_ex991-202609.htm"], "wcm18b13_ex991-202609.htm"),
        (["d10d.htm", "ex-99.1.html", "ex99_10.htm"], "ex-99.1.html"),
        (["a.htm", "EX99-1.HTM", "ex991.xml"], "EX99-1.HTM"),
    ],
)
def test_the_ex991_is_the_html_file_named_99_1(names, expected):
    assert pick_exhibit(names, "EX-99.1") == expected


def test_none_or_several_candidates_are_refused():
    with pytest.raises(AmbiguousExhibit) as none:
        pick_exhibit(["absee.htm", "ex103.xml", "ex102.htm"], "EX-102")
    assert str(none.value) == "cannot tell which file is the EX-102: none"
    with pytest.raises(AmbiguousExhibit) as several:
        pick_exhibit(["ex99_1.htm", "ex991.htm", "ex99_10.htm"], "EX-99.1")
    assert str(several.value) == "cannot tell which file is the EX-99.1: ex99_1.htm, ex991.htm"
    with pytest.raises(ValueError, match="no rule for exhibit 'EX-103'"):
        pick_exhibit(["ex103.xml"], "EX-103")


def test_archive_replay_serves_recorded_files():
    archive = ArchiveReplay(ARCHIVE)
    index = json.loads(archive.get_bytes(FOLDER + "index.json"))
    assert [item["name"] for item in index["directory"]["item"]] == [
        "fixture-trust-2026-f1_absee-202609.htm",
        "fixture-trust-2026-f1_ex102-202609.xml",
        "fixture-trust-2026-f1_ex103-202609.xml",
    ]
    with pytest.raises(MissingRecording) as missing:
        archive.get_bytes(FOLDER + "nothing.xml")
    assert str(missing.value) == f"no recording of {FOLDER}nothing.xml in {ARCHIVE}"


def test_fetch_exhibit_reads_the_index_then_the_file():
    fetched = fetch_exhibit(
        ArchiveReplay(ARCHIVE), cik=9_900_001, accession=F1_SEPTEMBER_TAPE, exhibit="EX-102"
    )
    assert fetched.accession == F1_SEPTEMBER_TAPE
    assert fetched.file_name == "fixture-trust-2026-f1_ex102-202609.xml"
    name = f"{F1_SEPTEMBER_TAPE}/fixture-trust-2026-f1_ex102-202609.xml"
    assert fetched.content == (ARCHIVE / name).read_bytes()


def test_first_recorded_asks_each_directory_in_order(tmp_path):
    url = "https://example.invalid/a"
    write_recordings(tmp_path / "one", {url: ("a", b"first")})
    write_recordings(tmp_path / "two", {url: ("a", b"second"), url + "b": ("b", b"only two")})
    both = FirstRecorded(ArchiveReplay(tmp_path / "one"), ArchiveReplay(tmp_path / "two"))
    assert both.get_bytes(url) == b"first"
    assert both.get_bytes(url + "b") == b"only two"
    with pytest.raises(MissingRecording) as missing:
        both.get_bytes(url + "c")
    assert str(missing.value) == (
        f"no recording of {url}c in {tmp_path / 'one'}, {tmp_path / 'two'}"
    )


def test_first_recorded_serves_json_too():
    edgar = FirstRecorded(Replay(EDGAR), Replay(FIXTURES / "edgar"))
    hyt = edgar.get_json("https://efts.sec.gov/LATEST/search-index?keysTyped=HYT+2024-RGCY")
    fixture = edgar.get_json(
        "https://efts.sec.gov/LATEST/search-index?keysTyped=Fixture+Trust+2026-F1"
    )
    assert fixture["hits"]["hits"][0]["_id"] == "9900001"
    assert "hits" in hyt


def test_first_recorded_needs_a_source():
    with pytest.raises(ValueError, match="name at least one recording directory"):
        FirstRecorded()


def test_default_sources_replay_when_the_environment_names_recordings(monkeypatch):
    monkeypatch.setenv(sources.EDGAR_REPLAY_ENV, f"{EDGAR}:{FIXTURES / 'edgar'}")
    monkeypatch.setenv(sources.ARCHIVE_REPLAY_ENV, str(ARCHIVE))
    found = default_sources()
    assert isinstance(found.edgar, FirstRecorded)
    assert [s.directory for s in found.edgar.sources] == [EDGAR, FIXTURES / "edgar"]
    assert isinstance(found.archive, FirstRecorded)
    assert [s.directory for s in found.archive.sources] == [ARCHIVE]


def test_default_sources_are_live_otherwise(monkeypatch):
    monkeypatch.delenv(sources.EDGAR_REPLAY_ENV, raising=False)
    monkeypatch.setenv(sources.ARCHIVE_REPLAY_ENV, "")
    found = default_sources()
    assert isinstance(found.edgar, Live)
    assert isinstance(found.archive, LiveArchive)


def test_live_archive_fetches_and_pauses(monkeypatch):
    calls = []
    monkeypatch.setattr(client, "get_bytes", lambda url: calls.append(("get", url)) or b"data")
    monkeypatch.setattr(client, "pause", lambda seconds: calls.append(("pause", seconds)))
    assert LiveArchive(0.5).get_bytes("https://www.sec.gov/x") == b"data"
    assert calls == [("get", "https://www.sec.gov/x"), ("pause", 0.5)]
    assert LiveArchive().interval_s == sources.LIVE_INTERVAL_S
