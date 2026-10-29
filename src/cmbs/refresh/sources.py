"""Where a refresh reads EDGAR from: the live hosts, or answers recorded once and replayed.

A refresh needs two kinds of answer. Filing discovery reads JSON (entity typeahead,
submissions) through `cmbs.edgar.replay`. Loading a month reads files from a filing's folder in
www.sec.gov/Archives: the folder's `index.json`, then the EX-102 asset file of the ABS-EE and the
EX-99.1 report of the 10-D. `Archive` is that second kind, with the same two implementations:
`LiveArchive` fetches, `ArchiveReplay` serves files recorded under a directory with a manifest of
URL to file (the layout of `cmbs.edgar.replay`).

Set CMBS_EDGAR_REPLAY and CMBS_ARCHIVE_REPLAY to recording directories to make the CLI and API
replay instead of calling SEC; each may list several directories separated by the path
separator (":"), searched in order.

Which file of a filing is the exhibit is read from its name, because `index.json` gives names
only: an EX-102 is an XML file whose name says 102 ("exh_102.xml"), an EX-99.1 an HTML file whose
name says 99.1 ("ex99_1.htm", "wcm18b13_ex991-202609.htm"). A filing with none or several such
files is refused rather than guessed at.
"""

import json
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from cmbs.edgar import archives, client
from cmbs.edgar.replay import Live, MissingRecording, Replay, Source
from cmbs.errors import Refused

EDGAR_REPLAY_ENV = "CMBS_EDGAR_REPLAY"
ARCHIVE_REPLAY_ENV = "CMBS_ARCHIVE_REPLAY"
MANIFEST = "recordings.json"
LIVE_INTERVAL_S = 0.15

# "exh_102.xml", "ex102.xml", "fixture-trust_ex102-202609.xml"; not "ex1020.xml".
_EX102 = re.compile(r"(?<!\d)102(?!\d)")
# "ex99_1.htm", "ex991.htm", "ex99-1.htm", "ex-99.1.htm", "x_ex991-202609.htm"; not "ex9910.htm".
_EX991 = re.compile(r"ex[-_.]?99[-_.]?1(?!\d)", re.IGNORECASE)


class Archive(Protocol):
    def get_bytes(self, url: str) -> bytes: ...


class AmbiguousExhibit(Refused):
    """A filing's folder has none, or more than one, file that could be the exhibit."""


class LiveArchive:
    """www.sec.gov/Archives, paced like the other live EDGAR sources."""

    def __init__(self, interval_s: float = LIVE_INTERVAL_S) -> None:
        self.interval_s = interval_s

    def get_bytes(self, url: str) -> bytes:
        data = client.get_bytes(url)
        client.pause(self.interval_s)
        return data


class ArchiveReplay:
    """Archive files recorded earlier, served back by URL."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        manifest = json.loads((directory / MANIFEST).read_text())
        self.responses: dict[str, str] = manifest["responses"]

    def get_bytes(self, url: str) -> bytes:
        name = self.responses.get(url)
        if name is None:
            raise MissingRecording(f"no recording of {url} in {self.directory}")
        return (self.directory / name).read_bytes()


class FirstRecorded:
    """Several recorded sources searched in order: the first that recorded a URL answers."""

    def __init__(self, *sources: Replay | ArchiveReplay) -> None:
        if not sources:
            raise ValueError("name at least one recording directory")
        self.sources = sources

    def _first(self, url: str, read: str) -> Any:
        for source in self.sources:
            try:
                return getattr(source, read)(url)
            except MissingRecording:
                continue
        directories = ", ".join(str(source.directory) for source in self.sources)
        raise MissingRecording(f"no recording of {url} in {directories}")

    def get_json(self, url: str) -> Any:
        return self._first(url, "get_json")

    def get_bytes(self, url: str) -> bytes:
        return self._first(url, "get_bytes")


@dataclass(frozen=True)
class Sources:
    """What a refresh reads: discovery's JSON and the archive's files."""

    edgar: Source
    archive: Archive


def _directories(value: str) -> list[Path]:
    return [Path(part) for part in value.split(os.pathsep) if part]


def default_sources() -> Sources:
    """Replay from the directories in the environment when set, otherwise the live hosts."""
    edgar_dirs = _directories(os.environ.get(EDGAR_REPLAY_ENV, ""))
    archive_dirs = _directories(os.environ.get(ARCHIVE_REPLAY_ENV, ""))
    edgar: Source = FirstRecorded(*map(Replay, edgar_dirs)) if edgar_dirs else Live()
    archive: Archive = (
        FirstRecorded(*map(ArchiveReplay, archive_dirs)) if archive_dirs else LiveArchive()
    )
    return Sources(edgar=edgar, archive=archive)


def pick_exhibit(names: Sequence[str], exhibit: str) -> str:
    """The one file named like the exhibit ("EX-102" or "EX-99.1"); refused if not exactly one."""
    if exhibit == "EX-102":
        found = [n for n in names if n.lower().endswith(".xml") and _EX102.search(n)]
    elif exhibit == "EX-99.1":
        found = [n for n in names if n.lower().endswith((".htm", ".html")) and _EX991.search(n)]
    else:
        raise ValueError(f"no rule for exhibit {exhibit!r}")
    if len(found) != 1:
        listed = ", ".join(found) if found else "none"
        raise AmbiguousExhibit(f"cannot tell which file is the {exhibit}: {listed}")
    return found[0]


@dataclass(frozen=True)
class Fetched:
    """One exhibit of one filing, as fetched."""

    accession: str
    file_name: str
    content: bytes


def fetch_exhibit(archive: Archive, *, cik: int, accession: str, exhibit: str) -> Fetched:
    """The filing's exhibit: its folder index read, the file picked by name, then fetched."""
    base = archives.filing_base_url(cik, accession)
    index = json.loads(archive.get_bytes(base + "index.json"))
    names = [item["name"] for item in index["directory"]["item"]]
    name = pick_exhibit(names, exhibit)
    return Fetched(accession=accession, file_name=name, content=archive.get_bytes(base + name))
