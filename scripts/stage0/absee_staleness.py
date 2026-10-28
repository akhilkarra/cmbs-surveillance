"""Parse CMBS ABS-EE EX-102 XML files and measure "most recent financials" staleness.

Usage:
  python3 -I absee_staleness.py FILING_DATE(YYYY-MM-DD) EX102.xml [EX102.xml ...]

Same logic as browser_extract.js (which produced the 2026-09 numbers in
research/11-data-sources.md because www.sec.gov rejected the project UA from curl):
- one row per top-level asset (assetNumber without '-'), skipping pari-passu companion
  notes whose propertyName == 'NA';
- "active" = ending actual balance > 0, not 'Defeased', property type != 'CH' (co-ops
  report no operating statements);
- staleness = reportingPeriodEndDate - mostRecentFinancialsEndDate (days); also vs filing date.
Dates in the XML are MM-DD-YYYY.
"""
import datetime as dt, statistics, sys, xml.etree.ElementTree as ET

NS = "{http://www.sec.gov/edgar/document/absee/cmbs/assetdata}"


def d(s):
    return dt.datetime.strptime(s, "%m-%d-%Y").date() if s else None


def txt(el, tag):
    x = el.find(NS + tag)
    return x.text.strip() if x is not None and x.text else ""


def rows(path):
    root = ET.parse(path).getroot()
    for a in root.findall(NS + "assets"):
        aid = txt(a, "assetNumber")
        if "-" in aid:
            continue
        p = a.find(NS + "property")
        pt = (lambda t: txt(p, t)) if p is not None else (lambda t: "")
        if pt("propertyName") == "NA":
            continue
        yield {
            "id": aid, "rpEnd": d(txt(a, "reportingPeriodEndDate")),
            "bal": float(txt(a, "reportPeriodEndActualBalanceAmount") or 0),
            "paidThrough": txt(a, "paidThroughDate"), "status": txt(a, "paymentStatusLoanCode"),
            "ssTransfer": txt(a, "mostRecentSpecialServicerTransferDate"),
            "msReturn": txt(a, "mostRecentMasterServicerReturnDate"),
            "workout": txt(a, "workoutStrategyCode"), "modCode": txt(a, "modificationCode"),
            "type": pt("propertyTypeCode"), "name": pt("propertyName"),
            "finStart": d(pt("mostRecentFinancialsStartDate")), "finEnd": d(pt("mostRecentFinancialsEndDate")),
            "noi": pt("mostRecentNetOperatingIncomeAmount"),
            "dscr": pt("mostRecentDebtServiceCoverageNetOperatingIncomePercentage"),
            "occ": pt("mostRecentPhysicalOccupancyPercentage"),
        }


def pct(v, q):
    v = sorted(v)
    return v[min(len(v) - 1, int(q * (len(v) - 1) + 0.5))] if v else None


def main(filing_date, paths):
    fd = dt.date.fromisoformat(filing_date)
    pooled = []
    for path in paths:
        rs = list(rows(path))
        act = [r for r in rs if r["bal"] > 0 and r["name"] != "Defeased" and r["type"] != "CH"]
        fin = [r for r in act if r["finEnd"]]
        st = [(r["rpEnd"] - r["finEnd"]).days for r in fin]
        pooled += st
        print(f"{path}: active={len(act)} with_fin={len(fin)} median={pct(st, .5)} p75={pct(st, .75)} "
              f"p90={pct(st, .9)} >183d={sum(x > 183 for x in st)} vs_filing_median="
              f"{pct([(fd - r['finEnd']).days for r in fin], .5)}")
    if pooled:
        print(f"POOLED n={len(pooled)} median={statistics.median(pooled)} p75={pct(pooled, .75)} p90={pct(pooled, .9)}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
