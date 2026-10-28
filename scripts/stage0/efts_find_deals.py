"""Find CMBS issuing-entity CIKs and form types via EDGAR full-text search (efts).

Usage: python3 -I efts_find_deals.py OUT_JSON
Uses only efts.sec.gov (works with the project UA). Rate-limited to <=5 req/s.
"""
import json, os, ssl, sys, time, urllib.parse, urllib.request

CTX = ssl.create_default_context(cafile="/etc/ssl/cert.pem")  # macOS system CA bundle

UA = os.environ.get("SEC_USER_AGENT", "cmbs-llm-research (academic project)")
DEALS = {
    "BANK5 2026-5YR23": '"BANK5 2026-5YR23"',
    "WFRBS 2014-C25": '"WFRBS Commercial Mortgage Trust 2014-C25"',
    "WFRBS 2013-C15": '"WFRBS Commercial Mortgage Trust 2013-C15"',
    "COMM 2014-CR18": '"COMM 2014-CR18"',
    "CGCMT 2016-C1": '"Citigroup Commercial Mortgage Trust 2016-C1"',
    "HYT 2024-RGCY": '"HYT 2024-RGCY"',
    "KSL 2026-HT3": '"KSL 2026-HT3"',
    "BX 2024-VLT5": '"BX 2024-VLT5"',
    "MSC 2017-ASHF": '"2017-ASHF"',
    "SMR 2022-IND": '"SMR 2022-IND"',
    "ILPT 2025-LPF2": '"ILPT 2025-LPF2"',
    "GSMS 2020-DUNE": '"2020-DUNE"',
    "CSMC 2020-FACT": '"2020-FACT"',
    "JW 2024-BERY": '"2024-BERY"',
    "Benchmark 2026-B43": '"Benchmark 2026-B43"',
}


def efts(q, forms=None):
    p = {"q": q}
    if forms:
        p["forms"] = forms
    url = "https://efts.sec.gov/LATEST/search-index?" + urllib.parse.urlencode(p)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
        return json.load(r)


def main(out):
    res = {}
    for name, q in DEALS.items():
        rows = []
        for forms in (None,):
            d = efts(q, forms)
            time.sleep(0.25)
            for h in d["hits"]["hits"]:
                s = h["_source"]
                rows.append({
                    "id": h["_id"], "form": s.get("form"), "file_date": s.get("file_date"),
                    "period": s.get("period_ending"), "ciks": s.get("ciks"),
                    "names": s.get("display_names"), "file_type": s.get("file_type"),
                })
        forms_seen = sorted({r["form"] for r in rows if r["form"]})
        res[name] = {"total": d["hits"]["total"]["value"], "forms_seen": forms_seen, "hits": rows}
        print(f"{name:20s} total={d['hits']['total']['value']:5d} forms={forms_seen}")
        names = sorted({n for r in rows for n in (r['names'] or [])})
        for n in names[:6]:
            print("     ", n)
    json.dump(res, open(out, "w"), indent=1)


if __name__ == "__main__":
    main(sys.argv[1])
