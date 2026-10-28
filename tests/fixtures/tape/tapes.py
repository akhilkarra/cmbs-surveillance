"""Build small Schedule AL (EX-102) files for tests, one asset at a time."""

from pathlib import Path
from xml.sax.saxutils import escape

FIXTURES = Path(__file__).resolve().parents[1]
FIXTURE_TAPE = FIXTURES / "schedule_al_minimal.xml"
HERE = Path(__file__).resolve().parent
URI = "http://www.sec.gov/edgar/document/absee/cmbs/assetdata"


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
    return (HERE / name).read_bytes()
