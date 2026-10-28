"""EDGAR full-text search (efts.sec.gov): find filings and filer CIKs for a deal."""

import urllib.parse
from collections.abc import Mapping
from typing import Any

from cmbs.edgar import client

EFTS_URL = "https://efts.sec.gov/LATEST/search-index"
REQUEST_INTERVAL_S = 0.25


def full_text_search(query: str, forms: str | None = None) -> dict[str, Any]:
    params = {"q": query}
    if forms:
        params["forms"] = forms
    return client.get_json(f"{EFTS_URL}?{urllib.parse.urlencode(params)}")


def summarize_hits(response: Mapping[str, Any]) -> dict[str, Any]:
    """Reduce an efts response to its total, the forms seen, and one row per hit."""
    hits = []
    for hit in response["hits"]["hits"]:
        source = hit["_source"]
        hits.append(
            {
                "id": hit["_id"],
                "form": source.get("form"),
                "file_date": source.get("file_date"),
                "period": source.get("period_ending"),
                "ciks": source.get("ciks"),
                "names": source.get("display_names"),
                "file_type": source.get("file_type"),
            }
        )
    return {
        "total": response["hits"]["total"]["value"],
        "forms_seen": sorted({h["form"] for h in hits if h["form"]}),
        "hits": hits,
    }


def search_deals(deals: Mapping[str, str]) -> dict[str, dict[str, Any]]:
    """Run one full-text search per deal and summarize each result."""
    results = {}
    for name, query in deals.items():
        results[name] = summarize_hits(full_text_search(query))
        client.pause(REQUEST_INTERVAL_S)
    return results
