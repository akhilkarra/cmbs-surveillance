"""Schedule AL (EX-102) files for tests: small ones built asset by asset, and real ones.

The real files are the EX-102 asset files of three ABS-EE filings, downloaded from
www.sec.gov/Archives on 2026-10-28 and stored gzip-compressed so every byte stays exactly as
filed (text hooks would otherwise "fix" them). `real_bytes` checks each against its SHA-256.
"""

import gzip
import hashlib
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

FIXTURES = Path(__file__).resolve().parents[1]
FIXTURE_TAPE = FIXTURES / "schedule_al_minimal.xml"
HERE = Path(__file__).resolve().parent
URI = "http://www.sec.gov/edgar/document/absee/cmbs/assetdata"


@dataclass(frozen=True)
class RealFile:
    cik: int
    accession: str
    sha256: str

    @property
    def url(self) -> str:
        folder = self.accession.replace("-", "")
        return f"https://www.sec.gov/Archives/edgar/data/{self.cik}/{folder}/exh_102.xml"


REAL_FILES = {
    # BANK 2018-BNK13, August 2026, filed 2026-08-27: 87 assets, 62 loans (20 co-ops among them).
    "bank-2018-bnk13_2026-08_ex102.xml.gz": RealFile(
        1744707,
        "0001888524-26-015516",
        "1c261ffca8fddbe26c87c4aff80f22c2b8fafc84f7b50513612b916ed4791707",
    ),
    # BANK 2018-BNK13, September 2026, filed 2026-09-29: 67 assets, 42 loans, no co-ops listed.
    # Byte for byte the same file the 10-D (0001888524-26-017704) attaches.
    "bank-2018-bnk13_2026-09_ex102.xml.gz": RealFile(
        1744707,
        "0001888524-26-017705",
        "18694fdec15d13583b9a6c933a6d2b1be107a05e05dbb4ed74620def41fb15ea",
    ),
    # BANK5 2026-5YR23, September 2026, filed 2026-10-01: 33 loans, portfolio properties nested
    # as several <property> elements of one asset, no mostRecent* financials yet.
    "bank5-2026-5yr23_2026-09_ex102.xml.gz": RealFile(
        2140978,
        "0001888524-26-018152",
        "dc5a19686305cde375f68e76d7838f059abfa8c8e3c73cfe2988529b3c679687",
    ),
}


def asset(number: str, prop: dict[str, str] | None = None, **tags: str) -> str:
    """One <assets> element; `reportingPeriodEndDate` defaults to 09-11-2026."""
    tags = {"reportingPeriodEndDate": "09-11-2026", **tags}
    lines = [f"<assetNumber>{escape(number)}</assetNumber>"]
    lines += [f"<{tag}>{escape(value)}</{tag}>" for tag, value in tags.items()]
    if prop is not None:
        inner = "".join(f"<{tag}>{escape(value)}</{tag}>" for tag, value in prop.items())
        lines.append(f"<property>{inner}</property>")
    return "<assets>" + "".join(lines) + "</assets>"


def tape(*assets: str) -> bytes:
    header = '<?xml version="1.0" encoding="UTF-8"?>'
    return f'{header}<assetData xmlns="{URI}">{"".join(assets)}</assetData>'.encode()


def fixture_bytes() -> bytes:
    return FIXTURE_TAPE.read_bytes()


def real_bytes(name: str) -> bytes:
    """The real EX-102 file exactly as filed."""
    content = gzip.decompress((HERE / name).read_bytes())
    expected = REAL_FILES[name].sha256
    if hashlib.sha256(content).hexdigest() != expected:
        raise AssertionError(f"{name} is not the file filed on EDGAR (SHA-256 {expected})")
    return content
