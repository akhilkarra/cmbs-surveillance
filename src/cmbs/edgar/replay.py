"""Where EDGAR answers come from: the live hosts, or responses recorded once and replayed.

Tests and scenarios never call the network. `Recorder` fetches each URL once from the live
hosts and saves the response under a directory with a manifest of URL to file; `Replay` serves
those files back. Set CMBS_EDGAR_REPLAY to a recordings directory to make the CLI and API
replay too.

A recording keeps only the fields discovery reads (`trim`): smaller files, and no addresses,
phone numbers or filer-agent names copied from EDGAR into the repository.
"""

import json
import os
import re
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import unquote_plus, urlsplit

from cmbs import clock
from cmbs.edgar import client

MANIFEST = "recordings.json"
REPLAY_ENV = "CMBS_EDGAR_REPLAY"
LIVE_INTERVAL_S = 0.15

SUBMISSION_COLUMNS = ("accessionNumber", "filingDate", "reportDate", "form", "primaryDocument")
HIT_FIELDS = ("entity", "form", "root_forms", "file_date", "period_ending", "ciks", "adsh")


class Source(Protocol):
    def get_json(self, url: str) -> Any: ...


class Live:
    """The live EDGAR hosts, paced to stay well under SEC's ten requests a second."""

    def __init__(self, interval_s: float = LIVE_INTERVAL_S) -> None:
        self.interval_s = interval_s

    def get_json(self, url: str) -> Any:
        data = client.get_json(url)
        client.pause(self.interval_s)
        return data


class MissingRecording(LookupError):
    """A replayed source was asked for a URL that was never recorded."""


def recording_name(url: str) -> str:
    """The file a URL's response is saved as, readable enough to find by eye.

    'https://data.sec.gov/submissions/CIK0001744707.json' -> 'submissions/CIK0001744707.json'
    'https://efts.sec.gov/LATEST/search-index?keysTyped=COMM+2014-CR18'
        -> 'search/keysTyped_COMM_2014-CR18.json'
    """
    parts = urlsplit(url)
    if parts.netloc == "data.sec.gov":
        return "submissions/" + parts.path.rsplit("/", 1)[-1]
    slug = re.sub(r"[^A-Za-z0-9.-]+", "_", unquote_plus(parts.query)).strip("_")
    return f"search/{slug}.json"


def _trim_block(block: dict[str, Any]) -> dict[str, Any]:
    return {column: block[column] for column in SUBMISSION_COLUMNS if column in block}


def trim(data: Any) -> Any:
    """Keep only what discovery reads from a submissions page or an efts response."""
    if isinstance(data, dict) and "filings" in data:
        filings = data["filings"]
        return {
            "cik": data.get("cik"),
            "name": data.get("name"),
            "filings": {
                "recent": _trim_block(filings["recent"]),
                "files": [{"name": page["name"]} for page in filings.get("files", [])],
            },
        }
    if isinstance(data, dict) and "accessionNumber" in data:
        return _trim_block(data)
    if isinstance(data, dict) and "hits" in data:
        hits = data["hits"]
        return {
            "hits": {
                "total": hits["total"],
                "hits": [
                    {
                        "_id": hit["_id"],
                        "_source": {
                            field: hit["_source"][field]
                            for field in HIT_FIELDS
                            if field in hit["_source"]
                        },
                    }
                    for hit in hits["hits"]
                ],
            }
        }
    return data


class Replay:
    """Responses recorded earlier, served back by URL."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        manifest = json.loads((directory / MANIFEST).read_text())
        self.recorded_on: str = manifest["recorded_on"]
        self.responses: dict[str, str] = manifest["responses"]

    def get_json(self, url: str) -> Any:
        name = self.responses.get(url)
        if name is None:
            raise MissingRecording(f"no recording of {url} in {self.directory}")
        return json.loads((self.directory / name).read_text())


class Recorder:
    """Fetch from `upstream` and save each trimmed response, so it can be replayed later."""

    def __init__(self, directory: Path, upstream: Source) -> None:
        self.directory = directory
        self.upstream = upstream
        manifest_path = directory / MANIFEST
        self.responses: dict[str, str] = (
            json.loads(manifest_path.read_text())["responses"] if manifest_path.is_file() else {}
        )

    def get_json(self, url: str) -> Any:
        data = trim(self.upstream.get_json(url))
        name = recording_name(url)
        path = self.directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
        self.responses[url] = name
        manifest = {
            "recorded_on": clock.today().isoformat(),
            "responses": dict(sorted(self.responses.items())),
        }
        (self.directory / MANIFEST).write_text(json.dumps(manifest, indent=1) + "\n")
        return data


def default_source() -> Source:
    """Replay from CMBS_EDGAR_REPLAY when it names a directory, otherwise the live hosts."""
    directory = os.environ.get(REPLAY_ENV)
    return Replay(Path(directory)) if directory else Live()
