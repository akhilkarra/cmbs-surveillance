from cmbs.edgar.archives import filing_base_url, is_wanted
from cmbs.edgar.search import summarize_hits
from cmbs.edgar.submissions import recent_filings, submissions_url


def test_summarize_hits_keeps_total_forms_and_rows():
    response = {
        "hits": {
            "total": {"value": 2},
            "hits": [
                {"_id": "a", "_source": {"form": "10-D", "display_names": ["Trust A"]}},
                {"_id": "b", "_source": {"form": "ABS-EE"}},
            ],
        }
    }
    summary = summarize_hits(response)
    assert summary["total"] == 2
    assert summary["forms_seen"] == ["10-D", "ABS-EE"]
    assert summary["hits"][0]["names"] == ["Trust A"]


def test_submissions_url_zero_pads_cik():
    assert submissions_url(1744707) == "https://data.sec.gov/submissions/CIK0001744707.json"


def test_recent_filings_turns_columns_into_rows():
    data = {
        "filings": {
            "recent": {
                "filingDate": ["2026-09-29"],
                "form": ["ABS-EE"],
                "accessionNumber": ["0001888524-26-017705"],
                "reportDate": ["2026-09-17"],
                "primaryDocument": ["exh_102.xml"],
            }
        }
    }
    (filing,) = recent_filings(data)
    assert filing.form == "ABS-EE"
    assert filing.accession == "0001888524-26-017705"


def test_filing_base_url_strips_accession_dashes():
    url = filing_base_url(1744707, "0001888524-26-017705")
    assert url == "https://www.sec.gov/Archives/edgar/data/1744707/000188852426017705/"


def test_only_xml_unless_distribution_report_requested():
    assert is_wanted("exh_102.xml", include_distribution_report=False)
    assert not is_wanted("ex991.htm", include_distribution_report=False)
    assert is_wanted("ex991.htm", include_distribution_report=True)
