"""Reading every asset of a Schedule AL file exactly as reported."""

import pytest

from cmbs.absee.schedule_al import (
    Asset,
    AssetKind,
    MalformedTape,
    asset_kind,
    parse_assets,
)
from cmbs.errors import Refused
from tests.fixtures.tape.tapes import URI, asset, fixture_bytes, tape


@pytest.mark.parametrize(
    ("number", "name", "kind"),
    [
        ("1", "Harbor Office Tower", AssetKind.LOAN),
        ("1A", "NA", AssetKind.COMPANION_NOTE),
        ("2-001", "Lakeside Hotel A", AssetKind.PORTFOLIO_PROPERTY),
        ("2-001", "NA", AssetKind.PORTFOLIO_PROPERTY),
        ("5", "", AssetKind.LOAN),
        ("5", "na", AssetKind.LOAN),
    ],
)
def test_asset_kind(number, name, kind):
    assert asset_kind(number, name) is kind


def test_the_fixture_has_nine_assets_in_file_order():
    assets = parse_assets(fixture_bytes())
    assert [(a.position, a.asset_number, a.kind) for a in assets] == [
        (1, "1", AssetKind.LOAN),
        (2, "1A", AssetKind.COMPANION_NOTE),
        (3, "2", AssetKind.LOAN),
        (4, "2-001", AssetKind.PORTFOLIO_PROPERTY),
        (5, "3", AssetKind.LOAN),
        (6, "4", AssetKind.LOAN),
        (7, "5", AssetKind.LOAN),
        (8, "6", AssetKind.LOAN),
        (9, "7", AssetKind.LOAN),
    ]


def test_tags_keep_their_text_exactly_as_reported():
    content = tape(
        asset(
            " 8 ",
            prop={"propertyName": " Cedar Office ", "propertyTypeCode": "OF"},
            paymentStatusLoanCode="0",
            paidThroughDate="",
        )
    )
    [parsed] = parse_assets(content)
    assert parsed == Asset(
        position=1,
        tags={
            "assetNumber": " 8 ",
            "reportingPeriodEndDate": "09-11-2026",
            "paymentStatusLoanCode": "0",
            "paidThroughDate": "",
        },
        properties=[{"propertyName": " Cedar Office ", "propertyTypeCode": "OF"}],
    )
    assert parsed.asset_number == "8"
    assert parsed.tag("paymentStatusLoanCode") == "0"
    assert parsed.tag("missing") == ""
    assert parsed.property_tag("propertyName") == "Cedar Office"
    assert parsed.property_tag("missing") == ""


def test_an_asset_without_property_reads_empty_property_tags():
    [parsed] = parse_assets(tape(asset("9")))
    assert parsed.properties == []
    assert parsed.property_tag("propertyName") == ""
    assert parsed.kind is AssetKind.LOAN


def test_every_property_element_is_kept_in_file_order():
    """Each <property> is one real property; bronze keeps them all, and the loan row combines
    them (tests/test_tape_properties.py), never reading the first one alone."""
    content = tape(
        "<assets><assetNumber>3</assetNumber>"
        "<property><propertyName>First</propertyName><propertyTypeCode>OF</propertyTypeCode>"
        "</property>"
        "<property><propertyName>Second</propertyName></property></assets>"
    )
    [parsed] = parse_assets(content)
    assert parsed.properties == [
        {"propertyName": "First", "propertyTypeCode": "OF"},
        {"propertyName": "Second"},
    ]
    assert parsed.kind is AssetKind.LOAN


def test_an_empty_element_reads_as_empty_text():
    [parsed] = parse_assets(
        tape("<assets><assetNumber>4</assetNumber><modificationCode/></assets>")
    )
    assert parsed.tags == {"assetNumber": "4", "modificationCode": ""}


def test_a_file_without_assets_has_none():
    assert parse_assets(tape()) == []


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"not xml", "the file is not XML: syntax error: line 1, column 0"),
        (
            f'<otherData xmlns="{URI}"/>'.encode(),
            "the file is not Schedule AL asset data: root is 'otherData'",
        ),
        (
            b"<assetData/>",
            "the file is not Schedule AL asset data: root is 'assetData'",
        ),
        (
            tape("<assets><assetNumber>1</assetNumber><notes><a>x</a></notes></assets>"),
            "asset 1 has an unexpected nested element 'notes'",
        ),
        (
            tape(
                asset("1"),
                "<assets><assetNumber>2</assetNumber><assetNumber>3</assetNumber></assets>",
            ),
            "asset 2 reports 'assetNumber' twice",
        ),
        (
            tape(
                "<assets><assetNumber>1</assetNumber><property>"
                "<propertyName>A</propertyName><propertyName>B</propertyName>"
                "</property></assets>"
            ),
            "asset 1 reports 'propertyName' twice",
        ),
    ],
)
def test_malformed_files_are_refused_with_the_reason(content, message):
    with pytest.raises(MalformedTape) as refusal:
        parse_assets(content)
    assert str(refusal.value) == message
    assert isinstance(refusal.value, Refused)
