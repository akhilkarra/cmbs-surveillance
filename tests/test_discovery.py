"""Filing discovery without a database: name matching, paging, form filters, recorded replays."""

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest

from cmbs import clock
from cmbs.domain import Period
from cmbs.edgar import discovery, replay, search, submissions
from cmbs.edgar.discovery import (
    AmbiguousDeal,
    BlankDealName,
    Discovery,
    Entity,
    FoundFiling,
    Match,
    discover,
    full_text_filings,
    name_variants,
    names_match,
    resolve,
)
from cmbs.edgar.replay import MissingRecording, Recorder, Replay, recording_name, trim

RECORDINGS = Path(__file__).parent / "fixtures" / "edgar"
ON = dt.date(2026, 10, 27)


class FakeSource:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.asked: list[str] = []

    def get_json(self, url: str) -> Any:
        self.asked.append(url)
        return self.responses[url]


def entities(*pairs: tuple[str, str]) -> dict[str, Any]:
    rows = [{"_id": cik, "_source": {"entity": name}} for cik, name in pairs]
    return {"hits": {"total": {"value": len(rows)}, "hits": rows}}


def no_entities() -> dict[str, Any]:
    return entities()


def block(*rows: tuple[str, str, str, str, str]) -> dict[str, list[str]]:
    return {
        "accessionNumber": [row[0] for row in rows],
        "form": [row[1] for row in rows],
        "filingDate": [row[2] for row in rows],
        "reportDate": [row[3] for row in rows],
        "primaryDocument": [row[4] for row in rows],
    }


def hit(
    accession: str, form: str, filed: str, ciks: list[str], doc: str = "x.htm"
) -> dict[str, Any]:
    return {
        "_id": f"{accession}:{doc}",
        "_source": {"adsh": accession, "form": form, "file_date": filed, "ciks": ciks},
    }


def hits(total: int, *rows: dict[str, Any]) -> dict[str, Any]:
    return {"hits": {"total": {"value": total}, "hits": list(rows)}}


# Names


def test_a_comm_cantor_series_is_also_tried_under_its_edgar_code():
    assert name_variants("COMM 2014-CR18") == ["COMM 2014-CR18", "COMM 2014-CCRE18"]


def test_whitespace_is_normalized_and_lowercase_input_still_renamed():
    assert name_variants("  comm   2014-cr18 ") == ["comm 2014-cr18", "comm 2014-CCRE18"]


def test_a_name_without_a_known_alias_has_one_variant():
    assert name_variants("BANK 2018-BNK13") == ["BANK 2018-BNK13"]
    assert name_variants("COMM 2014-UBS6") == ["COMM 2014-UBS6"]
    assert name_variants("WFRBS 2014-CR18") == ["WFRBS 2014-CR18"]
    assert name_variants("") == [""]


def test_only_the_series_word_is_renamed():
    assert name_variants("COMM CR18 2014-CR18") == ["COMM CR18 2014-CR18", "COMM CR18 2014-CCRE18"]


def test_every_word_of_the_name_must_be_a_whole_word_of_the_entity():
    assert names_match("WFRBS 2014-C25", "WFRBS Commercial Mortgage Trust 2014-C25")
    assert names_match("bank 2018-bnk13", "BANK 2018-BNK13")
    assert not names_match("BANK 2018-BNK1", "BANK 2018-BNK13")
    assert not names_match("BANK 2018-BNK13", "BANK5 2018-BNK13")
    assert not names_match("", "BANK 2018-BNK13")


# Resolving the issuing entity


def test_resolve_takes_the_one_entity_carrying_every_word():
    source = FakeSource(
        {
            search.typeahead_url("WFRBS 2014-C25"): entities(
                ("1623838", "WFRBS Commercial Mortgage Trust 2014-C25"),
                ("1", "WFRBS Commercial Mortgage Trust 2014-C26"),
            )
        }
    )
    assert resolve(source, "WFRBS 2014-C25") == Match(
        cik=1623838, edgar_name="WFRBS Commercial Mortgage Trust 2014-C25", note=None
    )


def test_resolve_notes_when_edgar_uses_another_series_code():
    source = FakeSource(
        {
            search.typeahead_url("COMM 2014-CR18"): no_entities(),
            search.typeahead_url("COMM 2014-CCRE18"): entities(
                ("1610486", "COMM 2014-CCRE18 Mortgage Trust")
            ),
        }
    )
    match = resolve(source, "COMM 2014-CR18")
    assert match.cik == 1610486
    assert match.note == (
        'EDGAR names the trust "COMM 2014-CCRE18" (entity "COMM 2014-CCRE18 Mortgage Trust")'
    )


def test_resolve_refuses_two_entities_with_every_word():
    source = FakeSource(
        {
            search.typeahead_url("BX 2024-VLT5"): entities(
                ("11", "BX 2024-VLT5 Trust"), ("12", "BX 2024-VLT5 Mortgage Trust")
            )
        }
    )
    with pytest.raises(AmbiguousDeal) as refused:
        resolve(source, "BX 2024-VLT5")
    assert str(refused.value) == (
        'several EDGAR entities match "BX 2024-VLT5": '
        "BX 2024-VLT5 Trust (CIK 11), BX 2024-VLT5 Mortgage Trust (CIK 12)"
    )


def test_the_same_entity_listed_twice_is_one_match():
    source = FakeSource(
        {search.typeahead_url("BANK 2018-BNK13"): entities(("7", "BANK 2018-BNK13"), ("7", "x"))}
    )
    assert resolve(source, "BANK 2018-BNK13").cik == 7


def test_resolve_without_a_matching_entity_has_no_cik():
    source = FakeSource({search.typeahead_url("HYT 2024-RGCY"): entities(("9", "HYT Corp"))})
    assert resolve(source, "HYT 2024-RGCY") == Match(cik=None)


# Filings


def test_a_periodic_filing_reports_on_its_distribution_month():
    ten_d = FoundFiling("a", 1, "10-D", dt.date(2026, 9, 29), dt.date(2026, 9, 17))
    assert ten_d.period == Period(2026, 9)
    assert FoundFiling("b", 1, "ABS-EE/A", dt.date(2026, 10, 2), dt.date(2026, 9, 17)).period == (
        Period(2026, 9)
    )
    assert FoundFiling("c", 1, "8-K", dt.date(2026, 9, 29), dt.date(2026, 9, 17)).period is None
    assert FoundFiling("d", 1, "10-D", dt.date(2026, 9, 29)).period is None


def test_blank_report_dates_and_documents_are_not_reported():
    rows = discovery.submission_filings(5, block(("0001-26-1", "424B5", "2014-12-12", "", "")))
    assert rows == [FoundFiling("0001-26-1", 5, "424B5", dt.date(2014, 12, 12), None, None)]


def test_a_missing_column_reads_as_blank():
    columns = {"accessionNumber": ["a"], "form": ["FWP"], "filingDate": ["2014-11-12"]}
    (row,) = submissions.filing_rows(columns)
    assert (row.report_date, row.primary_document) == ("", "")


def test_entity_filings_follow_the_older_pages():
    recent = block(("new", "10-D", "2026-09-29", "2026-09-17", "n.htm"))
    older = block(("old", "10-D", "2016-01-29", "2016-01-15", "o.htm"))
    source = FakeSource(
        {
            submissions.submissions_url(42): {
                "filings": {
                    "recent": recent,
                    "files": [{"name": "CIK0000000042-submissions-001.json"}],
                }
            },
            submissions.page_url("CIK0000000042-submissions-001.json"): older,
        }
    )
    found = discovery.entity_filings(source, 42)
    assert [(f.accession, f.cik, f.period) for f in found] == [
        ("new", 42, Period(2026, 9)),
        ("old", 42, Period(2016, 1)),
    ]
    assert source.asked[1] == (
        "https://data.sec.gov/submissions/CIK0000000042-submissions-001.json"
    )


def test_full_text_hits_keep_only_the_wanted_form_once_per_filing():
    response = hits(
        5,
        hit("acc-1", "ABS-15G", "2024-08-06", ["0000850779"], "exh1.htm"),
        hit("acc-1", "ABS-15G", "2024-08-06", ["0000850779"], "exh2.htm"),
        hit("acc-2", "10-K", "2024-03-01", ["0000000001"]),
        hit("acc-3", "ABS-15G", "", ["0000000002"]),
        hit("acc-4", "ABS-15G", "2024-09-01", []),
    )
    assert full_text_filings(response, ["ABS-15G"]) == [
        FoundFiling("acc-1", 850779, "ABS-15G", dt.date(2024, 8, 6))
    ]


def test_a_hit_without_an_accession_field_takes_it_from_the_id():
    row = {"_id": "acc-9:doc.htm", "_source": {"form": "ABS-15G", "file_date": "2024-08-06"}}
    row["_source"]["ciks"] = ["12"]
    row["_source"]["period_ending"] = "2024-08-05"
    assert full_text_filings(hits(1, row), ["ABS-15G"]) == [
        FoundFiling("acc-9", 12, "ABS-15G", dt.date(2024, 8, 6), dt.date(2024, 8, 5))
    ]


def test_depositor_search_follows_every_page():
    query = '"HYT 2024-RGCY"'
    source = FakeSource(
        {
            search.full_text_url(query, "ABS-15G"): hits(
                3,
                hit("a", "ABS-15G", "2024-08-06", ["1"]),
                hit("b", "ABS-15G", "2024-08-07", ["1"]),
            ),
            search.full_text_url(query, "ABS-15G", 2): hits(
                3, hit("c", "ABS-15G", "2024-08-08", ["1"])
            ),
        }
    )
    found = discovery.depositor_filings(source, "HYT 2024-RGCY")
    assert [filing.accession for filing in found] == ["a", "b", "c"]
    assert source.asked[1].endswith("&from=2")


def test_depositor_search_stops_on_an_empty_page():
    query = '"HYT 2024-RGCY"'
    source = FakeSource({search.full_text_url(query, "ABS-15G"): hits(9)})
    assert discovery.depositor_filings(source, "HYT 2024-RGCY") == []
    assert len(source.asked) == 1


def test_discover_keeps_filings_on_or_before_the_date_newest_first():
    source = FakeSource(
        {
            search.typeahead_url("BANK 2018-BNK13"): entities(("1744707", "BANK 2018-BNK13")),
            submissions.submissions_url(1744707): {
                "filings": {
                    "recent": block(
                        ("late", "10-D", "2026-10-28", "2026-10-19", ""),
                        ("same-day-b", "ABS-EE", "2026-10-27", "2026-10-19", ""),
                        ("same-day-a", "10-D", "2026-10-27", "2026-10-19", ""),
                        ("early", "10-D", "2026-09-29", "2026-09-17", ""),
                    )
                }
            },
        }
    )
    found = discover(source, " BANK  2018-BNK13 ", ON)
    assert found.deal == "BANK 2018-BNK13"
    assert [filing.accession for filing in found.filings] == ["same-day-b", "same-day-a", "early"]
    assert [filing.accession for filing in found.of_form("10-D")] == ["same-day-a", "early"]


def test_a_blank_deal_name_is_refused():
    with pytest.raises(BlankDealName, match="^name the deal to look up$"):
        discover(FakeSource({}), "   ", ON)


# Recorded responses (tests/fixtures/edgar, recorded from the live hosts)


def recorded(name: str) -> Discovery:
    return discover(Replay(RECORDINGS), name, ON)


def test_recorded_conduit_lists_its_september_and_october_filings():
    found = recorded("BANK 2018-BNK13")
    assert found.match == Match(cik=1744707, edgar_name="BANK 2018-BNK13")
    periodic = {
        (f.form, f.accession, f.filing_date)
        for f in found.filings
        if f.period in (Period(2026, 9), Period(2026, 10))
    }
    assert periodic == {
        ("10-D", "0001888524-26-017704", dt.date(2026, 9, 29)),
        ("ABS-EE", "0001888524-26-017705", dt.date(2026, 9, 29)),
        ("10-D", "0001888524-26-019174", dt.date(2026, 10, 27)),
        ("ABS-EE", "0001888524-26-019175", dt.date(2026, 10, 27)),
    }


def test_recorded_pre_2016_deal_has_no_loan_tape():
    found = recorded("WFRBS 2014-C25")
    assert found.match.cik == 1623838
    assert found.of_form("ABS-EE") == []
    assert len(found.of_form("10-D")) == 141


def test_recorded_suspended_deal_resolves_under_its_edgar_name():
    found = recorded("COMM 2014-CR18")
    assert found.match.cik == 1610486
    assert found.match.note is not None
    assert 'EDGAR names the trust "COMM 2014-CCRE18"' in found.match.note
    assert [f.filing_date for f in found.of_form("15-15D")] == [dt.date(2024, 10, 9)]


def test_recorded_private_deal_has_only_the_depositor_abs_15g():
    found = recorded("HYT 2024-RGCY")
    assert found.match == Match(cik=None)
    assert found.filings == (
        FoundFiling(
            "0001539497-24-001576", 850779, "ABS-15G", dt.date(2024, 8, 6), dt.date(2024, 8, 6)
        ),
    )


# Recording and replay


def test_recording_names_are_readable():
    assert recording_name(submissions.submissions_url(1744707)) == (
        "submissions/CIK0001744707.json"
    )
    assert recording_name(search.typeahead_url("COMM 2014-CR18")) == (
        "search/keysTyped_COMM_2014-CR18.json"
    )
    assert recording_name(search.full_text_url('"HYT 2024-RGCY"', "ABS-15G", 100)) == (
        "search/q_HYT_2024-RGCY_forms_ABS-15G_from_100.json"
    )


def test_trim_keeps_only_what_discovery_reads():
    main = {
        "cik": "1",
        "name": "Trust",
        "addresses": {"business": {"street1": "somewhere"}},
        "filings": {
            "recent": {**block(("a", "10-D", "2026-09-29", "2026-09-17", "d.htm")), "size": [1]},
            "files": [{"name": "page-001.json", "filingCount": 3}],
        },
    }
    assert trim(main) == {
        "cik": "1",
        "name": "Trust",
        "filings": {
            "recent": block(("a", "10-D", "2026-09-29", "2026-09-17", "d.htm")),
            "files": [{"name": "page-001.json"}],
        },
    }
    page = {**block(("b", "10-D", "2016-01-29", "", "")), "act": ["34"]}
    assert trim(page) == block(("b", "10-D", "2016-01-29", "", ""))
    response = {
        "took": 3,
        "hits": {
            "total": {"value": 1},
            "hits": [{"_id": "x", "_score": 2.0, "_source": {"form": "ABS-15G", "sics": ["6189"]}}],
        },
    }
    assert trim(response) == {
        "hits": {"total": {"value": 1}, "hits": [{"_id": "x", "_source": {"form": "ABS-15G"}}]}
    }
    assert trim([1, 2]) == [1, 2]


def test_recorder_saves_trimmed_responses_that_replay_gives_back(tmp_path):
    url = search.typeahead_url("BANK 2018-BNK13")
    upstream = FakeSource({url: {"took": 1, **entities(("1744707", "BANK 2018-BNK13"))}})
    with clock.frozen(dt.date(2026, 10, 28)):
        returned = Recorder(tmp_path, upstream).get_json(url)
    assert "took" not in returned
    replayed = Replay(tmp_path)
    assert replayed.recorded_on == "2026-10-28"
    assert replayed.get_json(url) == returned
    manifest = json.loads((tmp_path / "recordings.json").read_text())
    assert manifest["responses"] == {url: "search/keysTyped_BANK_2018-BNK13.json"}
    # A second recorder adds to the manifest rather than replacing it.
    other = search.typeahead_url("WFRBS 2014-C25")
    Recorder(tmp_path, FakeSource({other: entities()})).get_json(other)
    assert set(Replay(tmp_path).responses) == {url, other}


def test_replay_refuses_a_url_never_recorded():
    with pytest.raises(MissingRecording, match="no recording of https://example.test/x"):
        Replay(RECORDINGS).get_json("https://example.test/x")


def test_default_source_replays_when_the_variable_names_a_directory(monkeypatch):
    monkeypatch.setenv(replay.REPLAY_ENV, str(RECORDINGS))
    assert isinstance(replay.default_source(), Replay)
    monkeypatch.setenv(replay.REPLAY_ENV, "")
    assert isinstance(replay.default_source(), replay.Live)


def test_live_source_pauses_after_each_request(monkeypatch):
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(replay.client, "get_json", lambda url: calls.append(("get", url)) or {})
    monkeypatch.setattr(replay.client, "pause", lambda seconds: calls.append(("pause", seconds)))
    assert replay.Live(0.5).get_json("https://data.sec.gov/x") == {}
    assert calls == [("get", "https://data.sec.gov/x"), ("pause", 0.5)]
    assert replay.Live().interval_s == 0.15


def test_entity_is_read_from_the_typeahead_hit():
    assert discovery.typeahead_entities(entities(("1610486", "COMM"))) == [Entity(1610486, "COMM")]
